# Frontend route and response inventory

This inventory was recorded before the Next.js implementation and reflects the
running Python handlers in `app/web.py` rather than aspirational documentation.

## Session and reads

| Route | Verified response/use |
|---|---|
| `POST /api/v1/session/login` | JSON credentials; sets the HTTP-only `session` cookie. |
| `POST /api/v1/session/logout` | Expires the cookie. JSON logout currently needs no CSRF header. |
| `GET /api/v1/session/me` | Member, roles, effective permissions, and the per-session CSRF token. |
| `GET /api/v1/meetings` | Tenant-scoped meeting summaries. |
| `GET /api/v1/meetings/{id}/workspace` | Meeting, agenda, participants, quorum, documents (permission dependent), conflicts, motions/tallies, resolutions, minutes/items, and actions. |
| `GET /api/v1/papers?meeting_id=…` | Paper summaries including current revision and optimistic lock version. |
| `GET /api/v1/papers/{id}` | Paper detail, revision-bound reviews, and comments. |
| `GET /api/v1/meetings/{id}/conference` | Call state, caller admission, capabilities, and (for moderators) admission queue. |
| `GET /documents/{id}/download?version=N` | Protected binary download. |
| `GET /documents/{id}/versions` | JSON when the client does not request HTML. |

## Mutations

Paper, annotation, and conference JSON mutations already require
`X-CSRF-Token`. Legacy `/meetings/*` JSON mutations are protected by the same
header as part of this frontend integration. All identity and organisation scope
continue to come exclusively from the signed Python session.

The workspace uses only the implemented meeting transition, agenda,
participant, attendance, RSVP, conflict/recusal, motion/vote/close, resolution,
minutes/item, action/update/evidence/complete, paper/revision/reviewer/comment/
decision, board-pack, document upload/replacement, and conference routes.
Multipart document routes use the existing signed-session CSRF form field.

## Missing or deliberately unrepresented contracts

* No recording route or durable media storage exists.
* Formal attendance is not inferred from LiveKit presence.
* Conflict recusal does not remove media access.
* Presenter grants are recorded but screen-share exclusivity is not enforced by
  the provider.
* The paper JSON content field is a text-content demonstrator; real PDFs use the
  separate protected multipart document endpoint and do not become workflow
  paper revisions.
* There is no board-pack listing/download response, only edition publication.
* Action evidence has no JSON multipart alias; the UI connects the verified
  note endpoint only.
