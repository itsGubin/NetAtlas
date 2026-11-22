"""CSV import of Devices, Interfaces, Subnets, IP Addresses and VLANs.

Every row goes through the same service a person would use, so the same rules apply and each
change is in History. A preview runs the real import and rolls it back, so it cannot disagree
with what the import does. An import writes every row or none. It only creates items.
"""

import csv
import io
import re
from collections.abc import Callable
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from netatlas.accounts.services import require_editor
from netatlas.addressing.models import Vlan
from netatlas.addressing.services import create_ip_address, create_subnet, create_vlan
from netatlas.devices.models import Device, DeviceType, Interface
from netatlas.devices.services import create_device, create_interface
from netatlas.history.services import rehearse
from netatlas.places.models import Site
from netatlas.system.locks import lock_inventory

MAX_CSV_BYTES = 1024 * 1024
MAX_ROWS = 1000
SAMPLE_ROWS = 3


@dataclass(frozen=True)
class ImportField:
    name: str
    label: object
    required: bool
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class CsvColumns:
    """What an uploaded file holds, with a first guess at which column is which field."""

    kind: str
    headers: list[str]
    row_count: int
    sample: list[list[str]]
    mapping: dict[str, str]


@dataclass(frozen=True)
class ImportRow:
    # As a spreadsheet numbers it: the header is row 1.
    number: int
    values: dict[str, str]
    label: str
    errors: list[str]


@dataclass(frozen=True)
class ImportPreview:
    kind: str
    total: int
    valid: int
    rows: list[ImportRow]


class ImportFailed(Exception):
    """At least one row is invalid, so nothing was written."""

    def __init__(self, preview: ImportPreview):
        super().__init__("The import has invalid rows.")
        self.preview = preview


class _Lookups:
    """Finds what rows refer to by name. Sees items created by earlier rows."""

    def __init__(self):
        self.sites = {}
        for site in Site.objects.all():
            self.sites.setdefault(site.name.strip().casefold(), []).append(site.pk)
        self.device_types = {}
        for device_type in DeviceType.objects.all():
            key = str(device_type).strip().casefold()
            self.device_types.setdefault(key, []).append(device_type.pk)

    def site(self, name: str) -> int:
        found = self.sites.get(name.casefold(), [])
        if not found:
            raise ValidationError(_("No Site is named %(name)s.") % {"name": name})
        if len(found) > 1:
            raise ValidationError(_("Several Sites are named %(name)s.") % {"name": name})
        return found[0]

    def device_type(self, name: str) -> int:
        found = self.device_types.get(name.casefold(), [])
        if len(found) != 1:
            raise ValidationError(_("No single Device Type is named %(name)s.") % {"name": name})
        return found[0]

    def device(self, site_id: int, name: str) -> int:
        device = Device.objects.filter(site_id=site_id, name=name).first()
        if device is None:
            raise ValidationError(_("No Device is named %(name)s in this Site.") % {"name": name})
        return device.pk

    def interface(self, device_id: int, name: str) -> int:
        interface = Interface.objects.filter(device_id=device_id, name=name).first()
        if interface is None:
            message = _("This Device has no Interface named %(name)s.") % {"name": name}
            raise ValidationError(message)
        return interface.pk

    def vlan(self, site_id: int, number: str) -> int:
        vlan = Vlan.objects.filter(site_id=site_id, number=_number(number, _("VLAN"))).first()
        if vlan is None:
            raise ValidationError(_("No VLAN %(number)s in this Site.") % {"number": number})
        return vlan.pk


def _number(text: str, label) -> int:
    if not (text.isascii() and text.isdecimal() and len(text) <= 9):
        raise ValidationError(_("%(field)s must be a whole number.") % {"field": label})
    return int(text)


def _choice(text: str, allowed, label) -> str:
    value = text.casefold()
    if value not in allowed:
        message = _("%(field)s must be one of: %(choices)s") % {
            "field": label,
            "choices": ", ".join(allowed),
        }
        raise ValidationError(message)
    return value


def _import_device(actor, found: _Lookups, row: dict) -> str:
    site_id = found.site(row["site"])
    parent_id = found.device(site_id, row["hypervisor"]) if row["hypervisor"] else None
    default = Device.Kind.VIRTUAL if parent_id else Device.Kind.PHYSICAL
    kind = _choice(row["kind"], Device.Kind.values, _("Kind")) if row["kind"] else default
    device_type_id = found.device_type(row["device_type"]) if row["device_type"] else None
    height = _number(row["rack_height"], _("Rack height")) if row["rack_height"] else None
    return create_device(
        actor,
        name=row["name"],
        kind=kind,
        site_id=site_id,
        parent_id=parent_id,
        device_type_id=device_type_id,
        rack_height=height,
    ).name


def _import_interface(actor, found: _Lookups, row: dict) -> str:
    device_id = found.device(found.site(row["site"]), row["device"])
    kind = _choice(row["kind"], Interface.Kind.values, _("Kind")) if row["kind"] else "physical"
    lag_id = found.interface(device_id, row["lag"]) if row["lag"] else None
    interface = create_interface(actor, device_id, name=row["name"], kind=kind, lag_id=lag_id)
    return f"{row['device']} {interface.name}"


def _import_subnet(actor, found: _Lookups, row: dict) -> str:
    site_id = found.site(row["site"])
    vlan_id = found.vlan(site_id, row["vlan"]) if row["vlan"] else None
    return create_subnet(actor, cidr=row["cidr"], site_id=site_id, vlan_id=vlan_id).cidr


def _import_address(actor, found: _Lookups, row: dict) -> str:
    site_id = found.site(row["site"])
    if bool(row["device"]) != bool(row["interface"]):
        raise ValidationError(_("Give both a Device and an Interface, or neither."))
    interface_id = None
    if row["device"]:
        interface_id = found.interface(found.device(site_id, row["device"]), row["interface"])
    return create_ip_address(
        actor,
        address=row["address"],
        site_id=site_id,
        interface_id=interface_id,
        description=row["description"],
    ).address


def _import_vlan(actor, found: _Lookups, row: dict) -> str:
    number = _number(row["number"], _("Number"))
    vlan = create_vlan(actor, number=number, site_id=found.site(row["site"]), name=row["name"])
    return f"VLAN {vlan.number} {vlan.name}".strip()


def _parents_first(rows: list[tuple[int, dict]]) -> list[tuple[int, dict]]:
    """A Hypervisor listed below its Virtual Devices is still created before them."""
    by_name = {(row["site"].casefold(), row["name"]): row for _number, row in rows}

    def depth(row) -> int:
        seen = {id(row)}
        steps = 0
        while row["hypervisor"]:
            row = by_name.get((row["site"].casefold(), row["hypervisor"]))
            if row is None or id(row) in seen:
                break
            seen.add(id(row))
            steps += 1
        return steps

    return sorted(rows, key=lambda entry: depth(entry[1]))


def _lags_first(rows: list[tuple[int, dict]]) -> list[tuple[int, dict]]:
    """A LAG listed below its members is still created before them."""
    return sorted(rows, key=lambda entry: bool(entry[1]["lag"]))


@dataclass(frozen=True)
class _Kind:
    fields: tuple[ImportField, ...]
    create: Callable
    order: Callable = staticmethod(lambda rows: rows)


_SITE = ImportField("site", gettext_lazy("Site"), True, ("sitename", "location", "standort"))
_DEVICE_ALIASES = ("devicename", "hostname", "host", "geraet", "gerät")
_DESCRIPTION = ("description", "desc", "comment", "notes", "beschreibung")

_KINDS = {
    "device": _Kind(
        (
            ImportField("name", gettext_lazy("Name"), True, ("device", *_DEVICE_ALIASES)),
            _SITE,
            ImportField("kind", gettext_lazy("Kind"), False, ("type",)),
            ImportField("hypervisor", gettext_lazy("Hypervisor"), False, ("parent", "host")),
            ImportField(
                "device_type", gettext_lazy("Device Type"), False, ("devicetype", "model")
            ),
            ImportField("rack_height", gettext_lazy("Rack height"), False, ("rackheight", "u")),
        ),
        _import_device,
        _parents_first,
    ),
    "interface": _Kind(
        (
            ImportField("device", gettext_lazy("Device"), True, _DEVICE_ALIASES),
            _SITE,
            ImportField("name", gettext_lazy("Name"), True, ("interface", "port", "ifname")),
            ImportField("kind", gettext_lazy("Kind"), False, ("type",)),
            ImportField("lag", gettext_lazy("LAG"), False, ("portchannel", "bond")),
        ),
        _import_interface,
        _lags_first,
    ),
    "subnet": _Kind(
        (
            ImportField("cidr", gettext_lazy("CIDR"), True, ("subnet", "network", "prefix")),
            _SITE,
            ImportField("vlan", gettext_lazy("VLAN"), False, ("vlanid", "vlannumber")),
        ),
        _import_subnet,
    ),
    "ip-address": _Kind(
        (
            ImportField(
                "address", gettext_lazy("Address"), True, ("ip", "ipaddress", "ipv4", "ipv6")
            ),
            _SITE,
            ImportField("device", gettext_lazy("Device"), False, _DEVICE_ALIASES),
            ImportField("interface", gettext_lazy("Interface"), False, ("port", "ifname")),
            ImportField("description", gettext_lazy("Description"), False, _DESCRIPTION),
        ),
        _import_address,
    ),
    "vlan": _Kind(
        (
            ImportField("number", gettext_lazy("Number"), True, ("vlan", "vlanid", "id", "vid")),
            _SITE,
            ImportField("name", gettext_lazy("Name"), False, ("vlanname",)),
        ),
        _import_vlan,
    ),
}
IMPORT_KINDS = tuple(_KINDS)


def import_fields(kind: str) -> tuple[ImportField, ...]:
    return _spec(kind).fields


def read_upload(upload) -> str:
    """The uploaded file as text. Excel often saves Windows-1252 rather than UTF-8."""
    if upload is None:
        raise ValidationError({"file": [_("Choose a CSV file.")]})
    if upload.size > MAX_CSV_BYTES:
        raise ValidationError({"file": [_too_large()]})
    data = upload.read()
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def inspect_csv(actor, kind: str, text: str) -> CsvColumns:
    require_editor(actor)
    spec = _spec(kind)
    headers, records = _parse(text)
    return CsvColumns(
        kind=kind,
        headers=headers,
        row_count=len(records),
        sample=[cells for _number, cells in records[:SAMPLE_ROWS]],
        mapping=_guess(spec, headers),
    )


def preview_import(actor, kind: str, text: str, mapping=None) -> ImportPreview:
    """What the import would do, row by row. Nothing is kept."""
    require_editor(actor)
    spec = _spec(kind)
    rows = _rows(spec, text, mapping)
    with rehearse():
        return _apply(actor, kind, spec, rows)


def run_import(actor, kind: str, text: str, mapping=None) -> ImportPreview:
    """Create every row, or nothing if any row is invalid."""
    require_editor(actor)
    spec = _spec(kind)
    rows = _rows(spec, text, mapping)
    with transaction.atomic():
        result = _apply(actor, kind, spec, rows)
        if result.valid != result.total:
            transaction.set_rollback(True)
    if result.valid != result.total:
        raise ImportFailed(result)
    return result


def _spec(kind: str) -> _Kind:
    if kind not in _KINDS:
        raise ValidationError({"kind": [_("Choose what to import.")]})
    return _KINDS[kind]


def _too_large() -> str:
    return _("A CSV file has at most %(limit)d MB.") % {"limit": MAX_CSV_BYTES // (1024 * 1024)}


def _parse(text) -> tuple[list[str], list[tuple[int, list[str]]]]:
    """The header and each non-empty record with its row number."""
    if not isinstance(text, str) or not text.strip():
        raise ValidationError({"file": [_("This CSV file is empty.")]})
    if len(text.encode("utf-8", errors="replace")) > MAX_CSV_BYTES:
        raise ValidationError({"file": [_too_large()]})
    if "\x00" in text:
        raise ValidationError({"file": [_("This is not a CSV file.")]})

    text = text.removeprefix("﻿").lstrip("\r\n")
    first_line = text.splitlines()[0]
    delimiter = max(",;\t", key=first_line.count)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
    try:
        table = list(reader)
    except csv.Error as error:
        message = _("This CSV file cannot be read: %(reason)s") % {"reason": error}
        raise ValidationError({"file": [message]}) from error

    headers = [cell.strip() for cell in table[0]]
    if any(not header for header in headers):
        raise ValidationError({"file": [_("Every column needs a name in the first row.")]})
    if len(set(headers)) != len(headers):
        raise ValidationError({"file": [_("Each column needs its own name.")]})
    records = [
        (number, [cell.strip() for cell in cells])
        for number, cells in enumerate(table[1:], start=2)
        if any(cell.strip() for cell in cells)
    ]
    if not records:
        raise ValidationError({"file": [_("This CSV file has no rows to import.")]})
    if len(records) > MAX_ROWS:
        message = _("A CSV file has at most %(limit)d rows.") % {"limit": MAX_ROWS}
        raise ValidationError({"file": [message]})
    return headers, records


def _key(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.casefold())


def _guess(spec: _Kind, headers: list[str]) -> dict[str, str]:
    """Each field's likeliest column. A field's own name beats another field's alias."""
    keys = {_key(header): header for header in headers}
    mapping = {}
    for field in spec.fields:
        if _key(field.name) in keys:
            mapping[field.name] = keys[_key(field.name)]
    taken = set(mapping.values())
    for field in spec.fields:
        if field.name in mapping:
            continue
        for alias in field.aliases:
            header = keys.get(_key(alias))
            if header and header not in taken:
                mapping[field.name] = header
                taken.add(header)
                break
    return mapping


def _clean_mapping(spec: _Kind, headers: list[str], mapping) -> dict[str, str]:
    if mapping is None:
        mapping = _guess(spec, headers)
    mapping = {field: header for field, header in mapping.items() if header}
    names = {field.name for field in spec.fields}
    errors = {}
    for field, header in mapping.items():
        if field not in names:
            errors[field] = [_("Unknown field.")]
        elif header not in headers:
            errors[field] = [_("This file has no column %(name)s.") % {"name": header}]
    for field in spec.fields:
        if field.required and field.name not in mapping:
            errors[field.name] = [_("Choose a column for %(field)s.") % {"field": field.label}]
    used = list(mapping.values())
    for field, header in mapping.items():
        if used.count(header) > 1:
            errors.setdefault(field, []).append(_("Each column can be used for one field only."))
    if errors:
        raise ValidationError(errors)
    return mapping


def _rows(spec: _Kind, text, mapping) -> list[tuple[int, dict, list[str]]]:
    headers, records = _parse(text)
    chosen = _clean_mapping(spec, headers, mapping)
    position = {field: headers.index(header) for field, header in chosen.items()}
    rows = []
    for number, cells in records:
        values = {
            field.name: (
                cells[position[field.name]]
                if field.name in position and position[field.name] < len(cells)
                else ""
            )
            for field in spec.fields
        }
        errors = []
        if len(cells) > len(headers):
            errors.append(_("This row has more cells than the file has columns."))
        for field in spec.fields:
            if field.required and not values[field.name]:
                errors.append(_("%(field)s is required.") % {"field": field.label})
        rows.append((number, values, errors))
    return rows


def _apply(actor, kind, spec, rows) -> ImportPreview:
    # Held to the end of the transaction, so nothing can change between two rows.
    with transaction.atomic():
        lock_inventory()
        found = _Lookups()
        early = {number: errors for number, _values, errors in rows if errors}
        ordered = spec.order([(number, values) for number, values, errors in rows if not errors])
        outcomes = {}
        for number, values in ordered:
            try:
                # A savepoint, so a refused row leaves no trace and the next one can run.
                with transaction.atomic():
                    outcomes[number] = (spec.create(actor, found, values), [])
            except ValidationError as error:
                outcomes[number] = ("", error.messages)
            except Http404:
                outcomes[number] = ("", [_("Something this row refers to does not exist.")])

    results = [
        ImportRow(
            number=number,
            values=values,
            label=outcomes.get(number, ("", []))[0],
            errors=early.get(number) or outcomes[number][1],
        )
        for number, values, _errors in rows
    ]
    return ImportPreview(
        kind=kind,
        total=len(results),
        valid=sum(1 for row in results if not row.errors),
        rows=results,
    )
