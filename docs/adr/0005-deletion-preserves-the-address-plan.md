# Deletion preserves the address plan and never cascades through Virtual Devices

Deleting a Device also deletes its Interfaces and the Cables attached to them, but IP Addresses that were Assigned to those Interfaces become Reserved rather than being deleted. Otherwise, removing a Device would silently free its addresses for reuse. Deleting a Device that is the Hypervisor of any Virtual Device is refused until those Virtual Devices are moved or deleted, so that one action cannot wipe out a whole cluster. Before any delete is confirmed, the user is shown everything it will affect.

## Consequences

- When a Free-text Section is removed from a Page Template, its content is deleted from every Page using that template. It can only be recovered from History. Before saving, the template editor warns how many Pages have content in that section, so the loss is never silent.
