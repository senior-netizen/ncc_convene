# NCC Convene JSON API contract

This contract describes the Python backend that a future Next.js client can use. The frontend must not scrape HTML. All tenant context comes from the signed session cookie; clients must never send or choose an organisation ID.

## Authentication, cookies and CSRF

- `POST /api/v1/session/login` accepts `{"email":"…","password":"…"}` and sets the `session` cookie (`HttpOnly`, `SameSite=Lax`, `Path=/`; `Secure` when configured). It returns `{"ok":true}`.
- `POST /api/v1/session/logout` expires that cookie and returns `{"ok":true}`.
- `GET /api/v1/session/me` returns `member`, `roles`, and effective `permissions`.
- `GET /api/v1/session/permissions` returns `{"permissions":[…]}`.
- Unauthenticated JSON requests return HTTP 401. Permission failures return HTTP 403.

The current server-rendered forms carry a per-session CSRF field. Existing JSON mutations rely on `SameSite=Lax`; before enabling a separately hosted browser frontend, add an explicit CSRF header contract to JSON mutations. The recommended integration is **same origin**: proxy `/api/*`, `/documents/*`, and the existing mutation paths from Next.js to Python, preserve `Host`/cookie headers, and keep TLS termination and cookies on one public origin.

## Meeting reads

All meeting reads require `meetings.read`, are tenant-scoped from the session, and return 404 for a foreign or deleted resource.

- `GET /api/v1/meetings?limit=50&offset=0` returns `{"items":[…],"limit":50,"offset":0}`. Each item contains `id`, `title`, `starts_at`, `location`, `status`, `quorum_percent`, and `video_provider`. Ordering is `starts_at`, then `id`. Limit is 1–100; offset is 0–9,223,372,036,854,775,807.
- `GET /api/v1/meetings/{meeting_id}` returns `meeting`, `quorum`, aggregate participant counts, ordered `agenda`, and `allowed_transitions`. Agenda fields are `id`, `parent_id`, `title`, parsed `metadata`, `position`, `created_at`, and `updated_at`.
- `GET /api/v1/meetings/{meeting_id}/agenda` returns the same persisted agenda item fields under `items`.
- `GET /api/v1/meetings/{meeting_id}/workspace` returns a tenant-scoped projection containing `meeting`, ordered `agenda`, `participants`, `quorum`, document metadata, conflicts, motions with tallies, resolutions, minutes and minute items, and actions. Documents are omitted unless the user also has `documents.read`. Storage keys and all other filesystem paths are never returned.

## Existing JSON mutations

These paths accept JSON. Permission checks remain in Python.

| Route | Body (required unless marked optional) | Permission |
|---|---|---|
| `POST /meetings` | `title`, timezone-aware `starts_at`, optional `location`, `quorum_percent`, video fields | `meetings.write` |
| `POST /meetings/{id}/transition` | `status` | `meetings.write` |
| `POST /meetings/{id}/agenda` | `title`, integer `position`, optional `parent_id`, `metadata` | `agenda.write` |
| `POST /meetings/{id}/participants` | `member_id`, optional `observer` | `meetings.write` |
| `POST /meetings/{id}/attendance` | `member_id`, `status` | `attendance.write` |
| `POST /meetings/{id}/rsvp` | `response`; another `member_id` requires attendance management | `rsvp.write`/manager |
| `POST /meetings/{id}/conflicts` | `interest`, `management_action`, optional `agenda_item_id`, managed `member_id` | conflict permission |
| `POST /meetings/{id}/conflicts/{conflict}/recusal` | `status` | `conflicts.manage` |
| `POST /meetings/{id}/motions` | `agenda_item_id`, `proposer_member_id`, `text` | `motions.write` |
| `POST /meetings/{id}/motions/{motion}/votes` | `choice`, optional managed `member_id` | vote permission |
| `POST /meetings/{id}/motions/{motion}/close` | `{}` | `motions.write` |
| `POST /meetings/{id}/resolutions` | `agenda_item_id`, `text`, `outcome`, optional `motion_id`, `status` | `resolutions.write` |
| `POST /meetings/{id}/minutes` | `agenda_item_id`, `body`, optional `status` | `minutes.write` |
| `POST /meetings/{id}/minutes/{minute}/items` | `item_type`, `body`, integer `position` | `minutes.write` |
| `POST /meetings/{id}/actions` | `agenda_item_id`, `owner_member_id`, `description`, optional timezone-aware `due_at`, `resolution_id`, `priority` | `actions.write` |
| `POST /meetings/{id}/actions/{action}/updates` | `body`, optional `status` | owner/manager |
| `POST /meetings/{id}/actions/{action}/evidence` | `note`, optional internal `storage_key` (file upload API gap below) | owner/manager |
| `POST /meetings/{id}/actions/{action}/complete` | `{}`; requires evidence | owner/manager |

`GET /meetings/{id}/actions` and `/resolutions` are meeting-scoped. Member management remains at `/members` and `/members/{id}`.

## Documents

- Browser multipart upload: `POST /meetings/{meeting_id}/documents`, fields `file`, optional `title`, `classification`, `agenda_item_id`.
- Browser multipart replacement: `POST /documents/{document_id}/replace`, field `file`.
- `GET /documents/{document_id}/versions` returns JSON `{"items":[…]}` unless HTML is explicitly accepted. Metadata includes version, size, SHA-256, media type and creation time.
- `GET /documents/{document_id}/download?version=N` streams the selected historical bytes. Omitting `version` streams the latest. Ordinary PDF/file Accept headers work. Filenames use RFC 5987 encoding.

## Errors

New v1 reads and validation/conflict handling use:

```json
{"error":{"code":"invalid_request","message":"Human-readable explanation."}}
```

Important codes include `authentication_required`, `forbidden`, `not_found`, `invalid_pagination`, `invalid_version`, `invalid_request`, and `duplicate_vote`. Some legacy mutation permission/not-found responses still use `{"error":"forbidden"}` or `{"error":"not found"}`; normalization is an explicit API gap.

## Explicit Next.js gaps

Before a public Next.js client ships: add explicit CSRF-token/header enforcement to JSON mutations; add multipart JSON-API aliases for document/evidence upload; normalize every legacy error envelope; define CORS only if same-origin proxying is rejected; and add refresh/session-expiry UX. Business and governance decisions must remain in Python.
