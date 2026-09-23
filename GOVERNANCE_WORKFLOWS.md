# Governance workflow operations

## Implemented scope

The board-paper workflow is enabled end to end. A paper wraps the existing tenant-scoped document and immutable document-version records. It supports draft, submission, reviewer assignment, revision-specific comments, changes requested, resubmission, revision-bound approval, and immutable board-pack editions. Updating an approved or published paper creates a new document revision and returns the paper to draft; it never changes a published edition.

Reader bookmarks, highlights, and private notes are anchored to a document revision and returned only to their owner. Sharing is not enabled. Existing meeting, attendance, motion/vote, resolution, minutes, action, audit, and LiveKit conference workflows remain in service.

## Configuration and startup

Requirements are Python 3.11+, Node 20+ for the optional Next.js client, SQLite, and the dependencies in `pyproject.toml` and `web/package.json`.

```sh
export APP_DATABASE="$PWD/.data/ncc-convene.db"
export APP_UPLOAD_DIR="$PWD/.data/uploads"
export APP_SESSION_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
python -m app.seed
python -m app.web
```

In a second terminal:

```sh
cd web
npm ci
NCC_API_ORIGIN=http://127.0.0.1:8000 npm run dev
```

Migrations run idempotently when `Database` opens. Do not run the demo reset in production. Normal restart/redeploy reopens `APP_DATABASE` and never invokes the seed command. Uploads must be on persistent storage and backed up with the SQLite database. Configure the existing LiveKit variables described in `CONFERENCING.md`; this change introduces no second media provider.

## Backup and recovery

Take the database and upload tree from the same maintenance window. SQLite's online backup is exposed by the existing demo utility:

```sh
python -m app.demo backup --data-dir .data --destination /secure-backups/ncc-$(date +%F)
```

Restore into a **separate** environment before promoting it:

```sh
mkdir -p /tmp/ncc-restore
cp /secure-backups/ncc-YYYY-MM-DD/ncc-convene.db /tmp/ncc-restore/
cp -a /secure-backups/ncc-YYYY-MM-DD/uploads /tmp/ncc-restore/
APP_DATABASE=/tmp/ncc-restore/ncc-convene.db APP_UPLOAD_DIR=/tmp/ncc-restore/uploads python -m app.demo check --data-dir /tmp/ncc-restore
APP_DATABASE=/tmp/ncc-restore/ncc-convene.db APP_UPLOAD_DIR=/tmp/ncc-restore/uploads python -m unittest tests.test_api_acceptance -v
```

Also compare record counts for `board_papers`, `document_versions`, `board_pack_editions`, and `board_pack_items`, and verify every `storage_key` exists and matches its `sha256` before promotion. This repository does not include access to an operator backup target, so no production restoration is claimed.

## Ten-minute sample-data demonstration

1. Label the seeded NCC organisation and 24 September 2026 meeting as sample data.
2. Sign in as a commissioner, open the meeting's **Papers** tab, fill all template sections, save a draft, and submit its displayed revision.
3. Sign in as Secretariat, assign another commissioner with a deadline.
4. As that reviewer, add a revision-bound comment and request changes with a reason.
5. As the author, upload corrected content and resubmit; point out the new revision.
6. Reassign/review the new revision and approve it. Attempting to approve the old revision returns HTTP 409.
7. As Secretariat, publish a labelled board-pack edition, then revise the paper and show that the edition still references the approved revision.
8. As a commissioner, add a private revision note and show that another member's annotation query cannot return it.
9. Continue in the existing meeting workspace: attendance, quorum, motion, vote, resolution, minutes, action, and LiveKit call.
10. Restart backend and frontend and show the same paper revisions, pack edition, conference governance records, and append-only audit events.

## Security and operational limits

- Offline board-paper access is disabled. Confidential documents and private notes are not placed in service-worker caches or `localStorage`. Secure encrypted device storage, retention enforcement, remote wipe expectations, and disconnected revocation messaging must be agreed and independently verified before enabling it.
- The JSON demonstrator form sends textual content; production UI file selection and streaming upload remain to be connected to the existing validated binary store.
- Audit records are append-only through application and SQLite trigger controls. They are ordinary database records, not described as tamper-proof.
- Hosting, persistent volume, backup-object storage, email delivery, domain/TLS, monitoring, and LiveKit usage are ongoing operator/vendor costs. No prices are embedded because they depend on the selected providers and traffic.
- The repository license and applicable contracts determine ownership and licensing; this implementation makes no additional ownership claim.

## Prioritized remaining work

1. Replace legacy direct action completion with separate submit/verify permissions, non-self-verification, matters-arising snapshots, and deduplicated reminders.
2. Add decision search/export and a relationship manifest spanning paper revision, motion/vote snapshots, resolution, approved minutes, action, and evidence.
3. Version minutes and authenticated approvals; add PDF rendering through a selected existing export provider.
4. Complete chair agenda controls, configured eligibility snapshots, timers, and speaking queue integration with LiveKit raised hands.
5. Add the commissioner briefing and revision-diff indicators, then browser-driven tests for the full multi-person workflow.
6. Prove backup restoration against the operator's actual persistent storage and document measured recovery time and recovery point objectives.
