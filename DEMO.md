# NCC Convene demonstration runbook

## Start safely

Python 3.11+ is the only runtime requirement. Use different persistent directories so rehearsal changes cannot affect presentation data.

### macOS/Linux

```bash
python -m app.demo --data-dir .data/rehearsal --check
python -m app.demo --data-dir .data/rehearsal --host 127.0.0.1 --port 8000
# Presentation day, in a separate terminal/data directory:
python -m app.demo --data-dir .data/presentation --host 127.0.0.1 --port 8000
```

### Windows PowerShell

```powershell
py -3.11 -m app.demo --data-dir .data\rehearsal --check
py -3.11 -m app.demo --data-dir .data\rehearsal --host 127.0.0.1 --port 8000
py -3.11 -m app.demo --data-dir .data\presentation --host 127.0.0.1 --port 8000
```

The launcher creates the database, upload tree, and a generated session signing secret only when needed. It seeds only a database with no organisations. Restarting does **not** reset meeting state, attendance, votes, action state, or documents.

## Accounts

All demo-only accounts use `ChangeMe123!`: `secretariat@ncc.example`, `orgadmin@ncc.example`, `superadmin@ncc.example`, `chair@ncc.example`, `observer@ncc.example`, and `commissioner01@ncc.example` through `commissioner16@ncc.example`. Never reuse these credentials outside disposable demonstration data.

The meeting contains the Chair and sixteen Commissioners (17 voting board members). Secretariat and the observer attend as non-voting participants. Existing recusal and present-attendee voting rules still apply.

## Backup, check and recovery

```bash
python -m app.demo --data-dir .data/presentation --check
python -m app.demo --data-dir .data/presentation --backup .data/presentation-before-demo.tar.gz
```

The archive contains a consistent SQLite snapshot, uploads, and the session secret. Stop the app, extract those three entries into a clean data directory, and restart with that directory. Current document `storage_key` values are absolute paths: restoring to a different absolute directory does not rewrite them, so document checks/downloads can fail. Restore to the original path or migrate the keys deliberately after validating the copy. Backups are local operational snapshots, not a substitute for encrypted/off-site production backup.

## Rehearsal and ten-minute presentation

1. **0:00–1:00** — Sign in as Secretariat and open the seeded meeting.
2. **1:00–2:00** — Show 17 eligible board members, Secretariat/observer exclusion, agenda, and persisted PDFs labelled as demonstration content.
3. **2:00–3:00** — Upload a PDF, replace it, open Versions, and download both historical versions.
4. **3:00–4:00** — In another browser profile, sign in as Commissioner 01, RSVP, and return to the workspace.
5. **4:00–6:00** — As Secretariat create a motion with an eligible participant; as the Commissioner vote; close the motion. A second vote produces a controlled conflict.
6. **6:00–7:00** — Create the matching resolution and minutes/minute item.
7. **7:00–8:30** — Create a linked action, selecting an eligible active owner and a Harare local due time.
8. **8:30–9:15** — Show completion rejected without evidence; add evidence and complete it; point out the completion timestamp.
9. **9:15–10:00** — Sign in as Organisation Admin and show the append-only activity log. Restart the server and show that the session, documents, and workflow state remain.

## Remaining limitations and readiness

- **Local demo: ready.** Persistent launch/check/backup, real PDFs, tenant-scoped workflows, and HTTP acceptance coverage are provided. Perform the runbook once on the presentation machine.
- **Public deployment: not ready.** SQLite plus filesystem uploads are not durable on Vercel/serverless filesystems. A hosted deployment needs a managed relational database, durable object storage, key/secret management, TLS, monitoring, encrypted backups, migration operations, and security review.
- **Next.js integration: contract ready, implementation not started.** Use the same-origin proxy arrangement in `API.md`. Explicit JSON CSRF enforcement, multipart API aliases and final legacy-error normalization remain before public frontend delivery.

Document annotations and approval workflows are not implemented and are not part of this demonstration.

## Exact tested demo commands

```bash
python -m app.demo --data-dir .data/rehearsal --check
python -m app.demo --data-dir .data/rehearsal --backup .data/rehearsal.tar.gz
python -m app.demo --data-dir .data/rehearsal --host 127.0.0.1 --port 8000
```
