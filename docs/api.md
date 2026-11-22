# REST API conventions

Every API endpoint follows these rules. The machine-readable reference is the OpenAPI schema at `/api/schema/`, which requires authentication.

## Authentication

Send a token with every request:

```http
Authorization: Bearer nat_...
```

Create tokens on the **API tokens** page. A token is shown once, acts with its owner's current Role, and can have an expiry. NetAtlas stores only a hash of it.

- Users manage their own tokens. Admins can also list and revoke everyone's.
- A token cannot create another token, so a leaked token cannot outlive its revocation.
- A logged-in browser session also works, and its writes need a CSRF token.
- `/api/status/` is public, for health checks.

## Pagination

Lists accept `page` (from 1) and `page_size` (default 50, maximum 200):

```json
{"count": 204, "next": "https://…/api/users/?page=2", "previous": null, "results": []}
```

## Filtering

Filters are exact-match query parameters, such as `/api/users/?role=viewer`. An unknown filter or value is a `400`, so a typo never returns an unfiltered list.

## Errors

Every error has the same shape. `fields` lists messages per field and is empty for other errors.

```json
{"error": {"code": "validation_error", "message": "Invalid input.", "fields": {"password": ["This password is too short."]}}}
```

| Status | `code` |
| --- | --- |
| 400 | `validation_error`, `parse_error` |
| 401 | `not_authenticated` |
| 403 | `permission_denied` |
| 404 | `not_found` |
| 405 | `method_not_allowed` |
| 406 | `not_acceptable` |
| 415 | `unsupported_media_type` |
| 503 | `unavailable` |

`/api/status/` keeps its own body on `503`, because health checks read it.

## History

`GET /api/history/<kind>/<id>/` lists an item's History, newest first, for any Role. Entries record who changed which field from what to what, and stay available after the item is deleted. History is read-only.

## Customers and Sites

`/api/customers/` and `/api/sites/` support list, create, read, `PATCH` and delete. Every Role can read. Admins and Editors can write. `/api/sites/?customer=<id>` filters Sites by Customer.

- Deleting a Customer keeps its Sites, which then have no Customer.
- The last remaining Site cannot be deleted, and the attempt is a `400`.
- A fresh install has one Site named `Default`. Its History credits the actor `system`.

## Device Types

`/api/device-types/` supports list, create, read, `PATCH` and delete. Every Role can read. Admins and Editors can write.

- `interfaces` is a list of `{"name", "kind"}`, where `kind` is `physical`, `virtual` or `lag`. A `PATCH` replaces the whole list.
- A name can be a bulk pattern: `port[1-48]`, `port[01-48]` (zero-padded) or `Gi[1-2]/0/[1-48]`. Responses always list the expanded names.
- A Device Type has at most 1024 Interfaces, and their names are unique within it.
- `rack_height` is in units, and `0` is allowed. Make and model together are unique.

## Devices

`/api/devices/` supports list, create, read, `PATCH` and delete. Every Role can read. Admins and Editors can write. Lists filter by `site`, `kind` and `parent`.

- `kind` is `physical` or `virtual` and cannot be changed. Device names are unique within a Site.
- A Virtual Device needs a Hypervisor (`parent_id`), is always in its Site and takes no rack space. A Physical Device has no `parent_id` and needs a `site_id`.
- `device_type_id` copies the Device Type's Interfaces and height once. A Virtual Device can only use a type whose Interfaces are all virtual.
- Changing a Device's Site moves everything nested on it, and each move is recorded in History. A name clash in the new Site moves nothing.
- `GET /api/devices/<id>/virtual-devices/` lists everything running on a Device, depth first, with its `depth`.
- Deleting a Device deletes its Interfaces. A Hypervisor with Virtual Devices, and a Site with Devices, cannot be deleted.

## Interfaces

`/api/devices/<id>/interfaces/` lists and creates a Device's Interfaces. `/api/interfaces/<id>/` reads, changes (`PATCH`) and deletes one. Every Role can read. Admins and Editors can write.

- `kind` is `physical`, `virtual` or `lag`. A Virtual Device can only have Virtual Interfaces. Names are unique within a Device.
- `lag_id` bundles a Physical Interface into a LAG of the same Device.
- `kind` cannot change while the Interface is in a LAG, or is a LAG with members.
- Deleting a LAG keeps its members, without a LAG, and records that in their History.

## Racks

`/api/racks/` supports list, create, read, `PATCH` (name and height) and delete. Lists filter by `site`. Every Role can read. Admins and Editors can write.

- Rack names are unique within a Site. Height is 1 to 100 units, numbered from 1 at the bottom, and cannot shrink below the highest Device.
- `PUT /api/devices/<id>/placement/` with `{"rack_id", "unit", "face"}` places or moves a Physical Device in a Rack of its Site. `unit` is its lowest unit and `face` is `front` or `rear`. `DELETE` takes it out.
- A Device occupies its units on its own Face only, so front and rear can share a unit. Overlaps on one Face are refused.
- A zero-height Device is placed with `rack_id` alone.
- A racked Device must be taken out before it changes Site or switches to or from zero height. A Rack holding Devices cannot be deleted.
- `GET /api/racks/<id>/elevation/` returns `front` and `rear` from the top unit down, plus `zero_height`. A taller Device has `span` on its top unit and `is_covered` on the units below.

## Subnets

`/api/subnets/` supports list, create, read, `PATCH` and delete, for IPv4 and IPv6. Lists filter by `site` and `version`. Every Role can read. Admins and Editors can write.

- `cidr` is stored in canonical form and is unique within a Site (ADR 0002). A value with host bits set, such as `192.168.1.5/24`, is refused with the correct form in the message.
- Nesting is derived from the addresses. Lists are in tree order, and each Subnet has `parent_id` and `depth`.
- `utilisation` is the percentage covered by directly nested Subnets, rounded down to one decimal.
- Deleting a Subnet keeps those nested in it, which move up the tree. A Site with Subnets cannot be deleted.

## Cables

`/api/cables/` supports list, create, read, `PATCH` (label only) and delete. Lists filter by `device`. Every Role can read. Admins and Editors can write.

- A Cable joins two different Physical Interfaces, which may be in different Sites. An Interface has at most one Cable.
- Ends are stored lowest Interface ID first, so `interface_a_id` may differ from the order you sent.
- Deleting an Interface or Device deletes its Cables, and records that in History.
- `PUT /api/interfaces/<id>/pair/` with `{"interface_id": <other>}` pairs two Physical Interfaces of one Device, such as a patch panel's front and rear. `DELETE` removes the pair. Deleting one side dissolves it.
- `GET /api/interfaces/<id>/trace/` returns the whole path through a Physical Interface as `hops`, end to end. `cable_id` on a hop is the Cable to the next hop, and is null across a pair. `is_loop` is true if the path returns to itself.
- An Interface's `kind` cannot change while it has a Cable or a pair.

## Adding a documented item

- Inherit from `TrackedModel`, and set `history_kind` and any `history_exclude` fields.
- Write it only through `create_item`, `update_item` and `delete_item` in `netatlas/history/services.py`. They enforce the Role and record History in the same transaction. Any other write raises `UntrackedWrite`.
- Many-to-many fields are not recorded automatically. A slice that adds one must record its changes.

## Adding an endpoint

- Call the service layer from an `APIView`, and describe the shapes with plain `Serializer` classes (ADR 0003). `tests/test_adapter_boundary.py` rejects model serializers, generic views and model viewsets.
- Use `paginated` and `allowed_filters` from `netatlas/api/conventions.py`.
- Annotate each method with `@extend_schema`. The test suite fails on schema warnings.
