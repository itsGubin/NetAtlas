"""Which item fields a Live Field can show, and how each value is read."""

import ipaddress
from collections.abc import Callable
from dataclasses import dataclass

from django.utils.translation import gettext_lazy as _


@dataclass(frozen=True)
class LiveFieldDefinition:
    label: object
    read: Callable


def _attr(name):
    return lambda item: getattr(item, name)


def _display(name):
    return lambda item: getattr(item, f"get_{name}_display")()


def _sorted_addresses(addresses) -> list[str]:
    def key(text):
        parsed = ipaddress.ip_address(text)
        return (parsed.version, int(parsed))

    return sorted(set(addresses), key=key)


def _interface_addresses(interface) -> list[str]:
    return _sorted_addresses(ip.address for ip in interface.ip_addresses.all())


def _device_addresses(device) -> list[str]:
    return _sorted_addresses(
        ip.address
        for interface in device.interfaces.all()
        for ip in interface.ip_addresses.all()
    )


def _device_interfaces(device) -> list[str]:
    return sorted((interface.name for interface in device.interfaces.all()), key=str.casefold)


def _rack_position(device):
    if device.rack_unit is None:
        return None
    return f"U{device.rack_unit} {device.get_rack_face_display()}"


def _interface_cable(interface):
    return getattr(interface, "cable_as_a", None) or getattr(interface, "cable_as_b", None)


def _tagged_vlans(interface) -> list[str]:
    vlans = sorted(interface.tagged_vlans.all(), key=lambda vlan: vlan.number)
    return [str(vlan) for vlan in vlans]


def _ip_status(address):
    return _("Assigned") if address.interface_id else _("Reserved")


LIVE_FIELDS = {
    "customer": {"name": LiveFieldDefinition(_("Name"), _attr("name"))},
    "site": {
        "name": LiveFieldDefinition(_("Name"), _attr("name")),
        "customer": LiveFieldDefinition(_("Customer"), _attr("customer")),
    },
    "rack": {
        "name": LiveFieldDefinition(_("Name"), _attr("name")),
        "site": LiveFieldDefinition(_("Site"), _attr("site")),
        "height": LiveFieldDefinition(_("Height"), _attr("height")),
    },
    "device": {
        "name": LiveFieldDefinition(_("Name"), _attr("name")),
        "kind": LiveFieldDefinition(_("Kind"), _display("kind")),
        "site": LiveFieldDefinition(_("Site"), _attr("site")),
        "hypervisor": LiveFieldDefinition(_("Hypervisor"), _attr("parent")),
        "device-type": LiveFieldDefinition(_("Device Type"), _attr("device_type")),
        "rack": LiveFieldDefinition(_("Rack"), _attr("rack")),
        "rack-position": LiveFieldDefinition(_("Rack Position"), _rack_position),
        "rack-height": LiveFieldDefinition(_("Rack height"), _attr("rack_height")),
        "interfaces": LiveFieldDefinition(_("Interfaces"), _device_interfaces),
        "ip-addresses": LiveFieldDefinition(_("IP Addresses"), _device_addresses),
    },
    "interface": {
        "name": LiveFieldDefinition(_("Name"), _attr("name")),
        "device": LiveFieldDefinition(_("Device"), _attr("device")),
        "kind": LiveFieldDefinition(_("Kind"), _display("kind")),
        "lag": LiveFieldDefinition(_("LAG"), _attr("lag")),
        "paired-with": LiveFieldDefinition(_("Paired with"), _attr("paired_with")),
        "cable": LiveFieldDefinition(_("Cable"), _interface_cable),
        "untagged-vlan": LiveFieldDefinition(_("Untagged VLAN"), _attr("untagged_vlan")),
        "tagged-vlans": LiveFieldDefinition(_("Tagged VLANs"), _tagged_vlans),
        "ip-addresses": LiveFieldDefinition(_("IP Addresses"), _interface_addresses),
    },
    "vlan": {
        "number": LiveFieldDefinition(_("Number"), _attr("number")),
        "name": LiveFieldDefinition(_("Name"), _attr("name")),
        "site": LiveFieldDefinition(_("Site"), _attr("site")),
    },
    "subnet": {
        "cidr": LiveFieldDefinition(_("CIDR"), _attr("cidr")),
        "site": LiveFieldDefinition(_("Site"), _attr("site")),
        "vlan": LiveFieldDefinition(_("VLAN"), _attr("vlan")),
    },
    "ip-address": {
        "address": LiveFieldDefinition(_("Address"), _attr("address")),
        "status": LiveFieldDefinition(_("Status"), _ip_status),
        "site": LiveFieldDefinition(_("Site"), _attr("site")),
        "interface": LiveFieldDefinition(_("Interface"), _attr("interface")),
        "description": LiveFieldDefinition(_("Description"), _attr("description")),
    },
    "dhcp-range": {
        "start": LiveFieldDefinition(_("Start"), _attr("start")),
        "end": LiveFieldDefinition(_("End"), _attr("end")),
        "subnet": LiveFieldDefinition(_("Subnet"), _attr("subnet")),
        "description": LiveFieldDefinition(_("Description"), _attr("description")),
    },
    "cable": {
        "label": LiveFieldDefinition(_("Label"), _attr("label")),
        "interface-a": LiveFieldDefinition(_("Interface A"), _attr("interface_a")),
        "interface-b": LiveFieldDefinition(_("Interface B"), _attr("interface_b")),
    },
    "device-type": {
        "make": LiveFieldDefinition(_("Make"), _attr("make")),
        "model": LiveFieldDefinition(_("Model"), _attr("model_name")),
        "rack-height": LiveFieldDefinition(_("Rack height"), _attr("rack_height")),
    },
}


def is_live_field(kind: str, field: str) -> bool:
    return field in LIVE_FIELDS.get(kind, {})


def field_label(kind: str, field: str) -> str:
    return str(LIVE_FIELDS[kind][field].label)


def read_value(item, kind: str, field: str) -> list[str]:
    """The field's current value or values. Empty if it has none."""
    value = LIVE_FIELDS[kind][field].read(item)
    values = value if isinstance(value, list) else [value]
    return [str(entry) for entry in values if entry not in (None, "")]
