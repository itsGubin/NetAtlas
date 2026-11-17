# Subnets and VLANs are scoped per Site

Every Subnet and VLAN belongs to exactly one Site and is unique only within it, so two Sites (for example two MSP Customers) can both use `192.168.1.0/24` and VLAN 10. We rejected fully isolated per-Customer workspaces and NetBox-style VRFs: both add a concept that homelab users would have to understand, while per-Site scoping covers overlapping customer address space with a concept every user already has.

## Consequences

- A Subnet stretched across two Sites (for example over a VPN), or an aggregate covering several Sites' Subnets, cannot be expressed. This is accepted for v1.
- Overlapping address space *within* one Site is impossible; if enterprises need it, VRFs would be added on top later.
