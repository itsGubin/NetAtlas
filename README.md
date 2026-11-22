# NetAtlas

A self-hosted tool for documenting a network: its devices, cabling, VLANs and IP address space, together with the written documentation that explains it. See `GLOSSARY.md` for the vocabulary and `docs/adr/` for the decisions behind it.

## Run it

Requires Docker with Compose.

```sh
cp .env.example .env   # then set the secret key, database password and first Admin
docker compose up -d
```

NetAtlas is then on http://localhost:8000. `GET /api/status/` reports the version and whether the database is reachable (503 when it isn't). The REST API is described in [docs/api.md](docs/api.md). To log in through Authentik, Keycloak, Entra ID or Google, see [docs/oidc.md](docs/oidc.md). To back up, restore or upgrade, see [docs/backup.md](docs/backup.md).

## Develop

```sh
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
docker run -d --name netatlas-db -p 5432:5432 \
  -e POSTGRES_DB=netatlas -e POSTGRES_USER=netatlas -e POSTGRES_PASSWORD=netatlas postgres:18
.venv/bin/pytest          # tests, against real Postgres
.venv/bin/lint-imports    # views and API may only reach models through services (ADR 0003)
NETATLAS_DEBUG=true .venv/bin/python manage.py runserver
```

Code layout: `netatlas/web` (HTMX views) and `netatlas/api` (REST API) are thin adapters; domain rules live in each app's `services.py`.
