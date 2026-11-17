# Discovery only suggests, never overwrites

NetAtlas is the single source of truth for how the network *should* look, and its data is entered by hand. When discovery (ping sweeps, SNMP, LLDP, …) is added, it will only produce suggestions that a person accepts or rejects; it never writes documented data directly. We chose this over auto-syncing so that documented intent is never silently replaced by whatever happens to be on the wire, including misconfigurations that discovery would otherwise "document" as correct.
