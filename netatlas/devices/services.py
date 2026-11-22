"""Device Types."""

import math
import re
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from ipaddress import ip_address
from itertools import product

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import Http404
from django.utils.translation import gettext as _

from netatlas.accounts.services import require_editor, require_role
from netatlas.addressing.models import IpAddress, Vlan
from netatlas.devices.models import Cable, Device, DeviceType, Interface, Rack
from netatlas.history.services import create_item, delete_item, update_item
from netatlas.system.locks import lock_inventory

INTERFACE_KINDS = ("physical", "virtual", "lag")
MAX_INTERFACES = 1024
MAX_PATTERN_LENGTH = 128

_RANGE = re.compile(r"\[(\d{1,9})-(\d{1,9})\]")
# Control characters are refused too: Postgres cannot store NUL in JSON.
_NAME = re.compile(r"[^\s\x00-\x1f\x7f]{1,64}")
_UPDATABLE_DEVICE_FIELDS = {"name", "site_id", "parent_id", "rack_height"}
_UPDATABLE_INTERFACE_FIELDS = {"name", "kind", "lag_id"}
_UPDATABLE_CABLE_FIELDS = {"label"}
_UPDATABLE_RACK_FIELDS = {"name", "height"}
_INTERFACE_RELATIONS = (
    "interfaces__lag",
    "interfaces__paired_with",
    "interfaces__cable_as_a__interface_b__device",
    "interfaces__cable_as_b__interface_a__device",
    "interfaces__untagged_vlan",
    "interfaces__tagged_vlans",
    "interfaces__ip_addresses",
)


@dataclass(frozen=True)
class DeviceTypeSummary:
    id: int
    make: str
    model_name: str
    rack_height: int
    interfaces: list[dict]


@dataclass(frozen=True)
class VlanRef:
    id: int
    number: int
    name: str


@dataclass(frozen=True)
class IpAddressRef:
    id: int
    address: str


@dataclass(frozen=True)
class InterfaceSummary:
    id: int
    device_id: int
    name: str
    kind: str
    lag_id: int | None
    lag_name: str | None
    cable_id: int | None
    peer_interface_id: int | None
    peer_interface_name: str | None
    peer_device_id: int | None
    peer_device_name: str | None
    paired_interface_id: int | None
    paired_interface_name: str | None
    untagged_vlan: VlanRef | None
    tagged_vlans: list[VlanRef]
    ip_addresses: list[IpAddressRef]


@dataclass(frozen=True)
class InterfaceChoice:
    id: int
    label: str


@dataclass(frozen=True)
class CableSummary:
    id: int
    label: str
    interface_a_id: int
    interface_a_name: str
    device_a_id: int
    device_a_name: str
    interface_b_id: int
    interface_b_name: str
    device_b_id: int
    device_b_name: str


@dataclass(frozen=True)
class TraceHop:
    interface_id: int
    interface_name: str
    device_id: int
    device_name: str
    site_name: str
    is_start: bool
    # The Cable leading to the next hop. None when the next hop is a front/rear pair.
    cable_id: int | None
    cable_label: str | None


@dataclass(frozen=True)
class CableTrace:
    hops: list[TraceHop]
    is_loop: bool


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
    rack_id: int | None
    rack_name: str | None
    rack_unit: int | None
    rack_face: str
    interfaces: list[InterfaceSummary]


@dataclass(frozen=True)
class RackSummary:
    id: int
    name: str
    site_id: int
    site_name: str
    height: int


@dataclass(frozen=True)
class ElevationUnit:
    unit: int
    device_id: int | None
    device_name: str | None
    span: int
    # True for the lower units of a taller Device, which are drawn as part of its top unit.
    is_covered: bool


@dataclass(frozen=True)
class RackedDevice:
    id: int
    name: str


@dataclass(frozen=True)
class RackElevation:
    rack: RackSummary
    front: list[ElevationUnit]
    rear: list[ElevationUnit]
    zero_height: list[RackedDevice]


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
        Device.objects.select_related("site", "parent", "device_type", "rack")
        .prefetch_related(*_INTERFACE_RELATIONS)
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
        elif device.rack_id:
            _check_racked_device_change(device, values)

        if values.get("site_id", device.site_id) != device.site_id:
            moving = [device.pk, *(entry.pk for entry in nested)]
            # VLAN 10 and 10.0.0.1 can mean something else in every Site, so nothing carries over.
            if _uses_vlans(moving):
                message = _(
                    "Remove the VLANs from its Interfaces before moving it to another Site."
                )
                raise _field_error("site_id", message)
            if IpAddress.objects.filter(interface__device_id__in=moving).exists():
                message = _(
                    "Unassign the IP Addresses on its Interfaces before moving it to another Site."
                )
                raise _field_error("site_id", message)

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


def list_interfaces(actor, device_id: int) -> list[InterfaceSummary]:
    require_role(actor)
    return _device_summary(_find_device(device_id)).interfaces


def get_interface(actor, interface_id: int) -> InterfaceSummary:
    require_role(actor)
    return _interface_summary(_find_interface(interface_id))


def create_interface(
    actor, device_id: int, name: str, kind: str, lag_id: int | None = None
) -> InterfaceSummary:
    require_editor(actor)
    with transaction.atomic():
        _lock_device_tree()
        device = _find_device(device_id)
        _check_interface_rules(device, kind, lag_id)
        interface = create_item(
            actor, Interface, device=device, name=name, kind=kind, lag_id=lag_id
        )
    return _interface_summary(interface)


def update_interface(actor, interface_id: int, **values) -> InterfaceSummary:
    """Change an Interface. Its kind is locked while it is part of a LAG relationship."""
    require_editor(actor)
    unknown = set(values) - _UPDATABLE_INTERFACE_FIELDS
    if unknown:
        raise TypeError(f"Cannot update {', '.join(sorted(unknown))} on an Interface")

    with transaction.atomic():
        _lock_device_tree()
        interface = _find_interface(interface_id)
        kind = values.get("kind", interface.kind)
        cable, _peer = _cable_and_peer(interface)
        in_use = interface.lag_id or interface.paired_with_id or cable
        if kind != interface.kind and (in_use or interface.members.exists()):
            message = _("Remove this Interface's LAG, Cable or pair before changing its kind.")
            raise _field_error("kind", message)
        _check_interface_rules(interface.device, kind, values.get("lag_id", interface.lag_id))
        if values.get("lag_id") and (interface.untagged_vlan_id or interface.tagged_vlans.all()):
            message = _("Remove this Interface's VLANs before it joins a LAG.")
            raise _field_error("lag_id", message)
        update_item(actor, interface, **values)
    return _interface_summary(_find_interface(interface_id))


def delete_interface(actor, interface_id: int) -> None:
    """Delete an Interface. The members of a deleted LAG stay, without a LAG."""
    require_editor(actor)
    with transaction.atomic():
        _lock_device_tree()
        delete_item(actor, _find_interface(interface_id))


def list_racks(actor, site_id: int | None = None) -> list[RackSummary]:
    require_role(actor)
    racks = Rack.objects.select_related("site").order_by("site__name", "name", "pk")
    if site_id is not None:
        racks = racks.filter(site_id=site_id)
    return [_rack_summary(rack) for rack in racks]


def get_rack(actor, rack_id: int) -> RackSummary:
    require_role(actor)
    return _rack_summary(_find_rack(rack_id))


def create_rack(actor, name: str, site_id: int, height: int = 42) -> RackSummary:
    require_editor(actor)
    with transaction.atomic():
        _lock_device_tree()
        rack = create_item(actor, Rack, name=name, site_id=site_id, height=height)
    return _rack_summary(_find_rack(rack.pk))


def update_rack(actor, rack_id: int, **values) -> RackSummary:
    """Rename or resize a Rack. It cannot shrink below its highest Device."""
    require_editor(actor)
    unknown = set(values) - _UPDATABLE_RACK_FIELDS
    if unknown:
        raise TypeError(f"Cannot update {', '.join(sorted(unknown))} on a Rack")
    with transaction.atomic():
        _lock_device_tree()
        rack = _find_rack(rack_id)
        if "height" in values:
            placed = rack.devices.filter(rack_unit__isnull=False)
            highest = max((d.rack_unit + d.rack_height - 1 for d in placed), default=0)
            if values["height"] < highest:
                message = _("A Device reaches unit %(unit)d of this Rack.") % {"unit": highest}
                raise _field_error("height", message)
        update_item(actor, rack, **values)
    return _rack_summary(_find_rack(rack_id))


def delete_rack(actor, rack_id: int) -> None:
    """Delete an empty Rack. Devices must be taken out first, so no position is lost."""
    require_editor(actor)
    with transaction.atomic():
        _lock_device_tree()
        rack = _find_rack(rack_id)
        names = sorted(device.name for device in rack.devices.all())
        if names:
            raise ValidationError(
                _("Take these Devices out of the Rack first: %(names)s")
                % {"names": ", ".join(names)}
            )
        delete_item(actor, rack)


def place_device(
    actor, device_id: int, rack_id: int, unit: int | None = None, face: str | None = None
) -> DeviceSummary:
    """Place or move a Physical Device. A zero-height Device is in the Rack without a position."""
    require_editor(actor)
    with transaction.atomic():
        _lock_device_tree()
        device = _find_device(device_id)
        if device.kind != Device.Kind.PHYSICAL:
            raise _field_error("rack_id", _("Only a Physical Device can be placed in a Rack."))
        rack = _reference(Rack, rack_id, "rack_id")
        if rack.site_id != device.site_id:
            raise _field_error("rack_id", _("Choose a Rack in the Device's Site."))

        if device.rack_height == 0:
            if unit is not None or face:
                message = _("A zero-height Device is in the Rack without a position.")
                raise _field_error("unit", message)
        else:
            if unit is None:
                raise _field_error("unit", _("Choose a unit."))
            if face not in Device.Face.values:
                raise _field_error("face", _("Choose the front or rear Face."))
            problem = _position_problem(rack, device.pk, unit, device.rack_height, face)
            if problem:
                raise _field_error("unit", problem)
        update_item(actor, device, rack_id=rack.pk, rack_unit=unit, rack_face=face or "")
    return _device_summary(_find_device(device_id))


def unrack_device(actor, device_id: int) -> DeviceSummary:
    require_editor(actor)
    with transaction.atomic():
        _lock_device_tree()
        update_item(actor, _find_device(device_id), rack_id=None, rack_unit=None, rack_face="")
    return _device_summary(_find_device(device_id))


def rack_elevation(actor, rack_id: int) -> RackElevation:
    """Both Faces of a Rack from the top unit down, plus its zero-height Devices."""
    require_role(actor)
    rack = _find_rack(rack_id)
    devices = list(rack.devices.order_by("name", "pk"))
    return RackElevation(
        rack=_rack_summary(rack),
        front=_elevation_units(rack, devices, Device.Face.FRONT),
        rear=_elevation_units(rack, devices, Device.Face.REAR),
        zero_height=[RackedDevice(id=d.pk, name=d.name) for d in devices if d.rack_unit is None],
    )


def _elevation_units(rack, devices, face: str) -> list[ElevationUnit]:
    occupied = {}
    for device in devices:
        if device.rack_unit is not None and device.rack_face == face:
            top = device.rack_unit + device.rack_height - 1
            for unit in range(device.rack_unit, top + 1):
                occupied[unit] = (device, unit != top)

    units = []
    for unit in range(rack.height, 0, -1):
        device, is_covered = occupied.get(unit, (None, False))
        units.append(
            ElevationUnit(
                unit=unit,
                device_id=device.pk if device else None,
                device_name=device.name if device else None,
                span=device.rack_height if device else 1,
                is_covered=is_covered,
            )
        )
    return units


def _check_racked_device_change(device, values: dict) -> None:
    if values.get("site_id", device.site_id) != device.site_id:
        raise _field_error("site_id", _("Take this Device out of its Rack before moving it."))
    height = values.get("rack_height", device.rack_height)
    if height == device.rack_height:
        return
    if height == 0 or device.rack_height == 0:
        message = _("Take this Device out of its Rack before changing to or from zero height.")
        raise _field_error("rack_height", message)
    problem = _position_problem(
        device.rack, device.pk, device.rack_unit, height, device.rack_face
    )
    if problem:
        raise _field_error("rack_height", problem)


def _position_problem(rack, device_id: int, unit: int, height: int, face: str) -> str | None:
    """Why a Device cannot occupy these units on this Face, or None if it can."""
    top = unit + height - 1
    if unit < 1 or top > rack.height:
        return _("This Device does not fit: the Rack has units 1 to %(height)d.") % {
            "height": rack.height
        }
    others = rack.devices.filter(rack_face=face, rack_unit__lte=top).exclude(pk=device_id)
    names = sorted(d.name for d in others if d.rack_unit + d.rack_height - 1 >= unit)
    if names:
        return _("These units are taken by: %(names)s") % {"names": ", ".join(names)}
    return None


def _find_rack(rack_id: int):
    rack = Rack.objects.select_related("site").filter(pk=rack_id).first()
    if rack is None:
        raise Http404
    return rack


def _rack_summary(rack) -> RackSummary:
    return RackSummary(
        id=rack.pk,
        name=rack.name,
        site_id=rack.site_id,
        site_name=rack.site.name,
        height=rack.height,
    )


def list_cables(actor, device_id: int | None = None) -> list[CableSummary]:
    require_role(actor)
    cables = Cable.objects.select_related("interface_a__device", "interface_b__device")
    if device_id is not None:
        cables = cables.filter(
            Q(interface_a__device_id=device_id) | Q(interface_b__device_id=device_id)
        )
    return [_cable_summary(cable) for cable in cables.order_by("pk")]


def get_cable(actor, cable_id: int) -> CableSummary:
    require_role(actor)
    return _cable_summary(_find_cable(cable_id))


def create_cable(
    actor, interface_a_id: int, interface_b_id: int, label: str = ""
) -> CableSummary:
    """Join two Physical Interfaces. Each can have one Cable, and Sites may differ."""
    require_editor(actor)
    with transaction.atomic():
        _lock_device_tree()
        first = _cable_end(interface_a_id, "interface_a_id")
        second = _cable_end(interface_b_id, "interface_b_id")
        if first.pk == second.pk:
            raise _field_error("interface_b_id", _("A Cable needs two different Interfaces."))
        low, high = sorted((first, second), key=lambda interface: interface.pk)
        cable = create_item(
            actor, Cable, interface_a=low, interface_b=high, label=label.strip()
        )
    return _cable_summary(_find_cable(cable.pk))


def update_cable(actor, cable_id: int, **values) -> CableSummary:
    require_editor(actor)
    unknown = set(values) - _UPDATABLE_CABLE_FIELDS
    if unknown:
        raise TypeError(f"Cannot update {', '.join(sorted(unknown))} on a Cable")
    if "label" in values:
        values["label"] = values["label"].strip()
    with transaction.atomic():
        _lock_device_tree()
        update_item(actor, _find_cable(cable_id), **values)
    return _cable_summary(_find_cable(cable_id))


def delete_cable(actor, cable_id: int) -> None:
    require_editor(actor)
    with transaction.atomic():
        _lock_device_tree()
        delete_item(actor, _find_cable(cable_id))


def pair_interfaces(actor, interface_id: int, partner_id: int) -> InterfaceSummary:
    """Pair two Physical Interfaces of one Device, so a trace can pass through them."""
    require_editor(actor)
    with transaction.atomic():
        _lock_device_tree()
        interface = _find_interface(interface_id)
        partner = _reference(Interface, partner_id, "interface_id")
        if interface.pk == partner.pk or interface.device_id != partner.device_id:
            message = _("Choose another Interface on the same Device.")
            raise _field_error("interface_id", message)
        if Interface.Kind.PHYSICAL != interface.kind or Interface.Kind.PHYSICAL != partner.kind:
            raise _field_error("interface_id", _("Only Physical Interfaces can be paired."))
        if interface.paired_with_id or partner.paired_with_id:
            raise _field_error("interface_id", _("An Interface can only have one pair."))
        update_item(actor, interface, paired_with=partner)
        update_item(actor, partner, paired_with=interface)
    return _interface_summary(_find_interface(interface_id))


def unpair_interface(actor, interface_id: int) -> InterfaceSummary:
    require_editor(actor)
    with transaction.atomic():
        _lock_device_tree()
        interface = _find_interface(interface_id)
        partner = interface.paired_with
        if partner is not None:
            update_item(actor, interface, paired_with=None)
            update_item(actor, partner, paired_with=None)
    return _interface_summary(_find_interface(interface_id))


def trace_interface(actor, interface_id: int) -> CableTrace:
    """The whole path through an Interface, end to end, across Cables and pairs."""
    require_role(actor)
    start = _find_interface(interface_id)
    if start.kind != Interface.Kind.PHYSICAL:
        raise _field_error("interface_id", _("Only Physical Interfaces can be traced."))

    seen = {start.pk}
    ahead, loop_ahead = _walk(start, seen, through_cable=True)
    behind, loop_behind = _walk(start, seen, through_cable=False)
    path = [*reversed(behind), start, *ahead]

    hops = []
    for index, node in enumerate(path):
        following = path[index + 1] if index + 1 < len(path) else None
        cable, peer = _cable_and_peer(node)
        linked = cable is not None and following is not None and peer.pk == following.pk
        hops.append(
            TraceHop(
                interface_id=node.pk,
                interface_name=node.name,
                device_id=node.device_id,
                device_name=node.device.name,
                site_name=node.device.site.name,
                is_start=node.pk == start.pk,
                cable_id=cable.pk if linked else None,
                cable_label=cable.label if linked else None,
            )
        )
    return CableTrace(hops=hops, is_loop=loop_ahead or loop_behind)


def cable_candidates(actor, interface_id: int) -> list[InterfaceChoice]:
    """The Physical Interfaces this one could be cabled to."""
    require_role(actor)
    return _choices(
        Interface.objects.filter(
            kind=Interface.Kind.PHYSICAL, cable_as_a__isnull=True, cable_as_b__isnull=True
        ).exclude(pk=interface_id)
    )


def pair_candidates(actor, interface_id: int) -> list[InterfaceChoice]:
    """The Physical Interfaces of the same Device this one could be paired with."""
    require_role(actor)
    interface = _find_interface(interface_id)
    return _choices(
        Interface.objects.filter(
            device_id=interface.device_id,
            kind=Interface.Kind.PHYSICAL,
            paired_with__isnull=True,
        ).exclude(pk=interface_id)
    )


def _choices(interfaces) -> list[InterfaceChoice]:
    ordered = interfaces.select_related("device").order_by("device__name", "name", "pk")
    return [InterfaceChoice(id=i.pk, label=f"{i.device.name} {i.name}") for i in ordered]


def _walk(start, seen: set, through_cable: bool) -> tuple[list, bool]:
    """Follow alternating Cables and pairs until the path ends or returns to itself."""
    path = []
    current = start
    while True:
        following = _cable_and_peer(current)[1] if through_cable else current.paired_with
        if following is None:
            return path, False
        if following.pk in seen:
            return path, True
        seen.add(following.pk)
        current = _find_interface(following.pk)
        path.append(current)
        through_cable = not through_cable


def _cable_end(interface_id: int, field: str):
    interface = _reference(Interface, interface_id, field)
    if interface.kind != Interface.Kind.PHYSICAL:
        raise _field_error(field, _("Only a Physical Interface can take a Cable."))
    if _cable_and_peer(interface)[0] is not None:
        raise _field_error(field, _("This Interface already has a Cable."))
    return interface


def _cable_and_peer(interface) -> tuple:
    """The Interface's Cable and what is on its other end, or (None, None)."""
    cable = getattr(interface, "cable_as_a", None)
    if cable is not None:
        return cable, cable.interface_b
    cable = getattr(interface, "cable_as_b", None)
    if cable is not None:
        return cable, cable.interface_a
    return None, None


def _find_cable(cable_id: int):
    cable = (
        Cable.objects.select_related("interface_a__device", "interface_b__device")
        .filter(pk=cable_id)
        .first()
    )
    if cable is None:
        raise Http404
    return cable


def _cable_summary(cable) -> CableSummary:
    return CableSummary(
        id=cable.pk,
        label=cable.label,
        interface_a_id=cable.interface_a_id,
        interface_a_name=cable.interface_a.name,
        device_a_id=cable.interface_a.device_id,
        device_a_name=cable.interface_a.device.name,
        interface_b_id=cable.interface_b_id,
        interface_b_name=cable.interface_b.name,
        device_b_id=cable.interface_b.device_id,
        device_b_name=cable.interface_b.device.name,
    )


def _check_interface_rules(device, kind: str, lag_id: int | None) -> None:
    if device.kind == Device.Kind.VIRTUAL and kind != Interface.Kind.VIRTUAL:
        raise _field_error("kind", _("A Virtual Device can only have Virtual Interfaces."))
    if lag_id is None:
        return
    if kind != Interface.Kind.PHYSICAL:
        raise _field_error("lag_id", _("Only a Physical Interface can join a LAG."))
    lag = _reference(Interface, lag_id, "lag_id")
    if lag.kind != Interface.Kind.LAG or lag.device_id != device.pk:
        raise _field_error("lag_id", _("Choose a LAG on the same Device."))


def _find_interface(interface_id: int):
    interface = (
        Interface.objects.select_related(
            "device__site",
            "lag",
            "paired_with",
            "cable_as_a__interface_b__device",
            "cable_as_b__interface_a__device",
            "untagged_vlan",
        )
        .prefetch_related("tagged_vlans", "ip_addresses")
        .filter(pk=interface_id)
        .first()
    )
    if interface is None:
        raise Http404
    return interface


def _interface_summary(interface) -> InterfaceSummary:
    cable, peer = _cable_and_peer(interface)
    paired = interface.paired_with
    return InterfaceSummary(
        id=interface.pk,
        device_id=interface.device_id,
        name=interface.name,
        kind=interface.kind,
        lag_id=interface.lag_id,
        lag_name=interface.lag.name if interface.lag_id else None,
        cable_id=cable.pk if cable else None,
        peer_interface_id=peer.pk if peer else None,
        peer_interface_name=peer.name if peer else None,
        peer_device_id=peer.device_id if peer else None,
        peer_device_name=peer.device.name if peer else None,
        paired_interface_id=paired.pk if paired else None,
        paired_interface_name=paired.name if paired else None,
        untagged_vlan=_vlan_ref(interface.untagged_vlan) if interface.untagged_vlan_id else None,
        tagged_vlans=[
            _vlan_ref(vlan)
            for vlan in sorted(interface.tagged_vlans.all(), key=lambda v: v.number)
        ],
        ip_addresses=[
            IpAddressRef(id=item.pk, address=item.address)
            for item in sorted(interface.ip_addresses.all(), key=_numeric_address)
        ],
    )


def _numeric_address(item) -> tuple[int, int]:
    address = ip_address(item.address)
    return address.version, int(address)


def _vlan_ref(vlan) -> VlanRef:
    return VlanRef(id=vlan.pk, number=vlan.number, name=vlan.name)


def set_interface_vlans(
    actor, interface_id: int, untagged_vlan_id: int | None, tagged_vlan_ids
) -> InterfaceSummary:
    """Replace an Interface's VLANs. All must be in its Device's Site, as one History entry."""
    require_editor(actor)
    tagged_ids = set(tagged_vlan_ids)
    with transaction.atomic():
        _lock_device_tree()
        interface = _find_interface(interface_id)
        wanted = tagged_ids | ({untagged_vlan_id} if untagged_vlan_id is not None else set())
        # The row locks stop a VLAN from being deleted while it is being assigned.
        vlans = {v.pk: v for v in Vlan.objects.select_for_update().filter(pk__in=wanted)}
        site_id = interface.device.site_id
        if untagged_vlan_id is not None:
            _check_interface_vlan(vlans.get(untagged_vlan_id), site_id, "untagged_vlan_id")
        for vlan_id in sorted(tagged_ids):
            _check_interface_vlan(vlans.get(vlan_id), site_id, "tagged_vlan_ids")
        if untagged_vlan_id in tagged_ids:
            message = _("A VLAN cannot be both untagged and tagged on one Interface.")
            raise _field_error("tagged_vlan_ids", message)
        if interface.lag_id and wanted:
            field = "untagged_vlan_id" if untagged_vlan_id is not None else "tagged_vlan_ids"
            raise _field_error(field, _("Set VLANs on the LAG, not on its members."))
        update_item(
            actor,
            interface,
            untagged_vlan_id=untagged_vlan_id,
            many_to_many={"tagged_vlans": sorted(tagged_ids)},
        )
    return _interface_summary(_find_interface(interface_id))


def _check_interface_vlan(vlan, site_id: int, field: str) -> None:
    if vlan is None:
        raise _field_error(field, _("This VLAN does not exist."))
    if vlan.site_id != site_id:
        raise _field_error(field, _("Choose a VLAN in the Device's Site."))


def _uses_vlans(device_ids: list[int]) -> bool:
    return (
        Interface.objects.filter(device_id__in=device_ids)
        .filter(Q(untagged_vlan__isnull=False) | Q(tagged_vlans__isnull=False))
        .exists()
    )


def _lock_device_tree() -> None:
    """Serialize Device writes, so concurrent moves cannot form a cycle or split a tree."""
    lock_inventory()


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
        Device.objects.select_related("site", "parent", "device_type", "rack")
        .prefetch_related(*_INTERFACE_RELATIONS)
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
        rack_id=device.rack_id,
        rack_name=device.rack.name if device.rack_id else None,
        rack_unit=device.rack_unit,
        rack_face=device.rack_face,
        interfaces=[
            _interface_summary(interface)
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
