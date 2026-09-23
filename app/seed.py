"""A repeatable, deliberately small NCC demonstration dataset.

The IDs, timestamps, passwords and content in this module are fixed on purpose:
running it twice is safe and makes a useful reset for local demos and tests.
"""
import argparse
import hashlib
import os
import uuid
from pathlib import Path

from .auth import hash_password
from .db import Database


SEED_NAMESPACE = uuid.UUID("482bcd3b-0fd4-5b19-9b71-59dbe3d3f7b3")
SEED_TIME = "2026-09-01T09:00:00+00:00"
ORGANISATION_ID = str(uuid.uuid5(SEED_NAMESPACE, "organisation:ncc"))
MEETING_ID = str(uuid.uuid5(SEED_NAMESPACE, "meeting:2026-09-24"))
COMMITTEE_ID = str(uuid.uuid5(SEED_NAMESPACE, "committee:ncc-board"))
PASSWORD = "ChangeMe123!"
ROLES = ("Super Admin", "Organisation Admin", "Secretariat", "Chairperson",
         "Commissioner/Board Member", "Observer")


def _id(kind, name):
    return str(uuid.uuid5(SEED_NAMESPACE, f"{kind}:{name}"))


def _password(email):
    """Use a fixed salt so the demonstrator accounts are reproducible."""
    return hash_password(PASSWORD, hashlib.sha256(email.encode()).digest()[:16])


# The first four accounts are operating personas.  The remaining 17 people are
# the board: the Chair and sixteen commissioners.
PERSONAS = (
    ("superadmin@ncc.example", "NCC Super Admin", "Super Admin", "Platform administrator"),
    ("orgadmin@ncc.example", "NCC Organisation Admin", "Organisation Admin", "Organisation administrator"),
    ("secretariat@ncc.example", "NCC Secretariat", "Secretariat", "Board Secretary"),
    ("observer@ncc.example", "NCC Observer", "Observer", "Meeting observer"),
    ("chair@ncc.example", "Dr. Tariro Moyo", "Chairperson", "NCC Chairperson"),
) + tuple(
    (f"commissioner{i:02}@ncc.example", f"Commissioner {i:02}",
     "Commissioner/Board Member", "Commissioner")
    for i in range(1, 17)
)
PASSWORD_HASHES = {email: _password(email) for email, *_ in PERSONAS}

AGENDA = (
    "Opening, apologies and quorum",
    "Declarations of interest",
    "Confirmation of previous minutes",
    "Quarterly competitiveness review",
    "2027 National Competitiveness Outlook",
    "SME regulatory reform programme",
    "Digital trade and investment update",
    "Resolutions, actions and close",
)
PAPERS = (
    (3, "Q3 2026 Competitiveness Dashboard", b"NCC Q3 2026 competitiveness dashboard v1.0"),
    (4, "2027 National Competitiveness Outlook", b"NCC 2027 outlook board paper v1.0"),
    (5, "SME Regulatory Reform Programme", b"NCC SME reform board paper v1.0"),
    (6, "Digital Trade and Investment Update", b"NCC digital trade board paper v1.0"),
)


def _development_mode(value=None):
    if value is not None:
        return bool(value)
    return os.getenv("APP_ENV", "development").lower() in {"development", "dev", "test"}


def _may_reset(db, actor_member_id, development):
    if _development_mode(development):
        return True
    if not actor_member_id:
        return False
    return "Super Admin" in db.roles(actor_member_id, ORGANISATION_ID)


def _clear_demo_content(db):
    """Clear mutable demo records, leaving the append-only audit trail intact."""
    # Delete children before their parents.  audit_logs is intentionally absent:
    # its immutability is a system invariant, including during a demo reset.
    for table in ("completion_evidence", "actions", "minutes", "resolutions", "votes",
                  "motions", "conflict_declarations", "document_notes", "document_versions",
                  "documents", "meeting_rsvps", "meeting_attendees", "agenda_items", "meetings",
                  "committee_members", "committees"):
        db.execute(f"DELETE FROM {table} WHERE organisation_id=?", (ORGANISATION_ID,))


def seed(db, *, reset=False, actor_member_id=None, development=None):
    """Create the deterministic NCC demo, optionally resetting its mutable content.

    Reset is permitted only in development/test mode, or to an existing NCC Super
    Admin.  The actor is an internal value supplied by the caller, never request
    input.  Audit records are deliberately preserved rather than rewritten.
    """
    if reset and not _may_reset(db, actor_member_id, development):
        raise PermissionError("demo reset requires development mode or Super Admin")
    if reset:
        _clear_demo_content(db)

    db.execute("INSERT INTO organisations VALUES(?,?,?,?,?,NULL) ON CONFLICT(id) DO UPDATE SET "
               "name=excluded.name,slug=excluded.slug,updated_at=excluded.updated_at,deleted_at=NULL",
               (ORGANISATION_ID, "National Competitiveness Commission", "ncc", SEED_TIME, SEED_TIME))
    role_ids = {}
    for name in ROLES:
        role_ids[name] = _id("role", name)
        db.execute("INSERT INTO roles VALUES(?,?,?,?,?,NULL) ON CONFLICT(organisation_id,name) DO UPDATE SET "
                   "id=excluded.id,updated_at=excluded.updated_at,deleted_at=NULL",
                   (role_ids[name], ORGANISATION_ID, name, SEED_TIME, SEED_TIME))

    members = {}
    for email, name, role, title in PERSONAS:
        user_id, member_id = _id("user", email), _id("member", email)
        db.execute("INSERT INTO users VALUES(?,?,?,?,?,?,NULL) ON CONFLICT(email) DO UPDATE SET "
                   "id=excluded.id,password_hash=excluded.password_hash,display_name=excluded.display_name,"
                   "updated_at=excluded.updated_at,deleted_at=NULL",
                   (user_id, email, PASSWORD_HASHES[email], name, SEED_TIME, SEED_TIME))
        db.execute("INSERT INTO members VALUES(?,?,?,?,?,?,?,NULL) ON CONFLICT(organisation_id,user_id) DO UPDATE SET "
                   "id=excluded.id,title=excluded.title,status='active',updated_at=excluded.updated_at,deleted_at=NULL",
                   (member_id, ORGANISATION_ID, user_id, title, "active", SEED_TIME, SEED_TIME))
        db.execute("INSERT INTO member_profiles(member_id,organisation_id,display_name,email,phone,address,biography,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(member_id) DO UPDATE SET "
                   "organisation_id=excluded.organisation_id,phone=excluded.phone,address=excluded.address,"
                   "biography=excluded.biography,updated_at=excluded.updated_at",
                   (member_id, ORGANISATION_ID, name, email, None, None, f"Seeded {role} persona.", SEED_TIME, SEED_TIME))
        db.execute("INSERT INTO member_roles VALUES(?,?,?) ON CONFLICT(member_id,role_id) DO UPDATE SET "
                   "created_at=excluded.created_at", (member_id, role_ids[role], SEED_TIME))
        members[email] = member_id

    db.execute("INSERT INTO committees VALUES(?,?,?,?,?,?,NULL) ON CONFLICT(id) DO UPDATE SET "
               "name=excluded.name,description=excluded.description,updated_at=excluded.updated_at,deleted_at=NULL",
               (COMMITTEE_ID, ORGANISATION_ID, "NCC Board", "The 17-member NCC board", SEED_TIME, SEED_TIME))
    board_emails = ("chair@ncc.example",) + tuple(f"commissioner{i:02}@ncc.example" for i in range(1, 17))
    for email in board_emails:
        member_id = members[email]
        db.execute("INSERT INTO committee_members VALUES(?,?,?,?,?,?,?,NULL) ON CONFLICT(committee_id,member_id) DO UPDATE SET "
                   "role=excluded.role,updated_at=excluded.updated_at,deleted_at=NULL",
                   (_id("committee-member", email), ORGANISATION_ID, COMMITTEE_ID, member_id,
                    "chair" if email.startswith("chair") else "member", SEED_TIME, SEED_TIME))

    secretary = members["secretariat@ncc.example"]
    db.execute("INSERT INTO meetings VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL) ON CONFLICT(id) DO UPDATE SET "
               "committee_id=excluded.committee_id,title=excluded.title,starts_at=excluded.starts_at,"
               "location=excluded.location,status=excluded.status,quorum_percent=excluded.quorum_percent,"
               "video_provider=excluded.video_provider,video_metadata=excluded.video_metadata,"
               "updated_at=excluded.updated_at,deleted_at=NULL",
               (MEETING_ID, ORGANISATION_ID, COMMITTEE_ID, "NCC Board Meeting — 24 September 2026",
                "2026-09-24T09:00:00+02:00", "NCC Boardroom, Harare (hybrid)", "scheduled", None, 50,
                "Zoom", '{"meeting_id":"ncc-board-2026-09-24","mode":"hybrid"}', secretary, SEED_TIME, SEED_TIME))
    agenda_ids = []
    for position, title in enumerate(AGENDA):
        agenda_id = _id("agenda", str(position)); agenda_ids.append(agenda_id)
        db.execute("INSERT INTO agenda_items VALUES(?,?,?,?,?,?,?,?,?,NULL) ON CONFLICT(id) DO UPDATE SET "
                   "title=excluded.title,metadata=excluded.metadata,position=excluded.position,"
                   "updated_at=excluded.updated_at,deleted_at=NULL",
                   (agenda_id, ORGANISATION_ID, MEETING_ID, None, title, "{}", position, SEED_TIME, SEED_TIME))
    for position, title, content in PAPERS:
        document_id, version_id = _id("document", title), _id("document-version", title)
        db.execute("INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,?,NULL) ON CONFLICT(id) DO UPDATE SET "
                   "agenda_item_id=excluded.agenda_item_id,title=excluded.title,classification=excluded.classification,"
                   "status=excluded.status,updated_at=excluded.updated_at,deleted_at=NULL",
                   (document_id, ORGANISATION_ID, MEETING_ID, agenda_ids[position], title, "board", "published",
                    secretary, SEED_TIME, SEED_TIME))
        seeded_path = db._safe_upload_path(ORGANISATION_ID, document_id, version_id)
        seeded_path.write_bytes(content)
        db.execute("INSERT INTO document_versions(id,organisation_id,document_id,version_number,storage_key,sha256,size_bytes,created_by,created_at,content_type) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(document_id,version_number) DO UPDATE SET "
                   "id=excluded.id,storage_key=excluded.storage_key,sha256=excluded.sha256,size_bytes=excluded.size_bytes,"
                   "created_by=excluded.created_by,created_at=excluded.created_at,content_type=excluded.content_type",
                   (version_id, ORGANISATION_ID, document_id, 1, str(seeded_path),
                    hashlib.sha256(content).hexdigest(), len(content), secretary, SEED_TIME, "text/plain"))
    participants = board_emails + ("secretariat@ncc.example", "observer@ncc.example")
    responses = ("yes", "yes", "maybe", "yes", "no")
    for index, email in enumerate(participants):
        member_id = members[email]; observer = int(email == "observer@ncc.example")
        db.execute("INSERT INTO meeting_attendees VALUES(?,?,?,?,?,?,?,?,NULL) ON CONFLICT(meeting_id,member_id) DO UPDATE SET "
                   "status=excluded.status,observer=excluded.observer,updated_at=excluded.updated_at,deleted_at=NULL",
                   (_id("attendee", email), ORGANISATION_ID, MEETING_ID, member_id,
                    "present" if index < 10 else "pending", observer, SEED_TIME, SEED_TIME))
        if not observer:
            response = responses[index % len(responses)]
            db.execute("INSERT INTO meeting_rsvps VALUES(?,?,?,?,?,?,?,?,NULL) ON CONFLICT(meeting_id,member_id) DO UPDATE SET "
                       "response=excluded.response,responded_at=excluded.responded_at,updated_at=excluded.updated_at,deleted_at=NULL",
                       (_id("rsvp", email), ORGANISATION_ID, MEETING_ID, member_id, response,
                        SEED_TIME, SEED_TIME, SEED_TIME))
    db.conn.commit()
    return ORGANISATION_ID


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default=".data/ncc-convene.db")
    parser.add_argument("--reset", action="store_true", help="reset mutable demo records (development only)")
    args = parser.parse_args()
    os.makedirs(os.path.dirname(args.database) or ".", exist_ok=True)
    with Database(args.database) as database:
        seed(database, reset=args.reset)
    print("Seeded National Competitiveness Commission")
