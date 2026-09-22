# NCC Convene

NCC Convene is a tenant-isolated board-meeting application. This repository delivers the Phase 1/2 foundation: identities and roles, secure signed sessions, relational persistence, meeting and agenda workflows, board papers, immutable audit records, and a seeded NCC demo.

## Local development

Requires Python 3.11+ (no third-party runtime dependencies).

```bash
python -m app.seed --database .data/ncc-convene.db
# Repeatable development reset (preserves immutable audit history):
python -m app.seed --database .data/ncc-convene.db --reset
APP_DATABASE=.data/ncc-convene.db APP_SESSION_SECRET='replace-this-with-a-long-random-secret' python -m app.web
# Open http://127.0.0.1:8000/login. Demo login: secretariat@ncc.example / ChangeMe123!
python -m unittest discover -v
```

`APP_SESSION_SECRET` is required outside development. Cookies are `HttpOnly`, `SameSite=Lax`, signed, and expire after eight hours. Set `APP_COOKIE_SECURE=1` to add the `Secure` attribute. Uploaded files are stored beneath `.data/uploads/<organisation>/<document>/<version>` (override with `APP_UPLOAD_DIR`) and seeded board papers are immediately downloadable. Meetings follow `draft → scheduled → published → completed`; cancellation is available before completion. Browser mutation forms use signed-session CSRF tokens; API JSON workflows remain available for integrations.

## Seeded demo

The seed command creates the NCC operating personas: `superadmin@ncc.example`,
`orgadmin@ncc.example`, `secretariat@ncc.example`, and `observer@ncc.example`,
as well as `chair@ncc.example` and `commissioner01@ncc.example` through
`commissioner16@ncc.example`. Every demo account uses `ChangeMe123!` and must
be changed before any non-demo deployment. It includes the 17-member board, a
hybrid 24 September 2026 meeting, eight agenda items, four board papers and
participant RSVP/attendance data.

`--reset` is allowed in `APP_ENV=development`, `dev`, or `test` (the default is
development). In another environment application code must call `seed` with an
existing NCC Super Admin member ID; the command-line reset is therefore refused.

## Demo Runbook

1. Start the server and sign in at `/login` as `secretariat@ncc.example`.
2. Open the seeded Quarterly Commission Meeting from Dashboard.
3. Review the agenda, papers, participants, attendance and quorum in the meeting workspace.
4. Use the meeting workspace forms to assign participants, record attendance, save an RSVP, declare and manage conflicts, create motions, cast/close votes, create resolutions, save minutes, and create actions.
5. Upload a real PDF from the Board papers form. Use `Replace version` on the paper row to upload a new file; the version list and download link expose the latest stored bytes.
6. For an action, add an update, attach completion evidence (note plus optional PDF), then select `Complete action`. Completion is rejected until evidence exists.
7. Move the meeting through its permitted lifecycle buttons: `draft → scheduled → published → completed`; cancellation is available before completion. Switch to `orgadmin@ncc.example` or `superadmin@ncc.example` before opening `/activity-log`; Secretariat does not have `audit.read`.
8. Use `commissioner01@ncc.example` in a separate session for member RSVP/voting demonstrations where role ownership matters.

## API demo path

Sign in with `POST /login`, then use the session cookie to create a draft meeting (`POST /meetings`), build its agenda (`POST /meetings/{id}/agenda`), and assign participants (`POST /meetings/{id}/participants`). Transition it in order: `POST /meetings/{id}/transition` with `{"status":"scheduled"}`, then the same route with `{"status":"published"}`. The Secretariat can then record RSVPs and attendance. All mutation endpoints expect JSON and audit their changes.

## Data and permissions

The complete SQLite/PostgreSQL-compatible schema is in `migrations/001_phase_1_2.sql`. All tenant-owned tables have `organisation_id`; repository methods bind it from the authenticated session. `app/policy.py` centralises server-side role/permission checks. PostgreSQL deployments can apply the commented RLS policy template in the migration.
