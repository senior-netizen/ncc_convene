import hashlib
import json
import mimetypes
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMAS = tuple(sorted((Path(__file__).parent.parent / "migrations").glob("*.sql")))


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return str(uuid.uuid4())


class DuplicateVoteError(ValueError):
    """Raised when a member attempts to cast more than one vote on a motion."""


class StaleRevisionError(ValueError):
    """Raised when a workflow mutation targets a superseded paper revision."""


MEETING_TRANSITIONS = {
    "draft": ("scheduled", "cancelled"),
    "scheduled": ("published", "cancelled"),
    "published": ("completed", "cancelled"),
    "completed": (),
    "cancelled": (),
}


def allowed_meeting_transitions(status):
    return list(MEETING_TRANSITIONS.get(status, ()))


class Database:
    """Tenant-scoped persistence operations for the NCC Convene application."""

    def __init__(self, path=":memory:"):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        for schema in SCHEMAS:
            self.conn.executescript(schema.read_text())
        self._migrate_member_profiles()
        self._migrate_document_metadata()

    def close(self):
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def __del__(self):
        try:
            self.close()
        except (AttributeError, sqlite3.Error):
            pass

    def _migrate_member_profiles(self):
        """Add profile fields without rebuilding members or losing governance data."""
        columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(member_profiles)")}
        additions = {
            "display_name": "TEXT",
            "email": "TEXT",
            "profile_image_url": "TEXT",
            "term_starts_on": "TEXT",
            "term_ends_on": "TEXT",
            "deactivated_at": "TEXT",
        }
        for name, definition in additions.items():
            if name not in columns:
                self.conn.execute(f"ALTER TABLE member_profiles ADD COLUMN {name} {definition}")
        self.conn.commit()

    def _migrate_document_metadata(self):
        columns = {row['name'] for row in self.conn.execute('PRAGMA table_info(document_versions)')}
        if 'content_type' not in columns:
            self.conn.execute('ALTER TABLE document_versions ADD COLUMN content_type TEXT NOT NULL DEFAULT \'application/octet-stream\'')
        self.conn.commit()

    @staticmethod
    def _safe_upload_path(org, resource_id, generated_id=None):
        root = Path(os.getenv('APP_UPLOAD_DIR', '.data/uploads')).resolve()
        generated_id = generated_id or uid()
        path = (root / str(org) / str(resource_id) / str(generated_id)).resolve()
        if root not in path.parents or path == root:
            raise ValueError('invalid upload storage path')
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _validate_upload(content, content_type=None):
        max_bytes = int(os.getenv('APP_MAX_UPLOAD_BYTES', str(20 * 1024 * 1024)))
        if not isinstance(content, (bytes, bytearray)) or not content:
            raise ValueError('upload content is required')
        if len(content) > max_bytes:
            raise ValueError('upload exceeds maximum size')
        allowed_types = {'application/pdf', 'application/octet-stream', 'text/plain'}
        if content_type and content_type not in allowed_types:
            raise ValueError('unsupported upload content type')
        return bytes(content)

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

    def conference_eligible(self, org, meeting_id, member_id):
        """Eligibility is assignment or an explicit tenant-scoped operator role."""
        self._require_meeting(org, meeting_id)
        member = self.execute("SELECT status FROM members WHERE id=? AND organisation_id=? AND deleted_at IS NULL", (member_id, org)).fetchone()
        if not member or member["status"] != "active":
            return False
        assigned = self.execute("SELECT 1 FROM meeting_attendees WHERE organisation_id=? AND meeting_id=? AND member_id=? AND deleted_at IS NULL", (org, meeting_id, member_id)).fetchone()
        return bool(assigned or self.roles(member_id, org) & {"Super Admin", "Organisation Admin", "Secretariat"})

    def start_conference(self, org, actor, meeting_id, admission_required=True):
        self._require_meeting(org, meeting_id)
        existing = self.execute("SELECT * FROM conference_sessions WHERE organisation_id=? AND meeting_id=? AND state!='ended' ORDER BY created_at DESC LIMIT 1", (org, meeting_id)).fetchone()
        if existing:
            return dict(existing)
        session_id, timestamp = uid(), now()
        # Provider names contain no meaningful tenant or meeting identifier.
        room = "ncc-" + uuid.uuid4().hex
        self.execute("INSERT INTO conference_sessions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (session_id, org, meeting_id, "livekit", room, "active", int(admission_required), 0, actor, timestamp, timestamp, None, timestamp))
        self.conn.commit(); self.audit(org, actor, "conference.started", "conference_session", session_id, {"meeting_id": meeting_id})
        return dict(self.conference(org, meeting_id, session_id))

    def conference(self, org, meeting_id, session_id=None):
        sql = "SELECT * FROM conference_sessions WHERE organisation_id=? AND meeting_id=?"
        params = [org, meeting_id]
        if session_id: sql += " AND id=?"; params.append(session_id)
        else: sql += " ORDER BY created_at DESC LIMIT 1"
        return self.execute(sql, params).fetchone()

    def conference_participant(self, org, meeting_id, session_id, member_id):
        return self.execute("SELECT * FROM conference_participants WHERE organisation_id=? AND meeting_id=? AND conference_session_id=? AND member_id=?", (org, meeting_id, session_id, member_id)).fetchone()

    def request_admission(self, org, actor, meeting_id, session_id):
        conference = self.conference(org, meeting_id, session_id)
        if not conference or conference["state"] != "active" or not self.conference_eligible(org, meeting_id, actor): raise ValueError("conference entry is not available")
        current = self.conference_participant(org, meeting_id, session_id, actor)
        if current and current["blocked"]: raise ValueError("participant is blocked")
        if conference["locked"] and not (current and current["admission_state"] == "admitted"):
            raise ValueError("conference entry is locked")
        moderator = bool(self.roles(actor, org) & {"Super Admin", "Organisation Admin", "Secretariat", "Chairperson"})
        state = "admitted" if moderator or not conference["admission_required"] else "waiting"
        timestamp = now()
        self.execute("INSERT INTO conference_participants VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(conference_session_id,member_id) DO UPDATE SET admission_state=CASE WHEN blocked=1 OR admission_state='admitted' THEN admission_state ELSE excluded.admission_state END,updated_at=excluded.updated_at", (uid(), org, meeting_id, session_id, actor, state, 0, int(moderator), int(moderator), 0, None, timestamp, timestamp if state == "admitted" else None, None, timestamp))
        self.conn.commit(); self.audit(org, actor, "conference.admission_requested", "conference_session", session_id, {"state": state})
        return dict(self.conference_participant(org, meeting_id, session_id, actor))

    def moderate_participant(self, org, actor, meeting_id, session_id, target, action):
        conference = self.conference(org, meeting_id, session_id)
        moderator = self.conference_participant(org, meeting_id, session_id, actor)
        participant = self.conference_participant(org, meeting_id, session_id, target)
        if not conference or not moderator or not moderator["can_moderate"]: raise PermissionError("conference moderation denied")
        if not participant: raise ValueError("participant outside conference")
        timestamp = now()
        mapping = {"admit": ("admission_state='admitted',admitted_at=?", (timestamp,)), "reject": ("admission_state='rejected'", ()), "remove": ("blocked=1,removed_at=?", (timestamp,)), "grant_present": ("can_present=1", ()), "revoke_present": ("can_present=0", ()), "grant_moderate": ("can_moderate=1", ()), "revoke_moderate": ("can_moderate=0", ())}
        if action not in mapping: raise ValueError("unsupported moderation action")
        fields, values = mapping[action]
        self.execute(f"UPDATE conference_participants SET {fields},updated_at=? WHERE organisation_id=? AND meeting_id=? AND conference_session_id=? AND member_id=?", (*values, timestamp, org, meeting_id, session_id, target))
        self.conn.commit(); self.audit(org, actor, f"conference.{action}", "conference_participant", participant["id"], {"session_id": session_id, "target_member_id": target})
        return dict(self.conference_participant(org, meeting_id, session_id, target))

    def set_conference_lock(self, org, actor, meeting_id, session_id, locked):
        result = self.execute("UPDATE conference_sessions SET locked=?,updated_at=? WHERE id=? AND organisation_id=? AND meeting_id=? AND state='active'", (int(locked), now(), session_id, org, meeting_id))
        if not result.rowcount: raise ValueError("active conference not found")
        self.conn.commit(); self.audit(org, actor, "conference.locked" if locked else "conference.unlocked", "conference_session", session_id)

    def end_conference(self, org, actor, meeting_id, session_id):
        timestamp = now(); result = self.execute("UPDATE conference_sessions SET state='ended',ended_at=?,updated_at=? WHERE id=? AND organisation_id=? AND meeting_id=? AND state!='ended'", (timestamp, timestamp, session_id, org, meeting_id))
        if not result.rowcount: raise ValueError("active conference not found")
        self.conn.commit(); self.audit(org, actor, "conference.ended", "conference_session", session_id, {"meeting_id": meeting_id})

    def list_members(self, org, search=None, status=None):
        """Return tenant-scoped member identity, contact, and term information."""
        sql = ("SELECT m.id,m.organisation_id,m.user_id,m.title,m.status,m.created_at,m.updated_at,"
               "COALESCE(p.display_name,u.display_name) AS display_name,"
               "COALESCE(p.email,u.email) AS email,p.phone,p.address,p.biography,"
               "p.profile_image_url,p.term_starts_on,p.term_ends_on,p.deactivated_at "
               "FROM members m LEFT JOIN users u ON u.id=m.user_id "
               "LEFT JOIN member_profiles p ON p.member_id=m.id AND p.organisation_id=m.organisation_id "
               "WHERE m.organisation_id=? AND m.deleted_at IS NULL")
        params = [org]
        if status:
            if status not in {"active", "inactive"}:
                raise ValueError("invalid member status")
            sql += " AND m.status=?"; params.append(status)
        if search:
            term = f"%{search}%"
            sql += " AND (COALESCE(p.display_name,u.display_name,'') LIKE ? OR COALESCE(p.email,u.email,'') LIKE ? OR COALESCE(m.title,'') LIKE ?)"
            params.extend((term, term, term))
        return [dict(row) for row in self.execute(sql + " ORDER BY display_name, m.id", params)]

    def member_profile(self, org, member_id):
        return next((member for member in self.list_members(org) if member["id"] == member_id), None)

    @staticmethod
    def _validate_member_profile(profile):
        if not isinstance(profile, dict):
            raise ValueError("profile must be an object")
        allowed = {key: profile[key] for key in ("display_name", "email", "phone", "address", "biography", "profile_image_url", "term_starts_on", "term_ends_on") if key in profile}
        for key, value in allowed.items():
            if value is not None and not isinstance(value, str):
                raise ValueError(f"invalid {key}")
        for key in ("term_starts_on", "term_ends_on"):
            if allowed.get(key):
                try:
                    datetime.fromisoformat(allowed[key]).date()
                except ValueError as exc:
                    raise ValueError(f"invalid {key}") from exc
        if allowed.get("term_starts_on") and allowed.get("term_ends_on") and allowed["term_starts_on"] > allowed["term_ends_on"]:
            raise ValueError("term end must not precede term start")
        return allowed

    def create_member(self, org, actor, email=None, display_name=None, title=None, profile=None, user_id=None):
        self._require_member(org, actor)
        profile = dict(profile or {})
        if display_name is not None:
            profile.setdefault("display_name", display_name)
        if email is not None:
            profile.setdefault("email", email)
        profile = self._validate_member_profile(profile)
        if not profile.get("display_name"):
            raise ValueError("display_name is required")
        if user_id is None and profile.get("email"):
            user = self.execute("SELECT id FROM users WHERE email=? AND deleted_at IS NULL", (profile["email"],)).fetchone()
            user_id = user["id"] if user else None
        elif user_id is not None and not self.execute("SELECT 1 FROM users WHERE id=? AND deleted_at IS NULL", (user_id,)).fetchone():
            raise ValueError("unknown user")
        timestamp, member_id = now(), uid()
        try:
            self.execute("INSERT INTO members(id,organisation_id,user_id,title,status,created_at,updated_at,deleted_at) VALUES(?,?,?,?,?,?,?,NULL)",
                         (member_id, org, user_id, title, "active", timestamp, timestamp))
        except sqlite3.IntegrityError as exc:
            raise ValueError("user is already a member of this organisation") from exc
        self.execute("INSERT INTO member_profiles(member_id,organisation_id,display_name,email,phone,address,biography,profile_image_url,term_starts_on,term_ends_on,deactivated_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (member_id, org, profile["display_name"], profile.get("email"), profile.get("phone"), profile.get("address"), profile.get("biography"), profile.get("profile_image_url"), profile.get("term_starts_on"), profile.get("term_ends_on"), None, timestamp, timestamp))
        self.conn.commit(); self.audit(org, actor, "member.created", "member", member_id, {"user_id": user_id})
        return member_id

    def update_member(self, org, actor, member_id, fields):
        self._require_member(org, actor); self._require_member(org, member_id)
        if not isinstance(fields, dict): raise ValueError("member fields must be an object")
        changed = False
        if "title" in fields:
            self.execute("UPDATE members SET title=?,updated_at=? WHERE id=? AND organisation_id=?", (fields["title"], now(), member_id, org)); changed = True
        profile = dict(fields.get("profile", {}))
        profile.update({key: fields[key] for key in ("display_name", "email", "phone", "address", "biography", "profile_image_url", "term_starts_on", "term_ends_on") if key in fields})
        if profile:
            allowed = self._validate_member_profile(profile)
            existing = self.member_profile(org, member_id)
            start = allowed.get("term_starts_on", existing["term_starts_on"])
            end = allowed.get("term_ends_on", existing["term_ends_on"])
            if start and end and start > end: raise ValueError("term end must not precede term start")
            profile_exists = self.execute(
                "SELECT 1 FROM member_profiles WHERE member_id=? AND organisation_id=?", (member_id, org)
            ).fetchone()
            if profile_exists:
                assignments = ",".join(f"{key}=?" for key in allowed)
                self.execute(f"UPDATE member_profiles SET {assignments},updated_at=? WHERE member_id=? AND organisation_id=?", (*allowed.values(), now(), member_id, org))
            else:
                # Backfill a legacy membership on its first profile edit without
                # altering the membership or its governance/audit history.
                values = {key: allowed.get(key, existing.get(key)) for key in ("display_name", "email", "phone", "address", "biography", "profile_image_url", "term_starts_on", "term_ends_on")}
                self.execute("INSERT INTO member_profiles(member_id,organisation_id,display_name,email,phone,address,biography,profile_image_url,term_starts_on,term_ends_on,deactivated_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                             (member_id, org, values["display_name"], values["email"], values["phone"], values["address"], values["biography"], values["profile_image_url"], values["term_starts_on"], values["term_ends_on"], existing["deactivated_at"], now(), now()))
            changed = True
        if changed: self.conn.commit(); self.audit(org, actor, "member.updated", "member", member_id)
        return self.member_profile(org, member_id)

    def deactivate_member(self, org, actor, member_id):
        self._require_member(org, actor); self._require_member(org, member_id)
        timestamp = now()
        self.execute("UPDATE members SET status='inactive',updated_at=? WHERE id=? AND organisation_id=?", (timestamp, member_id, org))
        self.execute("UPDATE member_profiles SET deactivated_at=?,updated_at=? WHERE member_id=? AND organisation_id=?", (timestamp, timestamp, member_id, org))
        self.conn.commit(); self.audit(org, actor, "member.deactivated", "member", member_id, {"deactivated_at": timestamp})

    def assign_member_role(self, org, actor, member_id, role_id):
        self._require_member(org, actor); self._require_member(org, member_id)
        role = self.execute("SELECT 1 FROM roles WHERE id=? AND organisation_id=? AND deleted_at IS NULL", (role_id, org)).fetchone()
        if not role: raise ValueError("role outside tenant")
        self.execute("INSERT OR IGNORE INTO member_roles VALUES(?,?,?)", (member_id, role_id, now()))
        self.conn.commit(); self.audit(org, actor, "member.role_assigned", "member", member_id, {"role_id": role_id})

    def create_meeting(self, org, actor, title, starts_at, location, **kw):
        self._require_member(org, actor)
        self._validated_datetime(starts_at, "starts_at")
        committee_id = kw.get("committee_id")
        if committee_id and not self.execute("SELECT 1 FROM committees WHERE id=? AND organisation_id=? AND deleted_at IS NULL", (committee_id, org)).fetchone():
            raise ValueError("committee outside tenant")
        if not 1 <= kw.get("quorum_percent", 50) <= 100:
            raise ValueError("quorum percent must be between 1 and 100")
        meeting_id, created_at = uid(), now()
        self.execute(
            "INSERT INTO meetings(id,organisation_id,committee_id,title,starts_at,location,status,recurrence_rule,"
            "quorum_percent,video_provider,video_metadata,created_by,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (meeting_id, org, committee_id, title, starts_at, location, "draft", kw.get("recurrence_rule"),
             kw.get("quorum_percent", 50), kw.get("video_provider"),
             json.dumps(kw.get("video_metadata", {})), actor, created_at, created_at),
        )
        self.conn.commit()
        self.audit(org, actor, "meeting.created", "meeting", meeting_id)
        return meeting_id

    def transition_meeting(self, org, actor, meeting_id, status):
        self._require_member(org, actor)
        if status not in {"draft", "scheduled", "published", "completed", "cancelled"}:
            raise ValueError("invalid lifecycle status")
        meeting = self._require_meeting(org, meeting_id)
        if status not in MEETING_TRANSITIONS.get(meeting["status"], ()):
            raise ValueError("invalid lifecycle transition")
        self.execute(
            "UPDATE meetings SET status=?,updated_at=? WHERE id=? AND organisation_id=?",
            (status, now(), meeting_id, org),
        )
        self.conn.commit()
        self.audit(org, actor, "meeting.lifecycle_changed", "meeting", meeting_id, {"status": status})

    def add_agenda(self, org, actor, meeting_id, title, position, parent_id=None, metadata=None):
        self._require_member(org, actor)
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
        self._require_member(org, actor)
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
        self._require_member(org, actor)
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
        self._require_member(org, actor)
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
        self._require_member(org, actor)
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
            "SELECT count(*) n FROM meeting_attendees a WHERE meeting_id=? AND organisation_id=? "
            "AND observer=0 AND deleted_at IS NULL AND NOT EXISTS ("
            "SELECT 1 FROM conflict_declarations c WHERE c.organisation_id=? "
            "AND c.meeting_id=a.meeting_id AND c.member_id=a.member_id "
            "AND c.agenda_item_id IS NULL AND c.status IN ('recusal_required','recusal_approved') "
            "AND c.deleted_at IS NULL)", (meeting_id, org, org),
        ).fetchone()["n"]
        present = self.execute(
            "SELECT count(*) n FROM meeting_attendees a WHERE meeting_id=? AND organisation_id=? "
            "AND observer=0 AND status='present' AND deleted_at IS NULL AND NOT EXISTS ("
            "SELECT 1 FROM conflict_declarations c WHERE c.organisation_id=? "
            "AND c.meeting_id=a.meeting_id AND c.member_id=a.member_id "
            "AND c.agenda_item_id IS NULL AND c.status IN ('recusal_required','recusal_approved') "
            "AND c.deleted_at IS NULL)", (meeting_id, org, org),
        ).fetchone()["n"]
        # A meeting with no eligible attendees cannot be quorate.  Ceiling is
        # required so (for example) 50% of three members is two, not one.
        required = (eligible * meeting["quorum_percent"] + 99) // 100
        return {"eligible": eligible, "present": present, "required": required,
                "met": eligible > 0 and present >= required}

    def add_document(self, org, actor, title, content, meeting=None, agenda=None, classification="internal", content_type=None):
        self._require_member(org, actor)
        content = self._validate_upload(content, content_type)
        if meeting:
            self._require_meeting(org, meeting)
        if agenda and not self.execute(
            "SELECT 1 FROM agenda_items WHERE id=? AND organisation_id=? "
            "AND deleted_at IS NULL" + (" AND meeting_id=?" if meeting else ""),
            (agenda, org, meeting) if meeting else (agenda, org),
        ).fetchone():
            raise ValueError("agenda item outside tenant/meeting")
        document_id, version_id, created_at = uid(), uid(), now()
        storage_path = self._safe_upload_path(org, document_id, version_id)
        storage_path.write_bytes(bytes(content))
        self.execute(
            "INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (document_id, org, meeting, agenda, title, classification, "draft", actor,
             created_at, created_at, None),
        )
        self.execute(
            "INSERT INTO document_versions(id,organisation_id,document_id,version_number,storage_key,sha256,size_bytes,created_by,created_at,content_type) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (version_id, org, document_id, 1, str(storage_path),
             hashlib.sha256(content).hexdigest(), len(content), actor, created_at,
             content_type or 'application/octet-stream'),
        )
        self.conn.commit()
        self.audit(org, actor, "document.uploaded", "document", document_id)
        return document_id

    def replace_document(self, org, actor, document_id, content, content_type=None):
        self._require_member(org, actor)
        content = self._validate_upload(content, content_type)
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
        storage_path = self._safe_upload_path(org, document_id, version_id)
        storage_path.write_bytes(bytes(content))
        self.execute(
            "INSERT INTO document_versions(id,organisation_id,document_id,version_number,storage_key,sha256,size_bytes,created_by,created_at,content_type) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (version_id, org, document_id, row["n"], str(storage_path),
             hashlib.sha256(content).hexdigest(), len(content), actor, now(),
             content_type or 'application/octet-stream'),
        )
        self.conn.commit()
        self.audit(org, actor, "document.replaced", "document", document_id,
                   {"version": row["n"]})

    def create_board_paper(self, org, actor, meeting_id, agenda_id, fields, content,
                           classification="confidential", content_type="application/pdf"):
        """Create a draft paper using the existing immutable document store."""
        self._require_member(org, actor)
        meeting = self._require_meeting(org, meeting_id)
        self._agenda(org, meeting_id, agenda_id)
        if not meeting["committee_id"]:
            raise ValueError("paper meeting must belong to a committee")
        required = ("title", "purpose", "background", "recommendation", "implications")
        if not isinstance(fields, dict) or any(not str(fields.get(key, "")).strip() for key in required):
            raise ValueError("all paper template fields are required")
        document_id = self.add_document(org, actor, fields["title"].strip(), content, meeting_id,
                                        agenda_id, classification, content_type)
        revision = self.execute(
            "SELECT id FROM document_versions WHERE organisation_id=? AND document_id=? AND version_number=1",
            (org, document_id),
        ).fetchone()
        paper_id, timestamp = uid(), now()
        self.execute(
            "INSERT INTO board_papers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (paper_id, org, document_id, meeting_id, meeting["committee_id"], agenda_id, actor,
             *(fields[key].strip() for key in required), "draft", revision["id"], 1,
             timestamp, timestamp, None),
        )
        self.conn.commit()
        self.audit(org, actor, "paper.created", "board_paper", paper_id,
                   {"revision_id": revision["id"], "meeting_id": meeting_id, "agenda_item_id": agenda_id})
        return paper_id

    def board_paper(self, org, paper_id):
        row = self.execute(
            "SELECT p.*,v.version_number,v.sha256,v.size_bytes,v.content_type "
            "FROM board_papers p JOIN document_versions v ON v.id=p.current_revision_id "
            "AND v.organisation_id=p.organisation_id WHERE p.id=? AND p.organisation_id=? "
            "AND p.deleted_at IS NULL", (paper_id, org),
        ).fetchone()
        return dict(row) if row else None

    def list_board_papers(self, org, meeting_id=None, actor=None, reviewer_only=False):
        sql = ("SELECT p.*,v.version_number,v.sha256,v.size_bytes,v.content_type,"
               "a.title agenda_title,m.title meeting_title,r.deadline review_deadline,r.status review_status "
               "FROM board_papers p JOIN document_versions v ON v.id=p.current_revision_id "
               "AND v.organisation_id=p.organisation_id JOIN agenda_items a ON a.id=p.agenda_item_id "
               "AND a.organisation_id=p.organisation_id JOIN meetings m ON m.id=p.meeting_id "
               "AND m.organisation_id=p.organisation_id LEFT JOIN paper_review_assignments r "
               "ON r.paper_id=p.id AND r.revision_id=p.current_revision_id AND r.organisation_id=p.organisation_id ")
        params = []
        if actor:
            sql += "AND r.reviewer_member_id=? "
            params.append(actor)
        sql += "WHERE p.organisation_id=? AND p.deleted_at IS NULL "
        params.append(org)
        if meeting_id:
            sql += "AND p.meeting_id=? "
            params.append(meeting_id)
        if reviewer_only:
            sql += "AND r.id IS NOT NULL "
        return [dict(row) for row in self.execute(sql + "ORDER BY p.updated_at DESC,p.id", params)]

    def transition_board_paper(self, org, actor, paper_id, transition, expected_revision_id,
                               expected_lock_version, reason=None):
        """Apply a compare-and-swap transition bound to the exact binary revision."""
        self._require_member(org, actor)
        paper = self.board_paper(org, paper_id)
        if not paper:
            raise ValueError("paper outside tenant")
        if paper["current_revision_id"] != expected_revision_id or paper["lock_version"] != expected_lock_version:
            raise StaleRevisionError("paper changed; refresh before continuing")
        transitions = {
            "submit": ({"draft", "changes_requested"}, "submitted"),
            "start_review": ({"submitted", "resubmitted"}, "under_review"),
        }
        if transition not in transitions or paper["status"] not in transitions[transition][0]:
            raise ValueError(f"cannot {transition} paper from {paper['status']}")
        if transition == "submit" and paper["author_member_id"] != actor:
            raise PermissionError("only the author may submit this paper")
        target = "resubmitted" if transition == "submit" and paper["status"] == "changes_requested" else transitions[transition][1]
        timestamp = now()
        result = self.execute(
            "UPDATE board_papers SET status=?,lock_version=lock_version+1,updated_at=? "
            "WHERE id=? AND organisation_id=? AND current_revision_id=? AND lock_version=?",
            (target, timestamp, paper_id, org, expected_revision_id, expected_lock_version),
        )
        if result.rowcount != 1:
            self.conn.rollback(); raise StaleRevisionError("paper changed; refresh before continuing")
        self.conn.commit()
        self.audit(org, actor, f"paper.{target}", "board_paper", paper_id,
                   {"revision_id": expected_revision_id, "reason": reason})
        return self.board_paper(org, paper_id)

    def revise_board_paper(self, org, actor, paper_id, fields, content, expected_revision_id,
                           content_type="application/pdf"):
        paper = self.board_paper(org, paper_id)
        if not paper or paper["author_member_id"] != actor:
            raise PermissionError("only the tenant paper author may revise this paper")
        if paper["current_revision_id"] != expected_revision_id:
            raise StaleRevisionError("paper revision is stale")
        if paper["status"] not in {"draft", "changes_requested", "approved", "published"}:
            raise ValueError("paper cannot be revised in its current state")
        self.replace_document(org, actor, paper["document_id"], content, content_type)
        revision = self.execute(
            "SELECT id FROM document_versions WHERE organisation_id=? AND document_id=? ORDER BY version_number DESC LIMIT 1",
            (org, paper["document_id"]),
        ).fetchone()
        allowed_fields = ("title", "purpose", "background", "recommendation", "implications")
        values = {key: str(fields.get(key, paper[key])).strip() for key in allowed_fields}
        if any(not value for value in values.values()): raise ValueError("all paper template fields are required")
        self.execute("UPDATE paper_review_assignments SET status='superseded' WHERE organisation_id=? AND paper_id=? AND status='assigned'", (org, paper_id))
        self.execute(
            "UPDATE board_papers SET title=?,purpose=?,background=?,recommendation=?,implications=?,"
            "status='draft',current_revision_id=?,lock_version=lock_version+1,updated_at=? "
            "WHERE id=? AND organisation_id=? AND current_revision_id=?",
            (*values.values(), revision["id"], now(), paper_id, org, expected_revision_id),
        )
        self.conn.commit()
        self.audit(org, actor, "paper.revised", "board_paper", paper_id,
                   {"previous_revision_id": expected_revision_id, "revision_id": revision["id"]})
        return self.board_paper(org, paper_id)

    def assign_paper_reviewer(self, org, actor, paper_id, reviewer_id, deadline=None):
        paper = self.board_paper(org, paper_id)
        if not paper: raise ValueError("paper outside tenant")
        self._require_member(org, actor); self._require_member(org, reviewer_id)
        if paper["status"] not in {"submitted", "resubmitted", "under_review"}:
            raise ValueError("paper is not ready for review")
        if deadline: self._validated_datetime(deadline, "review deadline")
        assignment_id, timestamp = uid(), now()
        try:
            self.execute("INSERT INTO paper_review_assignments VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                         (assignment_id, org, paper_id, paper["current_revision_id"], reviewer_id,
                          deadline, "assigned", actor, timestamp, None, None))
        except sqlite3.IntegrityError as exc:
            raise ValueError("reviewer is already assigned to this revision") from exc
        self.execute("UPDATE board_papers SET status='under_review',lock_version=lock_version+1,updated_at=? WHERE id=? AND organisation_id=?", (timestamp, paper_id, org))
        self.conn.commit(); self.audit(org, actor, "paper.review_assigned", "board_paper", paper_id,
            {"revision_id": paper["current_revision_id"], "reviewer_member_id": reviewer_id, "deadline": deadline})
        return assignment_id

    def review_board_paper(self, org, actor, paper_id, decision, revision_id, reason):
        if decision not in {"changes_requested", "approved"}: raise ValueError("invalid review decision")
        if not str(reason or "").strip(): raise ValueError("review reason is required")
        paper = self.board_paper(org, paper_id)
        if not paper: raise ValueError("paper outside tenant")
        if paper["current_revision_id"] != revision_id: raise StaleRevisionError("cannot approve a superseded revision")
        assignment = self.execute("SELECT id,status FROM paper_review_assignments WHERE organisation_id=? AND paper_id=? AND revision_id=? AND reviewer_member_id=?", (org, paper_id, revision_id, actor)).fetchone()
        if not assignment or assignment["status"] != "assigned": raise PermissionError("active review assignment required")
        timestamp = now()
        self.execute("UPDATE paper_review_assignments SET status=?,decided_at=?,reason=? WHERE id=? AND organisation_id=?", (decision, timestamp, reason.strip(), assignment["id"], org))
        self.execute("UPDATE board_papers SET status=?,lock_version=lock_version+1,updated_at=? WHERE id=? AND organisation_id=? AND current_revision_id=?", (decision, timestamp, paper_id, org, revision_id))
        self.conn.commit(); self.audit(org, actor, f"paper.{decision}", "board_paper", paper_id,
            {"revision_id": revision_id, "reason": reason.strip(), "assignment_id": assignment["id"]})
        return self.board_paper(org, paper_id)

    def add_paper_comment(self, org, actor, paper_id, revision_id, body):
        paper = self.board_paper(org, paper_id)
        revision = self.execute("SELECT 1 FROM document_versions WHERE id=? AND document_id=? AND organisation_id=?", (revision_id, paper["document_id"] if paper else None, org)).fetchone()
        if not paper or not revision: raise ValueError("paper revision outside tenant")
        if not str(body or "").strip(): raise ValueError("comment is required")
        comment_id = uid(); self.execute("INSERT INTO paper_review_comments VALUES(?,?,?,?,?,?,?,NULL)", (comment_id, org, paper_id, revision_id, actor, body.strip(), now()))
        self.conn.commit(); self.audit(org, actor, "paper.comment_added", "board_paper", paper_id, {"revision_id": revision_id, "comment_id": comment_id})
        return comment_id

    def publish_board_pack(self, org, actor, meeting_id, paper_ids, label=None):
        self._require_member(org, actor); self._require_meeting(org, meeting_id)
        if not paper_ids or len(set(paper_ids)) != len(paper_ids): raise ValueError("select unique approved papers")
        papers = []
        for paper_id in paper_ids:
            paper = self.board_paper(org, paper_id)
            if not paper or paper["meeting_id"] != meeting_id or paper["status"] != "approved":
                raise ValueError("board pack may contain only approved papers for this meeting")
            papers.append(paper)
        number = self.execute("SELECT coalesce(max(edition_number),0)+1 n FROM board_pack_editions WHERE organisation_id=? AND meeting_id=?", (org, meeting_id)).fetchone()["n"]
        edition_id, timestamp = uid(), now()
        self.execute("INSERT INTO board_pack_editions VALUES(?,?,?,?,?,?,?)", (edition_id, org, meeting_id, number, label or f"Edition {number}", actor, timestamp))
        for position, paper in enumerate(papers, 1):
            self.execute("INSERT INTO board_pack_items VALUES(?,?,?,?,?,?)", (uid(), org, edition_id, paper["id"], paper["current_revision_id"], position))
            self.execute("UPDATE board_papers SET status='published',lock_version=lock_version+1,updated_at=? WHERE id=? AND organisation_id=?", (timestamp, paper["id"], org))
        self.conn.commit(); self.audit(org, actor, "board_pack.published", "board_pack", edition_id, {"meeting_id": meeting_id, "edition_number": number, "items": [{"paper_id": p["id"], "revision_id": p["current_revision_id"]} for p in papers]})
        return edition_id

    def add_annotation(self, org, actor, document_id, revision_id, kind, locator, body=None):
        if kind not in {"bookmark", "highlight", "note"} or not str(locator or "").strip(): raise ValueError("invalid annotation")
        revision = self.execute("SELECT 1 FROM document_versions WHERE organisation_id=? AND document_id=? AND id=?", (org, document_id, revision_id)).fetchone()
        if not revision: raise ValueError("document revision outside tenant")
        annotation_id, timestamp = uid(), now()
        self.execute("INSERT INTO document_annotations VALUES(?,?,?,?,?,?,?,?,?,?,?,NULL)", (annotation_id, org, document_id, revision_id, actor, kind, locator.strip(), body, 0, timestamp, timestamp))
        self.conn.commit(); self.audit(org, actor, "annotation.created", "document_annotation", annotation_id, {"revision_id": revision_id, "kind": kind})
        return annotation_id

    def _agenda(self, org, meeting_id, agenda_id):
        row = self.execute("SELECT 1 FROM agenda_items WHERE id=? AND meeting_id=? "
                           "AND organisation_id=? AND deleted_at IS NULL",
                           (agenda_id, meeting_id, org)).fetchone()
        if not row:
            raise ValueError("agenda item outside tenant/meeting")

    @staticmethod
    def _validated_datetime(value, field="date"):
        """Require an ISO-8601 timestamp with an explicit timezone."""
        if not isinstance(value, str) or not value:
            raise ValueError(f"invalid {field}")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"invalid {field}") from exc
        if parsed.tzinfo is None:
            raise ValueError(f"invalid {field}")
        return parsed

    def _next_number(self, sequence_table, org, year):
        """Atomically allocate the next organisation/year number in SQLite."""
        return self.execute(
            f"INSERT INTO {sequence_table}(organisation_id,year,last_number) VALUES(?,?,1) "
            "ON CONFLICT(organisation_id,year) DO UPDATE SET last_number=last_number+1 "
            "RETURNING last_number",
            (org, year),
        ).fetchone()["last_number"]

    def declare_conflict(self, org, actor, meeting_id, member_id, interest,
                         management_action, agenda_id=None):
        self._require_member(org, actor)
        self._require_meeting(org, meeting_id); self._require_member(org, member_id)
        if agenda_id: self._agenda(org, meeting_id, agenda_id)
        conflict_id, timestamp = uid(), now()
        self.execute("INSERT INTO conflict_declarations VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                     (conflict_id, org, meeting_id, agenda_id, member_id, interest,
                      management_action, "declared", timestamp, timestamp, None))
        self.conn.commit()
        self.audit(org, actor, "conflict.declared", "conflict", conflict_id,
                   {"meeting_id": meeting_id, "member_id": member_id, "agenda_item_id": agenda_id})
        return conflict_id

    def manage_conflict_recusal(self, org, actor, meeting_id, conflict_id, status):
        """Record the Chairperson or Secretariat's recusal decision for a conflict."""
        self._require_member(org, actor)
        if status not in {"recusal_required", "recusal_approved"}:
            raise ValueError("invalid recusal status")
        self._require_meeting(org, meeting_id)
        result = self.execute(
            "UPDATE conflict_declarations SET status=?,management_action=?,updated_at=? "
            "WHERE id=? AND meeting_id=? AND organisation_id=? AND deleted_at IS NULL",
            (status, status.replace("_", " "), now(), conflict_id, meeting_id, org),
        )
        if result.rowcount != 1:
            raise ValueError("conflict outside tenant/meeting")
        self.conn.commit()
        self.audit(org, actor, "conflict.recusal_managed", "conflict", conflict_id,
                   {"meeting_id": meeting_id, "status": status})

    def _vote_quorum(self, org, meeting_id, agenda_id):
        """Return quorum counts for a motion, excluding applicable recusals."""
        meeting = self._require_meeting(org, meeting_id)
        where = (
            " FROM meeting_attendees a WHERE a.meeting_id=? AND a.organisation_id=? "
            "AND a.observer=0 AND a.deleted_at IS NULL AND NOT EXISTS ("
            "SELECT 1 FROM conflict_declarations c WHERE c.organisation_id=? "
            "AND c.meeting_id=a.meeting_id AND c.member_id=a.member_id "
            "AND (c.agenda_item_id IS NULL OR c.agenda_item_id=?) "
            "AND c.status IN ('recusal_required','recusal_approved') AND c.deleted_at IS NULL)"
        )
        eligible = self.execute("SELECT count(*) n" + where,
                                (meeting_id, org, org, agenda_id)).fetchone()["n"]
        present = self.execute("SELECT count(*) n" + where + " AND a.status='present'",
                               (meeting_id, org, org, agenda_id)).fetchone()["n"]
        required = (eligible * meeting["quorum_percent"] + 99) // 100
        return {"eligible": eligible, "present": present, "required": required,
                "met": eligible > 0 and present >= required}

    def create_motion(self, org, actor, meeting_id, agenda_id, proposer_id, text):
        self._require_member(org, actor)
        self._require_meeting(org, meeting_id); self._agenda(org, meeting_id, agenda_id)
        self._require_member(org, proposer_id)
        attendee = self.execute("SELECT 1 FROM meeting_attendees WHERE meeting_id=? AND member_id=? "
                                "AND organisation_id=? AND observer=0 AND deleted_at IS NULL",
                                (meeting_id, proposer_id, org)).fetchone()
        if not attendee: raise ValueError("proposer is not an eligible attendee")
        motion_id, timestamp = uid(), now()
        self.execute("INSERT INTO motions VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (motion_id, org, meeting_id, agenda_id, proposer_id, text, "open",
                      timestamp, timestamp, None))
        self.conn.commit(); self.audit(org, actor, "motion.created", "motion", motion_id,
                                       {"meeting_id": meeting_id, "agenda_item_id": agenda_id})
        return motion_id

    def cast_vote(self, org, actor, meeting_id, motion_id, member_id, choice):
        self._require_member(org, actor)
        if choice not in {"for", "against", "abstain"}: raise ValueError("invalid vote")
        motion = self.execute("SELECT * FROM motions WHERE id=? AND meeting_id=? AND organisation_id=? "
                              "AND deleted_at IS NULL", (motion_id, meeting_id, org)).fetchone()
        if not motion: raise ValueError("motion outside tenant/meeting")
        if motion["status"] != "open": raise ValueError("motion is not open for voting")
        eligible = self.execute(
            "SELECT 1 FROM meeting_attendees a WHERE a.meeting_id=? AND a.member_id=? "
            "AND a.organisation_id=? AND a.status='present' AND a.observer=0 AND a.deleted_at IS NULL "
            "AND NOT EXISTS (SELECT 1 FROM conflict_declarations c WHERE c.organisation_id=? "
            "AND c.meeting_id=a.meeting_id AND c.member_id=a.member_id "
            "AND (c.agenda_item_id IS NULL OR c.agenda_item_id=?) "
            "AND c.status IN ('recusal_required','recusal_approved') AND c.deleted_at IS NULL)",
            (meeting_id, member_id, org, org, motion["agenda_item_id"]),
        ).fetchone()
        if not eligible: raise ValueError("only present, non-recused, non-observer attendees may vote")
        timestamp = now()
        try:
            self.execute("INSERT INTO votes VALUES(?,?,?,?,?,?,?)",
                         (uid(), org, motion_id, member_id, choice, timestamp, timestamp))
        except sqlite3.IntegrityError as exc:
            if "votes.motion_id, votes.member_id" in str(exc):
                raise DuplicateVoteError("a vote has already been cast for this motion") from exc
            raise
        self.conn.commit(); self.audit(org, actor, "vote.cast", "motion", motion_id,
                                       {"member_id": member_id, "choice": choice})

    def vote_tally(self, org, meeting_id, motion_id):
        motion = self.execute("SELECT * FROM motions WHERE id=? AND meeting_id=? AND organisation_id=? "
                              "AND deleted_at IS NULL", (motion_id, meeting_id, org)).fetchone()
        if not motion: raise ValueError("motion outside tenant/meeting")
        counts = {row["choice"]: row["n"] for row in self.execute(
            "SELECT v.choice,count(*) n FROM votes v WHERE v.motion_id=? AND v.organisation_id=? "
            "AND NOT EXISTS (SELECT 1 FROM conflict_declarations c WHERE c.organisation_id=? "
            "AND c.meeting_id=? AND c.member_id=v.member_id "
            "AND (c.agenda_item_id IS NULL OR c.agenda_item_id=?) "
            "AND c.status IN ('recusal_required','recusal_approved') AND c.deleted_at IS NULL) "
            "GROUP BY v.choice",
            (motion_id, org, org, meeting_id, motion["agenda_item_id"]))}
        tally = {choice: counts.get(choice, 0) for choice in ("for", "against", "abstain")}
        quorum = self._vote_quorum(org, meeting_id, motion["agenda_item_id"])
        tally.update({"eligible": quorum["eligible"], "quorum_met": quorum["met"],
                      "passed": quorum["met"] and tally["for"] > tally["against"]})
        return tally

    def close_motion(self, org, actor, meeting_id, motion_id):
        self._require_member(org, actor)
        tally = self.vote_tally(org, meeting_id, motion_id)
        result = self.execute("UPDATE motions SET status='closed',updated_at=? WHERE id=? AND meeting_id=? "
                              "AND organisation_id=? AND status='open'", (now(), motion_id, meeting_id, org))
        if result.rowcount != 1: raise ValueError("motion is not open for voting")
        self.conn.commit(); self.audit(org, actor, "motion.closed", "motion", motion_id, tally)
        return tally

    def create_resolution(self, org, actor, meeting_id, agenda_id, text, outcome, motion_id=None,
                          status="approved"):
        self._require_member(org, actor)
        if outcome not in {"carried", "not_carried", "noted"}: raise ValueError("invalid resolution outcome")
        if status not in {"draft", "approved", "published"}: raise ValueError("invalid resolution status")
        meeting = self._require_meeting(org, meeting_id); self._agenda(org, meeting_id, agenda_id)
        if motion_id:
            motion = self.execute("SELECT agenda_item_id,status FROM motions WHERE id=? AND meeting_id=? "
                                  "AND organisation_id=? AND deleted_at IS NULL", (motion_id, meeting_id, org)).fetchone()
            if not motion or motion["agenda_item_id"] != agenda_id or motion["status"] != "closed":
                raise ValueError("resolution must reference a closed motion on the same agenda item")
            if (outcome == "carried") != self.vote_tally(org, meeting_id, motion_id)["passed"]:
                raise ValueError("resolution outcome does not match vote result")
            if outcome == "carried" and self.execute(
                "SELECT 1 FROM resolutions WHERE motion_id=? AND organisation_id=? "
                "AND outcome='carried' AND deleted_at IS NULL", (motion_id, org)
            ).fetchone():
                raise ValueError("a carried resolution already exists for this motion")
        year = self._validated_datetime(meeting["starts_at"], "meeting starts_at").year
        resolution_id, timestamp = uid(), now()
        number = self._next_number("resolution_number_sequences", org, year)
        try:
            self.execute("INSERT INTO resolutions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (resolution_id, org, meeting_id, agenda_id, motion_id, year, number,
                          text, outcome, status, timestamp, timestamp, None))
        except sqlite3.IntegrityError as exc:
            if motion_id and outcome == "carried":
                raise ValueError("a carried resolution already exists for this motion") from exc
            raise
        self.conn.commit(); self.audit(org, actor, "resolution.created", "resolution", resolution_id,
                                       {"meeting_id": meeting_id, "agenda_item_id": agenda_id, "motion_id": motion_id,
                                        "year": year, "number": number})
        return resolution_id

    def save_minutes(self, org, actor, meeting_id, agenda_id, body, status="draft"):
        self._require_member(org, actor)
        if status not in {"draft", "in_review", "approved"}: raise ValueError("invalid minutes status")
        self._require_meeting(org, meeting_id); self._agenda(org, meeting_id, agenda_id)
        timestamp = now(); existing = self.execute("SELECT id FROM minutes WHERE meeting_id=? AND agenda_item_id=? "
                                                   "AND organisation_id=? AND deleted_at IS NULL",
                                                   (meeting_id, agenda_id, org)).fetchone()
        if existing:
            self.execute("UPDATE minutes SET body=?,status=?,updated_at=? WHERE id=? AND organisation_id=?",
                         (body, status, timestamp, existing["id"], org)); minute_id = existing["id"]; event = "minutes.updated"
        else:
            minute_id = uid(); event = "minutes.created"
            self.execute("INSERT INTO minutes VALUES(?,?,?,?,?,?,?,?,?,?)", (minute_id, org, meeting_id,
                         agenda_id, body, status, actor, timestamp, timestamp, None))
        self.conn.commit(); self.audit(org, actor, event, "minutes", minute_id,
                                       {"meeting_id": meeting_id, "agenda_item_id": agenda_id, "status": status})
        return minute_id

    def add_minute_item(self, org, actor, minute_id, item_type, body, position, meeting_id=None):
        self._require_member(org, actor)
        if item_type not in {"discussion", "decision", "action", "note"}:
            raise ValueError("invalid minute item type")
        minute = self.execute("SELECT 1 FROM minutes WHERE id=? AND organisation_id=? AND deleted_at IS NULL" +
                              (" AND meeting_id=?" if meeting_id else ""),
                              (minute_id, org, meeting_id) if meeting_id else (minute_id, org)).fetchone()
        if not minute: raise ValueError("minutes outside tenant")
        if not isinstance(body, str) or not body or not isinstance(position, int) or position < 0:
            raise ValueError("invalid minute item")
        item_id, timestamp = uid(), now()
        self.execute("INSERT INTO minute_items VALUES(?,?,?,?,?,?,?,?,?,NULL)",
                     (item_id, org, minute_id, item_type, body, position, actor, timestamp, timestamp))
        self.conn.commit(); self.audit(org, actor, "minute_item.created", "minute_item", item_id,
                                       {"minute_id": minute_id, "item_type": item_type})
        return item_id

    def create_action(self, org, actor, meeting_id, agenda_id, owner_id, description, due_at=None,
                      resolution_id=None, priority="normal"):
        self._require_member(org, actor)
        meeting = self._require_meeting(org, meeting_id); self._agenda(org, meeting_id, agenda_id); self._require_member(org, owner_id)
        if priority not in {"low", "normal", "high", "critical"}: raise ValueError("invalid action priority")
        if due_at is not None: self._validated_datetime(due_at, "due_at")
        if resolution_id and not self.execute("SELECT 1 FROM resolutions WHERE id=? AND meeting_id=? AND agenda_item_id=? "
                                               "AND organisation_id=? AND deleted_at IS NULL", (resolution_id, meeting_id, agenda_id, org)).fetchone():
            raise ValueError("resolution outside tenant/meeting/agenda")
        year = self._validated_datetime(meeting["starts_at"], "meeting starts_at").year
        action_id, timestamp = uid(), now(); number = self._next_number("action_number_sequences", org, year)
        self.execute("INSERT INTO actions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (action_id, org, meeting_id,
                     agenda_id, resolution_id, year, number, owner_id, description, priority, due_at, "open", timestamp, timestamp, None, None))
        self.conn.commit(); self.audit(org, actor, "action.created", "action", action_id,
                                       {"owner_member_id": owner_id, "agenda_item_id": agenda_id, "year": year, "number": number})
        return action_id

    def add_action_update(self, org, actor, action_id, body, status=None):
        self._require_member(org, actor)
        if status is not None and status not in {"open", "in_progress", "completed", "cancelled"}:
            raise ValueError("invalid action status")
        if not isinstance(body, str) or not body: raise ValueError("action update body is required")
        action = self.execute("SELECT status FROM actions WHERE id=? AND organisation_id=? AND deleted_at IS NULL",
                              (action_id, org)).fetchone()
        if not action: raise ValueError("action outside tenant")
        if action["status"] in {"completed", "cancelled"} and status not in {None, action["status"]}:
            raise ValueError("action lifecycle is final")
        transitions = {"open": {"in_progress", "cancelled"},
                       "in_progress": {"open", "completed", "cancelled"},
                       "completed": set(), "cancelled": set()}
        if status and status != action["status"] and status not in transitions[action["status"]]:
            raise ValueError("invalid action lifecycle transition")
        if status == "completed" and not self.execute(
            "SELECT 1 FROM completion_evidence WHERE action_id=? AND organisation_id=? AND deleted_at IS NULL",
            (action_id, org),
        ).fetchone():
            raise ValueError("completion evidence is required")
        update_id, timestamp = uid(), now()
        self.execute("INSERT INTO action_updates VALUES(?,?,?,?,?,?,?,NULL)",
                     (update_id, org, action_id, actor, body, status, timestamp))
        if status and status != action["status"]:
            completed_at = timestamp if status == "completed" else None
            self.execute("UPDATE actions SET status=?,completed_at=COALESCE(?,completed_at),updated_at=? "
                         "WHERE id=? AND organisation_id=?", (status, completed_at, timestamp, action_id, org))
        self.conn.commit(); self.audit(org, actor, "action.updated", "action_update", update_id,
                                       {"action_id": action_id, "status": status})
        return update_id

    def action_traceability(self, org, action_id=None, meeting_id=None):
        sql = "SELECT * FROM action_traceability WHERE organisation_id=?"
        params = [org]
        if action_id: sql += " AND id=?"; params.append(action_id)
        if meeting_id: sql += " AND meeting_id=?"; params.append(meeting_id)
        return [dict(row) for row in self.execute(sql + " ORDER BY action_year,action_number", params)]

    def resolution_traceability(self, org, resolution_id=None, meeting_id=None):
        sql = "SELECT * FROM resolution_traceability WHERE organisation_id=?"
        params = [org]
        if resolution_id: sql += " AND id=?"; params.append(resolution_id)
        if meeting_id: sql += " AND meeting_id=?"; params.append(meeting_id)
        return [dict(row) for row in self.execute(sql + " ORDER BY resolution_year,resolution_number", params)]

    def add_completion_evidence(self, org, actor, action_id, note, storage_key=None):
        self._require_member(org, actor)
        action = self.execute("SELECT * FROM actions WHERE id=? AND organisation_id=? AND deleted_at IS NULL", (action_id, org)).fetchone()
        if not action: raise ValueError("action outside tenant")
        evidence_id, timestamp = uid(), now()
        self.execute("INSERT INTO completion_evidence VALUES(?,?,?,?,?,?,?,?)",
                     (evidence_id, org, action_id, actor, note, storage_key, timestamp, None))
        self.conn.commit(); self.audit(org, actor, "action.evidence_added", "completion_evidence", evidence_id,
                                       {"action_id": action_id})
        return evidence_id

    def complete_action(self, org, actor, action_id):
        self._require_member(org, actor)
        evidence = self.execute("SELECT 1 FROM completion_evidence WHERE action_id=? AND organisation_id=? "
                                "AND deleted_at IS NULL", (action_id, org)).fetchone()
        if not evidence: raise ValueError("completion evidence is required")
        result = self.execute("UPDATE actions SET status='completed',completed_at=?,updated_at=? WHERE id=? "
                              "AND organisation_id=? AND deleted_at IS NULL AND status IN ('open','in_progress')", (now(), now(), action_id, org))
        if result.rowcount != 1: raise ValueError("action is not open or in progress")
        self.conn.commit(); self.audit(org, actor, "action.completed", "action", action_id)
