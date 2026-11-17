# Django and HTMX on Postgres, shipped as Docker Compose

NetAtlas is a server-rendered Django application using HTMX, with JavaScript limited to self-contained islands such as the Page editor. It runs on Postgres and is shipped as a Docker Compose stack. Django was chosen because multi-user authentication, Roles, History and a CRUD-heavy domain are what it provides out of the box, and because NetBox and Nautobot prove the pattern for this domain. HTMX keeps almost all of the UI in one language and one codebase.

The HTMX views and the REST API are two thin adapters over one shared service layer, which holds all domain rules, validation and History recording. Neither adapter may bypass it. The UI does not make HTTP calls to the API, but every operation the UI can perform exists in the service layer and is therefore exposed by the API. This keeps the API complete by construction, so that Discovery can later be just another API client (ADR 0001).

## Considered Options

- **TypeScript end to end, or a Go single binary**: rejected in favour of Python's ecosystem for this domain.
- **FastAPI + SQLAlchemy**: rejected because authentication, users and permissions would have to be built by hand.
- **A separate SPA over a JSON API**: rejected because it would mean two codebases.
- **HTMX views calling the REST API over HTTP**: rejected as needless overhead. The shared service layer gives the same guarantee that the API covers everything the UI does.
- **A single container with embedded SQLite**: rejected in favour of Postgres, which gives full-text search and concurrent multi-user writes.
