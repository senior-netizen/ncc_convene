# NCC Convene

NCC Convene is a tenant-isolated board-meeting application. This repository delivers the Phase 1/2 foundation: identities and roles, secure signed sessions, relational persistence, meeting and agenda workflows, board papers, immutable audit records, and a seeded NCC demo.

## Local development

Requires Python 3.11+ (no third-party runtime dependencies).

```bash
python -m app.demo --data-dir .data/rehearsal --check
python -m app.demo --data-dir .data/rehearsal
# Open http://127.0.0.1:8000/login. Demo login: secretariat@ncc.example / ChangeMe123!
python -m unittest discover -v
```

For the connected Next.js workspace, keep the Python process running and start
the frontend in a second shell:

```bash
cd web
npm ci
NCC_API_ORIGIN=http://127.0.0.1:8000 npm run dev
# Open http://localhost:3000 and sign in; do not open the Python login page.
```

The Next.js server forwards `/api/*`, `/meetings/*`, and `/documents/*` to
Python on the same browser origin. Python remains responsible for the signed
HTTP-only session, tenant selection, CSRF validation, permissions, audit,
governance decisions, protected files, and LiveKit token issuance. Do not set a
`NEXT_PUBLIC_` backend secret.

### Ten-minute Next.js demonstration

1. Sign in at `http://localhost:3000` as `secretariat@ncc.example`, open the
   seeded meeting card, and show the status, agenda, quorum and attendance.
2. Open **Papers**, create a clearly labelled text-content draft, submit its
   displayed revision, assign a seeded commissioner, and add a revision comment.
   Use **Protected documents** separately to demonstrate a real PDF upload and
   authenticated download.
3. In a separate private browser session, sign in as the assigned commissioner,
   review the paper and record a decision. Return as Secretariat to publish all
   approved papers as a new immutable edition.
4. Record formal attendance, then create a motion. In the commissioner session,
   cast a vote; as Secretariat close it and create the linked resolution,
   minutes, and action. Add an evidence note before completing the action.
5. On **Conference**, start an admission-required call. Request entry from the
   commissioner session, admit them, and (only with configured LiveKit) join
   from both browsers. Move between Agenda and Papers to show the mounted call.
   Lock, remove, and end are moderator controls; none alters formal attendance
   or the governance meeting lifecycle.

Real media exchange is not verified by the Python tests. It requires reachable
LiveKit credentials, browser device permission, and a two-browser rehearsal as
described in [CONFERENCING.md](CONFERENCING.md). Email delivery, production
hosting/TLS, persistent backup storage, and LiveKit capacity remain operator
infrastructure responsibilities.

See [DEMO.md](DEMO.md) for the persistent launcher, backup/recovery,
rehearsal and ten-minute runbook. See [API.md](API.md) for the authenticated
JSON/file contract and the future Next.js handoff.
See [CONFERENCING.md](CONFERENCING.md) for the embedded LiveKit architecture,
security model, local/Cloud setup, API contract, demo and verification limits.

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
