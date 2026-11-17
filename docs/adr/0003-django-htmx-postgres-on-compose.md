# Django and HTMX on Postgres, shipped as Docker Compose

NetAtlas is a server-rendered Django application using HTMX, with JavaScript limited to self-contained islands such as the Page editor. It runs on Postgres and is shipped as a Docker Compose stack. Django was chosen because multi-user authentication, Roles, History and a CRUD-heavy domain are what it provides out of the box, and because NetBox and Nautobot prove the pattern for this domain. HTMX keeps almost all of the UI in one language and one codebase.

## Considered Options

- **TypeScript end to end, or a Go single binary**: rejected in favour of Python's ecosystem for this domain.
- **FastAPI + SQLAlchemy**: rejected because authentication, users and permissions would have to be built by hand.
- **A separate SPA over a JSON API**: rejected because it would mean two codebases. The REST API still exists, for scripting and imports, but the UI does not consume it.
- **A single container with embedded SQLite**: rejected in favour of Postgres, which gives full-text search and concurrent multi-user writes.
