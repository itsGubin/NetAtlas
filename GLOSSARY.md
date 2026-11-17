# NetAtlas

A self-hosted tool for documenting a network: its devices, cabling, VLANs and IP address space, together with the written documentation that explains it.

## Language

### Places

**Customer**:
An optional grouping of Sites belonging to one client organisation, used by MSPs.
_Avoid_: Tenant, client, organisation, workspace

**Site**:
A physical place where Devices live, such as a home, office or datacenter; every Device belongs to exactly one Site.
_Avoid_: Location, building, region

**Rack**:
A rack of mounting units within a Site, in which a Physical Device may optionally be placed.
_Avoid_: Cabinet, enclosure

**Rack Position**:
Where a racked Device sits: its lowest unit and which Face it is mounted on; a zero-height Device (such as a vertical PDU) is in the Rack without one.
_Avoid_: Slot, location

**Face**:
The side of a Rack a Device is mounted on, either front or rear.
_Avoid_: Side, orientation

### Devices

**Device**:
Anything documented that has Interfaces or takes rack space, whether real hardware or virtual; it may or may not have IP Addresses.
_Avoid_: Host, node, asset, endpoint

**Physical Device**:
A Device that is real hardware; it can be placed in a Rack and connected by Cables.
_Avoid_: Bare metal, appliance

**Virtual Device**:
A Device that runs on a Hypervisor rather than being hardware of its own, such as a VM or container; it has Interfaces but never Cables.
_Avoid_: VM (as a separate concept), guest

**Hypervisor**:
The role of any Device, physical or virtual, that is the parent of one or more Virtual Devices.
_Avoid_: Host, virtualization server, Docker host

**Device Type**:
A reusable template (make and model) describing a Device's Interfaces and rack height, copied when a Device is created from it.
_Avoid_: Model, template, product

### Connectivity

**Interface**:
A network connection point belonging to exactly one Device; it is either a Physical Interface, a Virtual Interface or a LAG.
_Avoid_: Port, NIC

**Physical Interface**:
An Interface that exists in hardware and can take a Cable.
_Avoid_: Port, jack

**Virtual Interface**:
An Interface with no hardware of its own, such as a VLAN interface or loopback; it never takes a Cable.
_Avoid_: SVI, logical interface

**LAG**:
An Interface that bundles several Physical Interfaces of the same Device into one.
_Avoid_: Bond, port-channel, trunk, team

**Cable**:
A physical link joining exactly two Physical Interfaces; a path through a patch panel is a separate Cable on each side.
_Avoid_: Link, connection, patch

### Addressing

**Subnet**:
A block of IP address space, written in CIDR notation, belonging to exactly one Site and unique within it; a Subnet may contain smaller Subnets.
_Avoid_: Prefix, network, range

**VLAN**:
A numbered layer-2 broadcast domain belonging to exactly one Site; a Subnet may be bound to one VLAN.
_Avoid_: Network, segment

**Untagged VLAN**:
The single VLAN an Interface carries without 802.1Q tags.
_Avoid_: Native VLAN, access VLAN, PVID

**Tagged VLAN**:
A VLAN an Interface carries with 802.1Q tags; an Interface may carry many.
_Avoid_: Trunk VLAN, allowed VLAN

**IP Address**:
A single address within exactly one Subnet; it is either Assigned to an Interface or Reserved.
_Avoid_: IP, host address

**Assigned**:
The state of an IP Address that is configured on an Interface.
_Avoid_: Used, active, allocated

**Reserved**:
The state of an IP Address that is held for a purpose but not on any Interface.
_Avoid_: Planned, blocked

**DHCP Range**:
A block of addresses within a Subnet handed out dynamically and not documented individually as IP Addresses.
_Avoid_: Pool, scope, dynamic range

### Documentation

**Page**:
A piece of documentation; it is either an Item Page or a Standalone Page.
_Avoid_: Article, wiki entry, document, side

**Item Page**:
The one Page that every documented item (Customer, Site, Rack, Device, VLAN, Subnet) has automatically.
_Avoid_: Note, comment, description

**Standalone Page**:
A Page not belonging to any item, such as "How the guest Wi-Fi works", that can mention and embed items.
_Avoid_: Wiki page, article

**Page Template**:
A skeleton of headings, Live Fields and Free-text Sections that shapes Item Pages; editing it updates every Page using it. Each kind of item has its own default, and each Device Type can override the default for Devices.
_Avoid_: Layout, blueprint

**Free-text Section**:
A named part of a Page Template whose content is written per Page and kept when the template changes, unless the section itself is removed.
_Avoid_: Slot, block, custom field

**Live Field**:
A placeholder in a Page that always shows a current value of a documented item, such as a Device's or an Interface's IP Addresses; it follows the item through renames and shows as broken if the item is deleted.
_Avoid_: Tag, variable, placeholder, token

**Mention**:
A link in a Page to a documented item; the item's Page lists every Page that mentions it.
_Avoid_: Reference, backlink, wikilink

### Organisation

**Tag**:
A free label attached to documented items, such as "prod" or "dev".
_Avoid_: Label, category

**Role**:
What a user may do across NetAtlas: Admin, Editor or Viewer.
_Avoid_: Permission level, group

**History**:
The record of every change to a documented item or Page: what changed, from what to what, by whom and when.
_Avoid_: Audit log, changelog, revisions

### Discovery

**Discovery**:
Automatically observing the live network to find differences from what is documented.
_Avoid_: Scan, sync, auto-import

**Suggestion**:
A proposed change produced by Discovery that a person accepts or rejects; it never changes documented data on its own.
_Avoid_: Finding, diff, alert
