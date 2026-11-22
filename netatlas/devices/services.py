"""Device Types."""

import math
import re
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from itertools import product

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.http import Http404
from django.utils.translation import gettext as _

from netatlas.accounts.services import require_editor, require_role
from netatlas.devices.models import Device, DeviceType, Interface
from netatlas.history.services import create_item, delete_item, update_item

INTERFACE_KINDS = ("physical", "virtual", "lag")
MAX_INTERFACES = 1024
MAX_PATTERN_LENGTH = 128

_RANGE = re.compile(r"\[(\d{1,9})-(\d{1,9})\]")
# Control characters are refused too: Postgres cannot store NUL in JSON.
_NAME = re.compile(r"[^\s\x00-\x1f\x7f]{1,64}")
_UPDATABLE_DEVICE_FIELDS = {"name", "site_id", "parent_id", "rack_height"}
# Any fixed number. It identifies this lock among Postgres advisory locks.
_DEVICE_TREE_LOCK = 7_400_801


@dataclass(frozen=True)
class DeviceTypeSummary:
    id: int
    make: str
    model_name: str
    rack_height: int
    interfaces: list[dict]


@dataclass(frozen=True)
class InterfaceSummary:
    id: int
    name: str
    kind: str


@dataclass(frozen=True)
class DeviceSummary:
    id: int
    name: str
    kind: str
    site_id: int
    site_name: str
    parent_id: int | None
    parent_name: str | None
    device_type_id: int | None
    device_type_name: str | None
    rack_height: int
    interfaces: list[InterfaceSummary]


@dataclass(frozen=True)
class VirtualDeviceEntry:
    id: int
    name: str
    parent_id: int
    depth: int


def create_device(
    actor,
    name: str,
    kind: str,
    site_id: int | None = None,
    parent_id: int | None = None,
    device_type_id: int | None = None,
    rack_height: int | None = None,
) -> DeviceSummary:
    """Create a Device, copying Interfaces and height from its Device Type if it has one."""
    require_editor(actor)
    with transaction.atomic():
        _lock_device_tree()
        device_type = None
        if device_type_id is not None:
            device_type = _reference(DeviceType, device_type_id, "device_type_id")

        if kind == Device.Kind.VIRTUAL:
            if parent_id is None:
                raise _field_error("parent_id", _("A Virtual Device needs a Hypervisor."))
            parent = _reference(Device, parent_id, "parent_id")
            site_id = _inherited_site(parent, site_id)
            _require_virtual_interfaces(device_type)
            height = _virtual_height(rack_height)
        else:
            if parent_id is not None:
                raise _field_error("parent_id", _("A Physical Device has no Hypervisor."))
            height = rack_height
            if height is None:
                height = device_type.rack_height if device_type else 1

        device = create_item(
            actor,
            Device,
            name=name,
            kind=kind,
            site_id=site_id,
            parent_id=parent_id,
            device_type=device_type,
            rack_height=height,
        )
        for interface in device_type.interfaces if device_type else ():
            create_item(actor, Interface, device=device, **interface)
    return _device_summary(device)


def get_device(actor, device_id: int) -> DeviceSummary:
    require_role(actor)
    return _device_summary(_find_device(device_id))


def list_devices(
    actor, site_id: int | None = None, kind: str | None = None, parent_id: int | None = None
) -> list[DeviceSummary]:
    require_role(actor)
    devices = (
        Device.objects.select_related("site", "parent", "device_type")
        .prefetch_related("interfaces")
        .order_by("name", "pk")
    )
    if site_id is not None:
        devices = devices.filter(site_id=site_id)
    if kind is not None:
        devices = devices.filter(kind=kind)
    if parent_id is not None:
        devices = devices.filter(parent_id=parent_id)
    return [_device_summary(device) for device in devices]


def virtual_devices(actor, device_id: int) -> list[VirtualDeviceEntry]:
    """Everything running on a Device, depth first, each level sorted by name."""
    require_role(actor)
    device = _find_device(device_id)
    children = defaultdict(list)
    for entry in sorted(_descendants(device), key=lambda d: (d.name, d.pk)):
        children[entry.parent_id].append(entry)

    entries = []
    stack = [(child, 1) for child in reversed(children[device.pk])]
    while stack:
        current, depth = stack.pop()
        entries.append(
            VirtualDeviceEntry(
                id=current.pk, name=current.name, parent_id=current.parent_id, depth=depth
            )
        )
        stack.extend((child, depth + 1) for child in reversed(children[current.pk]))
    return entries


def update_device(actor, device_id: int, **values) -> DeviceSummary:
    """Change a Device. A new Site carries along everything nested on it."""
    require_editor(actor)
    unknown = set(values) - _UPDATABLE_DEVICE_FIELDS
    if unknown:
        raise TypeError(f"Cannot update {', '.join(sorted(unknown))} on a Device")

    with transaction.atomic():
        _lock_device_tree()
        device = _find_device(device_id)
        nested = _descendants(device)

        if device.kind == Device.Kind.VIRTUAL:
            parent = device.parent
            if "parent_id" in values:
                if values["parent_id"] is None:
                    raise _field_error("parent_id", _("A Virtual Device needs a Hypervisor."))
                parent = _reference(Device, values["parent_id"], "parent_id")
                if parent.pk == device.pk or parent.pk in {entry.pk for entry in nested}:
                    message = _("A Device cannot run on itself or on its own Virtual Devices.")
                    raise _field_error("parent_id", message)
            values["site_id"] = _inherited_site(parent, values.get("site_id"))
            if "rack_height" in values:
                values["rack_height"] = _virtual_height(values["rack_height"])
        elif values.get("parent_id") is not None:
            raise _field_error("parent_id", _("A Physical Device has no Hypervisor."))

        updated = update_item(actor, device, **values)
        if updated.site_id != device.site_id:
            for entry in nested:
                update_item(actor, entry, site_id=updated.site_id)
    return _device_summary(_find_device(device_id))


def delete_device(actor, device_id: int) -> None:
    """Delete a Device and its Interfaces. A Hypervisor must be empty first (ADR 0005)."""
    require_editor(actor)
    with transaction.atomic():
        _lock_device_tree()
        device = _find_device(device_id)
        names = sorted(child.name for child in device.children.all())
        if names:
            raise ValidationError(
                _("Move or delete its Virtual Devices first: %(names)s")
                % {"names": ", ".join(names)}
            )
        delete_item(actor, device)


def _lock_device_tree() -> None:
    """Serialize Device writes, so concurrent moves cannot form a cycle or split a tree."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [_DEVICE_TREE_LOCK])


def _descendants(device) -> list:
    found = []
    level = [device.pk]
    while level:
        children = list(Device.objects.filter(parent_id__in=level).order_by("pk"))
        found.extend(children)
        level = [child.pk for child in children]
    return found


def _inherited_site(parent, site_id: int | None) -> int:
    """A Virtual Device is always in its Hypervisor's Site."""
    if site_id is not None and site_id != parent.site_id:
        raise _field_error("site_id", _("A Virtual Device is in its Hypervisor's Site."))
    return parent.site_id


def _require_virtual_interfaces(device_type) -> None:
    if device_type and any(i["kind"] != Interface.Kind.VIRTUAL for i in device_type.interfaces):
        raise _field_error(
            "device_type_id", _("A Virtual Device can only use a type with Virtual Interfaces.")
        )


def _virtual_height(rack_height: int | None) -> int:
    if rack_height:
        raise _field_error("rack_height", _("A Virtual Device takes no rack space."))
    return 0


def _reference(model, item_id: int, field: str):
    item = model.objects.filter(pk=item_id).first()
    if item is None:
        raise _field_error(field, _("This item does not exist."))
    return item


def _field_error(field: str, message: str) -> ValidationError:
    return ValidationError({field: [message]})


def _find_device(device_id: int):
    device = (
        Device.objects.select_related("site", "parent", "device_type")
        .prefetch_related("interfaces")
        .filter(pk=device_id)
        .first()
    )
    if device is None:
        raise Http404
    return device


def _device_summary(device) -> DeviceSummary:
    return DeviceSummary(
        id=device.pk,
        name=device.name,
        kind=device.kind,
        site_id=device.site_id,
        site_name=device.site.name,
        parent_id=device.parent_id,
        parent_name=device.parent.name if device.parent else None,
        device_type_id=device.device_type_id,
        device_type_name=str(device.device_type) if device.device_type else None,
        rack_height=device.rack_height,
        interfaces=[
            InterfaceSummary(id=interface.pk, name=interface.name, kind=interface.kind)
            for interface in sorted(device.interfaces.all(), key=lambda i: i.pk)
        ],
    )


def create_device_type(
    actor, make: str, model_name: str, rack_height: int = 1, interfaces=()
) -> DeviceTypeSummary:
    # Check the Role first, so a Viewer cannot make the server expand a pattern.
    require_editor(actor)
    expanded = _expand(interfaces)
    with _unique_make_and_model():
        device_type = create_item(
            actor,
            DeviceType,
            make=make,
            model_name=model_name,
            rack_height=rack_height,
            interfaces=expanded,
        )
    return _summary(device_type)


def list_device_types(actor) -> list[DeviceTypeSummary]:
    require_role(actor)
    return [_summary(item) for item in DeviceType.objects.order_by("make", "model_name", "pk")]


def get_device_type(actor, device_type_id: int) -> DeviceTypeSummary:
    require_role(actor)
    return _summary(_find(device_type_id))


def update_device_type(actor, device_type_id: int, **values) -> DeviceTypeSummary:
    """Change a Device Type. Devices already created from it keep their copy."""
    require_editor(actor)
    device_type = _find(device_type_id)
    if "interfaces" in values:
        values["interfaces"] = _expand(values["interfaces"])
    with _unique_make_and_model():
        return _summary(update_item(actor, device_type, **values))


def delete_device_type(actor, device_type_id: int) -> None:
    require_editor(actor)
    delete_item(actor, _find(device_type_id))


def parse_interface_lines(text: str) -> list[dict]:
    """Read one `name [kind]` per line. The kind defaults to physical."""
    interfaces = []
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        if len(parts) > 2:
            raise _invalid(_("Write one Interface per line, as: name kind."))
        interfaces.append({"name": parts[0], "kind": parts[1] if len(parts) == 2 else "physical"})
    return interfaces


def format_interface_lines(interfaces: list[dict]) -> str:
    return "\n".join(f"{interface['name']} {interface['kind']}" for interface in interfaces)


def _find(device_type_id: int):
    device_type = DeviceType.objects.filter(pk=device_type_id).first()
    if device_type is None:
        raise Http404
    return device_type


@contextmanager
def _unique_make_and_model():
    try:
        yield
    except IntegrityError as error:
        # Two requests raced for the same make and model after both passed validation.
        message = _("A Device Type with this make and model already exists.")
        raise ValidationError({"model_name": [message]}) from error


def _expand(interfaces) -> list[dict]:
    """The Interface list with every bulk pattern such as `port[1-48]` written out."""
    if not isinstance(interfaces, (list, tuple)):
        raise _invalid(_("Must be a list of Interfaces."))

    expanded = []
    seen = set()
    for entry in interfaces:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            raise _invalid(_("Each Interface needs a name and a kind."))
        if entry.get("kind") not in INTERFACE_KINDS:
            raise _invalid(_("Unknown Interface kind."))
        for name in _expand_name(entry["name"], MAX_INTERFACES - len(expanded)):
            if name in seen:
                raise _invalid(_("Interface %(name)s is defined twice.") % {"name": name})
            seen.add(name)
            expanded.append({"name": name, "kind": entry["kind"]})
    return expanded


def _expand_name(pattern: str, limit: int) -> list[str]:
    if len(pattern) > MAX_PATTERN_LENGTH:
        raise _invalid(_("An Interface name is too long."))

    matches = list(_RANGE.finditer(pattern))
    literals = _RANGE.split(pattern)[::3]
    if any("[" in literal or "]" in literal for literal in literals):
        raise _invalid(_("Write a range as [1-48]."))
    if any(int(match[1]) > int(match[2]) for match in matches):
        raise _invalid(_("A range must not run backwards."))
    # Count before building anything, so a huge pattern costs nothing.
    if math.prod(int(match[2]) - int(match[1]) + 1 for match in matches) > limit:
        raise _invalid(
            _("A Device Type can have at most %(limit)d Interfaces.") % {"limit": MAX_INTERFACES}
        )

    names = [
        "".join(part for pair in zip(literals, [*numbers, ""]) for part in pair)
        for numbers in product(*(_range_values(match) for match in matches))
    ]
    if not all(_NAME.fullmatch(name) for name in names):
        raise _invalid(
            _("An Interface name needs 1 to 64 characters, without spaces or control characters.")
        )
    return names


def _range_values(match) -> list[str]:
    """The numbers of a range. A leading zero, as in `[01-48]`, pads them all to that width."""
    start = match[1]
    width = len(start) if len(start) > 1 and start.startswith("0") else 0
    return [str(number).zfill(width) for number in range(int(start), int(match[2]) + 1)]


def _invalid(message: str) -> ValidationError:
    return ValidationError({"interfaces": [message]})


def _summary(item) -> DeviceTypeSummary:
    return DeviceTypeSummary(
        id=item.pk,
        make=item.make,
        model_name=item.model_name,
        rack_height=item.rack_height,
        interfaces=item.interfaces,
    )
