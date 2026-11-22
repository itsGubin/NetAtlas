"""Device Types."""

import math
import re
from contextlib import contextmanager
from dataclasses import dataclass
from itertools import product

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.http import Http404
from django.utils.translation import gettext as _

from netatlas.accounts.services import require_editor, require_role
from netatlas.devices.models import DeviceType
from netatlas.history.services import create_item, delete_item, update_item

INTERFACE_KINDS = ("physical", "virtual", "lag")
MAX_INTERFACES = 1024
MAX_PATTERN_LENGTH = 128

_RANGE = re.compile(r"\[(\d{1,9})-(\d{1,9})\]")
# Control characters are refused too: Postgres cannot store NUL in JSON.
_NAME = re.compile(r"[^\s\x00-\x1f\x7f]{1,64}")


@dataclass(frozen=True)
class DeviceTypeSummary:
    id: int
    make: str
    model_name: str
    rack_height: int
    interfaces: list[dict]


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
