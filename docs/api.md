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

## Adding a documented item

- Inherit from `TrackedModel`, and set `history_kind` and any `history_exclude` fields.
- Write it only through `create_item`, `update_item` and `delete_item` in `netatlas/history/services.py`. They enforce the Role and record History in the same transaction. Any other write raises `UntrackedWrite`.
- Many-to-many fields are not recorded automatically. A slice that adds one must record its changes.

## Adding an endpoint

- Call the service layer from an `APIView`, and describe the shapes with plain `Serializer` classes (ADR 0003). `tests/test_adapter_boundary.py` rejects model serializers, generic views and model viewsets.
- Use `paginated` and `allowed_filters` from `netatlas/api/conventions.py`.
- Annotate each method with `@extend_schema`. The test suite fails on schema warnings.
