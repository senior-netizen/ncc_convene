"""Persistent, standard-library launcher for the local NCC demonstration."""
import argparse
import os
import secrets
import sqlite3
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from wsgiref.simple_server import make_server


def configure(data_dir):
    data_dir = Path(data_dir).expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    uploads = data_dir / "uploads"
    uploads.mkdir(exist_ok=True)
    secret_file = data_dir / "session-secret"
    if not secret_file.exists():
        secret_file.write_text(secrets.token_urlsafe(48), encoding="utf-8")
        try:
            secret_file.chmod(0o600)
        except OSError:
            pass
    os.environ.update(APP_DATABASE=str(data_dir / "ncc-convene.db"),
                      APP_UPLOAD_DIR=str(uploads),
                      APP_SESSION_SECRET=secret_file.read_text(encoding="utf-8").strip())
    return data_dir


def initialize(data_dir):
    from .db import Database
    from .seed import seed
    database = Database(os.environ["APP_DATABASE"])
    count = database.execute("SELECT count(*) AS n FROM organisations").fetchone()["n"]
    if count == 0:
        seed(database)
    database.close()


def check(data_dir):
    missing = []
    connection = sqlite3.connect(os.environ["APP_DATABASE"])
    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    for (key,) in connection.execute(
        "SELECT v.storage_key FROM document_versions v JOIN documents d "
        "ON d.id=v.document_id AND d.organisation_id=v.organisation_id "
        "WHERE d.deleted_at IS NULL"
    ):
        if not Path(key).is_file():
            missing.append(key)
    connection.close()
    if integrity != "ok" or missing:
        details = "; ".join(([f"integrity: {integrity}"] if integrity != "ok" else []) +
                            ([f"missing files: {', '.join(missing)}"] if missing else []))
        raise SystemExit(f"Demo check failed: {details}")
    print(f"Demo check passed: database integrity ok; all document files present in {data_dir}")


def backup(data_dir, destination=None):
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = Path(destination or data_dir.parent / f"ncc-convene-backup-{timestamp}.tar.gz").resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    # SQLite's online backup API produces a consistent snapshot while preserving
    # the source database. The archive also contains uploads and the session key.
    snapshot = data_dir / ".backup.db"
    source = sqlite3.connect(os.environ["APP_DATABASE"])
    target = sqlite3.connect(snapshot)
    source.backup(target)
    target.close(); source.close()
    with tarfile.open(destination, "w:gz") as archive:
        archive.add(snapshot, arcname="ncc-convene.db")
        archive.add(data_dir / "uploads", arcname="uploads")
        archive.add(data_dir / "session-secret", arcname="session-secret")
    snapshot.unlink()
    print(f"Backup created: {destination}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the persistent NCC Convene demo")
    parser.add_argument("--data-dir", default=".data/demo")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--backup", nargs="?", const="", metavar="ARCHIVE")
    args = parser.parse_args(argv)
    data_dir = configure(args.data_dir)
    initialize(data_dir)
    if args.check:
        check(data_dir)
        return
    if args.backup is not None:
        check(data_dir)
        backup(data_dir, args.backup or None)
        return
    from .web import app
    print(f"NCC Convene demo: http://{args.host}:{args.port}/login")
    make_server(args.host, args.port, app).serve_forever()


if __name__ == "__main__":
    main()
