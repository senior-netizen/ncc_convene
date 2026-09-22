# NCC Convene

NCC Convene is a tenant-isolated board-meeting application. This repository delivers the Phase 1/2 foundation: identities and roles, secure signed sessions, relational persistence, meeting and agenda workflows, board papers, immutable audit records, and a seeded NCC demo.

## Local development

Requires Python 3.11+ (no third-party runtime dependencies).

```bash
python -m app.seed --database .data/ncc-convene.db
APP_DATABASE=.data/ncc-convene.db APP_SESSION_SECRET='replace-this-with-a-long-random-secret' python -m app.web
# Open http://127.0.0.1:8000. Demo login: secretariat@ncc.example / ChangeMe123!
python -m unittest discover -v
```

`APP_SESSION_SECRET` is required outside development. Cookies are `HttpOnly`, `SameSite=Lax`, signed, and expire after eight hours. Deploy behind HTTPS and set `APP_COOKIE_SECURE=1`.

## API demo path

Sign in with `POST /login`, then use the session cookie to create a draft meeting (`POST /meetings`), build its agenda (`POST /meetings/{id}/agenda`), assign participants (`POST /meetings/{id}/participants`), and publish it (`POST /meetings/{id}/transition` with `{"status":"published"}`). The Secretariat can then record RSVPs and attendance. All mutation endpoints expect JSON and audit their changes.

## Data and permissions

The complete SQLite/PostgreSQL-compatible schema is in `migrations/001_phase_1_2.sql`. All tenant-owned tables have `organisation_id`; repository methods bind it from the authenticated session. `app/policy.py` centralises server-side role/permission checks. PostgreSQL deployments can apply the commented RLS policy template in the migration.
