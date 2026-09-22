import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = Path(__file__).parent.parent / "migrations/001_phase_1_2.sql"


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return str(uuid.uuid4())


class Database:
    """Tenant-scoped persistence operations for the NCC Convene application."""

    def __init__(self, path=":memory:"):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA.read_text())

    def execute(self, sql, params=()):
        return self.conn.execute(sql, params)

    def audit(self, organisation_id, actor, event, resource, resource_id=None, payload=None):
        self.execute(
            "INSERT INTO audit_logs VALUES (?,?,?,?,?,?,?,?)",
            (uid(), organisation_id, actor, event, resource, resource_id,
             json.dumps(payload or {}, sort_keys=True), now()),
        )
        self.conn.commit()

    def member(self, org, user):
        return self.execute(
            "SELECT * FROM members WHERE organisation_id=? AND user_id=? AND deleted_at IS NULL",
            (org, user),
        ).fetchone()

    def roles(self, member_id, org=None):
        if org is None:
            return {
                row["name"] for row in self.execute(
                    "SELECT r.name FROM roles r JOIN member_roles mr ON mr.role_id=r.id "
                    "WHERE mr.member_id=? AND r.deleted_at IS NULL", (member_id,)
                )
            }
        return {
            row["name"] for row in self.execute(
                "SELECT r.name FROM roles r "
                "JOIN member_roles mr ON mr.role_id=r.id "
                "JOIN members m ON m.id=mr.member_id "
                "WHERE mr.member_id=? AND m.organisation_id=? "
                "AND m.deleted_at IS NULL AND r.deleted_at IS NULL "
                "AND (r.organisation_id=? OR r.organisation_id IS NULL)",
                (member_id, org, org),
            )
        }

    def meeting(self, org, meeting_id):
        return self.execute(
            "SELECT * FROM meetings WHERE id=? AND organisation_id=? AND deleted_at IS NULL",
            (meeting_id, org),
        ).fetchone()

    def _require_meeting(self, org, meeting_id):
        meeting = self.meeting(org, meeting_id)
        if not meeting:
            raise ValueError("meeting outside tenant")
        return meeting

    def _require_member(self, org, member_id):
        member = self.execute(
            "SELECT 1 FROM members WHERE id=? AND organisation_id=? AND deleted_at IS NULL",
            (member_id, org),
        ).fetchone()
        if not member:
            raise ValueError("member outside tenant")

    def create_meeting(self, org, actor, title, starts_at, location, **kw):
        self._require_member(org, actor)
        meeting_id, created_at = uid(), now()
        self.execute(
            "INSERT INTO meetings(id,organisation_id,title,starts_at,location,status,recurrence_rule,"
            "quorum_percent,video_provider,video_metadata,created_by,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (meeting_id, org, title, starts_at, location, "draft", kw.get("recurrence_rule"),
             kw.get("quorum_percent", 50), kw.get("video_provider"),
             json.dumps(kw.get("video_metadata", {})), actor, created_at, created_at),
        )
        self.conn.commit()
        self.audit(org, actor, "meeting.created", "meeting", meeting_id)
        return meeting_id

    def transition_meeting(self, org, actor, meeting_id, status):
        if status not in {"draft", "scheduled", "published", "completed", "cancelled"}:
            raise ValueError("invalid lifecycle status")
        self._require_meeting(org, meeting_id)
        self.execute(
            "UPDATE meetings SET status=?,updated_at=? WHERE id=? AND organisation_id=?",
            (status, now(), meeting_id, org),
        )
        self.conn.commit()
        self.audit(org, actor, "meeting.lifecycle_changed", "meeting", meeting_id, {"status": status})

    def add_agenda(self, org, actor, meeting_id, title, position, parent_id=None, metadata=None):
        self._require_meeting(org, meeting_id)
        if parent_id and not self.execute(
            "SELECT 1 FROM agenda_items WHERE id=? AND meeting_id=? AND organisation_id=? "
            "AND deleted_at IS NULL", (parent_id, meeting_id, org)
        ).fetchone():
            raise ValueError("agenda parent outside tenant/meeting")
        agenda_id, created_at = uid(), now()
        self.execute(
            "INSERT INTO agenda_items VALUES(?,?,?,?,?,?,?,?,?,?)",
            (agenda_id, org, meeting_id, parent_id, title, json.dumps(metadata or {}), position,
             created_at, created_at, None),
        )
        self.conn.commit()
        self.audit(org, actor, "agenda.created", "agenda_item", agenda_id)
        return agenda_id

    def reorder_agenda(self, org, actor, meeting_id, ordered_ids):
        self._require_meeting(org, meeting_id)
        for position, agenda_id in enumerate(ordered_ids):
            if not self.execute(
                "SELECT 1 FROM agenda_items WHERE id=? AND meeting_id=? AND organisation_id=? "
                "AND deleted_at IS NULL", (agenda_id, meeting_id, org)
            ).fetchone():
                raise ValueError("agenda item outside tenant/meeting")
            self.execute(
                "UPDATE agenda_items SET position=?,updated_at=? WHERE id=? AND organisation_id=?",
                (position, now(), agenda_id, org),
            )
        self.conn.commit()
        self.audit(org, actor, "agenda.reordered", "meeting", meeting_id, {"order": ordered_ids})

    def assign_attendee(self, org, actor, meeting_id, member_id, observer=False):
        self._require_meeting(org, meeting_id)
        self._require_member(org, member_id)
        self.execute(
            "INSERT INTO meeting_attendees VALUES(?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(meeting_id,member_id) DO UPDATE SET observer=excluded.observer,"
            "updated_at=excluded.updated_at",
            (uid(), org, meeting_id, member_id, "pending", int(observer), now(), now(), None),
        )
        self.conn.commit()
        self.audit(org, actor, "meeting.participant_assigned", "meeting", meeting_id,
                   {"member_id": member_id, "observer": observer})

    def rsvp(self, org, actor, meeting_id, member_id, response):
        if response not in {"yes", "no", "maybe"}:
            raise ValueError("invalid RSVP")
        self._require_meeting(org, meeting_id)
        self._require_member(org, member_id)
        timestamp = now()
        self.execute(
            "INSERT INTO meeting_rsvps VALUES(?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(meeting_id,member_id) DO UPDATE SET response=excluded.response,"
            "responded_at=excluded.responded_at,updated_at=excluded.updated_at",
            (uid(), org, meeting_id, member_id, response, timestamp, timestamp, timestamp, None),
        )
        self.conn.commit()
        self.audit(org, actor, "meeting.rsvp_updated", "meeting", meeting_id,
                   {"member_id": member_id, "response": response})

    def attendance(self, org, actor, meeting_id, member_id, status):
        if status not in {"present", "absent", "apology"}:
            raise ValueError("invalid attendance")
        self._require_meeting(org, meeting_id)
        self._require_member(org, member_id)
        result = self.execute(
            "UPDATE meeting_attendees SET status=?,updated_at=? "
            "WHERE meeting_id=? AND member_id=? AND organisation_id=? AND deleted_at IS NULL",
            (status, now(), meeting_id, member_id, org),
        )
        if result.rowcount != 1:
            raise ValueError("member is not assigned to meeting")
        self.conn.commit()
        self.audit(org, actor, "meeting.attendance_updated", "meeting", meeting_id,
                   {"member_id": member_id, "status": status})

    def quorum(self, org, meeting_id):
        meeting = self._require_meeting(org, meeting_id)
        eligible = self.execute(
            "SELECT count(*) n FROM meeting_attendees WHERE meeting_id=? AND organisation_id=? "
            "AND observer=0 AND deleted_at IS NULL", (meeting_id, org),
        ).fetchone()["n"]
        present = self.execute(
            "SELECT count(*) n FROM meeting_attendees WHERE meeting_id=? AND organisation_id=? "
            "AND observer=0 AND status='present' AND deleted_at IS NULL", (meeting_id, org),
        ).fetchone()["n"]
        required = (eligible * meeting["quorum_percent"] + 99) // 100
        return {"eligible": eligible, "present": present, "required": required,
                "met": present * 100 >= eligible * meeting["quorum_percent"]}

    def add_document(self, org, actor, title, content, meeting=None, agenda=None, classification="internal"):
        import hashlib
        if meeting:
            self._require_meeting(org, meeting)
        if agenda and not self.execute(
            "SELECT 1 FROM agenda_items WHERE id=? AND organisation_id=? "
            "AND deleted_at IS NULL" + (" AND meeting_id=?" if meeting else ""),
            (agenda, org, meeting) if meeting else (agenda, org),
        ).fetchone():
            raise ValueError("agenda item outside tenant/meeting")
        document_id, version_id, created_at = uid(), uid(), now()
        self.execute(
            "INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (document_id, org, meeting, agenda, title, classification, "draft", actor,
             created_at, created_at, None),
        )
        self.execute(
            "INSERT INTO document_versions VALUES(?,?,?,?,?,?,?,?,?)",
            (version_id, org, document_id, 1, "inline/" + version_id,
             hashlib.sha256(content).hexdigest(), len(content), actor, created_at),
        )
        self.conn.commit()
        self.audit(org, actor, "document.uploaded", "document", document_id)
        return document_id

    def replace_document(self, org, actor, document_id, content):
        import hashlib
        if not self.execute(
            "SELECT 1 FROM documents WHERE id=? AND organisation_id=? AND deleted_at IS NULL",
            (document_id, org),
        ).fetchone():
            raise ValueError("document outside tenant")
        row = self.execute(
            "SELECT coalesce(max(version_number),0)+1 n FROM document_versions "
            "WHERE document_id=? AND organisation_id=?", (document_id, org),
        ).fetchone()
        version_id = uid()
        self.execute(
            "INSERT INTO document_versions VALUES(?,?,?,?,?,?,?,?,?)",
            (version_id, org, document_id, row["n"], "inline/" + version_id,
             hashlib.sha256(content).hexdigest(), len(content), actor, now()),
        )
        self.conn.commit()
        self.audit(org, actor, "document.replaced", "document", document_id,
                   {"version": row["n"]})
