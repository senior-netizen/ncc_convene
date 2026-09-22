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

    def list_members(self, org, search=None, status=None):
        """Return only members and profile data belonging to ``org``."""
        sql = ("SELECT m.id,m.organisation_id,m.user_id,m.title,m.status,m.created_at,m.updated_at,"
               "u.email,u.display_name,p.phone,p.address,p.biography "
               "FROM members m JOIN users u ON u.id=m.user_id "
               "LEFT JOIN member_profiles p ON p.member_id=m.id AND p.organisation_id=m.organisation_id "
               "WHERE m.organisation_id=? AND m.deleted_at IS NULL")
        params = [org]
        if status:
            if status not in {"active", "inactive"}:
                raise ValueError("invalid member status")
            sql += " AND m.status=?"; params.append(status)
        if search:
            term = f"%{search}%"
            sql += " AND (u.display_name LIKE ? OR u.email LIKE ? OR COALESCE(m.title,'') LIKE ?)"
            params.extend((term, term, term))
        return [dict(row) for row in self.execute(sql + " ORDER BY u.display_name, m.id", params)]

    def member_profile(self, org, member_id):
        members = self.list_members(org)
        return next((member for member in members if member["id"] == member_id), None)

    def create_member(self, org, actor, email, display_name, title=None, profile=None):
        self._require_member(org, actor)
        if not isinstance(email, str) or not email or not isinstance(display_name, str) or not display_name:
            raise ValueError("email and display_name are required")
        profile = profile or {}
        if not isinstance(profile, dict):
            raise ValueError("profile must be an object")
        user = self.execute("SELECT id FROM users WHERE email=? AND deleted_at IS NULL", (email,)).fetchone()
        timestamp = now()
        if user:
            user_id = user["id"]
        else:
            # A provisioned account cannot authenticate until its password is set.
            user_id = uid()
            self.execute("INSERT INTO users VALUES(?,?,?,?,?,?,NULL)",
                         (user_id, email, "!", display_name, timestamp, timestamp))
        member_id = uid()
        try:
            self.execute("INSERT INTO members VALUES(?,?,?,?,?,?,?,NULL)",
                         (member_id, org, user_id, title, "active", timestamp, timestamp))
        except sqlite3.IntegrityError as exc:
            raise ValueError("user is already a member of this organisation") from exc
        self.execute("INSERT INTO member_profiles VALUES(?,?,?,?,?,?,?)",
                     (member_id, org, profile.get("phone"), profile.get("address"),
                      profile.get("biography"), timestamp, timestamp))
        self.conn.commit(); self.audit(org, actor, "member.created", "member", member_id)
        return member_id

    def update_member(self, org, actor, member_id, fields):
        self._require_member(org, actor); self._require_member(org, member_id)
        if not isinstance(fields, dict): raise ValueError("member fields must be an object")
        member = self.member_profile(org, member_id)
        changed = False
        if "display_name" in fields:
            if not isinstance(fields["display_name"], str) or not fields["display_name"]: raise ValueError("invalid display_name")
            self.execute("UPDATE users SET display_name=?,updated_at=? WHERE id=?", (fields["display_name"], now(), member["user_id"])); changed = True
        if "title" in fields:
            self.execute("UPDATE members SET title=?,updated_at=? WHERE id=? AND organisation_id=?", (fields["title"], now(), member_id, org)); changed = True
        profile = fields.get("profile", {key: fields[key] for key in ("phone", "address", "biography") if key in fields})
        if profile:
            if not isinstance(profile, dict): raise ValueError("profile must be an object")
            allowed = {key: profile[key] for key in ("phone", "address", "biography") if key in profile}
            if allowed:
                assignments = ",".join(f"{key}=?" for key in allowed)
                self.execute(f"UPDATE member_profiles SET {assignments},updated_at=? WHERE member_id=? AND organisation_id=?", (*allowed.values(), now(), member_id, org)); changed = True
        if changed: self.conn.commit(); self.audit(org, actor, "member.updated", "member", member_id)
        return self.member_profile(org, member_id)

    def deactivate_member(self, org, actor, member_id):
        self._require_member(org, actor); self._require_member(org, member_id)
        self.execute("UPDATE members SET status='inactive',updated_at=? WHERE id=? AND organisation_id=?", (now(), member_id, org))
        self.conn.commit(); self.audit(org, actor, "member.deactivated", "member", member_id)

    def assign_member_role(self, org, actor, member_id, role_id):
        self._require_member(org, actor); self._require_member(org, member_id)
        role = self.execute("SELECT 1 FROM roles WHERE id=? AND organisation_id=? AND deleted_at IS NULL", (role_id, org)).fetchone()
        if not role: raise ValueError("role outside tenant")
        self.execute("INSERT OR IGNORE INTO member_roles VALUES(?,?,?)", (member_id, role_id, now()))
        self.conn.commit(); self.audit(org, actor, "member.role_assigned", "member", member_id, {"role_id": role_id})

    def create_meeting(self, org, actor, title, starts_at, location, **kw):
        self._require_member(org, actor)
        self._validated_datetime(starts_at, "starts_at")
        if not 1 <= kw.get("quorum_percent", 50) <= 100:
            raise ValueError("quorum percent must be between 1 and 100")
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
        meeting = self._require_meeting(org, meeting_id)
        transitions = {"draft": {"scheduled", "cancelled"}, "scheduled": {"published", "cancelled"},
                       "published": {"completed", "cancelled"}, "completed": set(), "cancelled": set()}
        if status not in transitions[meeting["status"]]:
            raise ValueError("invalid lifecycle transition")
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
        # A meeting with no eligible attendees cannot be quorate.  Ceiling is
        # required so (for example) 50% of three members is two, not one.
        required = (eligible * meeting["quorum_percent"] + 99) // 100
        return {"eligible": eligible, "present": present, "required": required,
                "met": eligible > 0 and present >= required}

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

    def create_motion(self, org, actor, meeting_id, agenda_id, proposer_id, text):
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
        if choice not in {"for", "against", "abstain"}: raise ValueError("invalid vote")
        motion = self.execute("SELECT * FROM motions WHERE id=? AND meeting_id=? AND organisation_id=? "
                              "AND deleted_at IS NULL", (motion_id, meeting_id, org)).fetchone()
        if not motion: raise ValueError("motion outside tenant/meeting")
        if motion["status"] != "open": raise ValueError("motion is not open for voting")
        eligible = self.execute("SELECT 1 FROM meeting_attendees WHERE meeting_id=? AND member_id=? "
                                "AND organisation_id=? AND status='present' AND observer=0 AND deleted_at IS NULL",
                                (meeting_id, member_id, org)).fetchone()
        if not eligible: raise ValueError("only present, non-observer attendees may vote")
        timestamp = now()
        self.execute("INSERT INTO votes VALUES(?,?,?,?,?,?,?) ON CONFLICT(motion_id,member_id) DO UPDATE SET "
                     "choice=excluded.choice,cast_at=excluded.cast_at,updated_at=excluded.updated_at",
                     (uid(), org, motion_id, member_id, choice, timestamp, timestamp))
        self.conn.commit(); self.audit(org, actor, "vote.cast", "motion", motion_id,
                                       {"member_id": member_id, "choice": choice})

    def vote_tally(self, org, meeting_id, motion_id):
        motion = self.execute("SELECT * FROM motions WHERE id=? AND meeting_id=? AND organisation_id=? "
                              "AND deleted_at IS NULL", (motion_id, meeting_id, org)).fetchone()
        if not motion: raise ValueError("motion outside tenant/meeting")
        counts = {row["choice"]: row["n"] for row in self.execute(
            "SELECT choice,count(*) n FROM votes WHERE motion_id=? AND organisation_id=? GROUP BY choice",
            (motion_id, org))}
        tally = {choice: counts.get(choice, 0) for choice in ("for", "against", "abstain")}
        quorum = self.quorum(org, meeting_id)
        tally.update({"eligible": quorum["eligible"], "quorum_met": quorum["met"],
                      "passed": quorum["met"] and tally["for"] > tally["against"]})
        return tally

    def close_motion(self, org, actor, meeting_id, motion_id):
        tally = self.vote_tally(org, meeting_id, motion_id)
        result = self.execute("UPDATE motions SET status='closed',updated_at=? WHERE id=? AND meeting_id=? "
                              "AND organisation_id=? AND status='open'", (now(), motion_id, meeting_id, org))
        if result.rowcount != 1: raise ValueError("motion is not open for voting")
        self.conn.commit(); self.audit(org, actor, "motion.closed", "motion", motion_id, tally)
        return tally

    def create_resolution(self, org, actor, meeting_id, agenda_id, text, outcome, motion_id=None,
                          status="approved"):
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

    def add_minute_item(self, org, actor, minute_id, item_type, body, position):
        if item_type not in {"discussion", "decision", "action", "note"}:
            raise ValueError("invalid minute item type")
        minute = self.execute("SELECT 1 FROM minutes WHERE id=? AND organisation_id=? AND deleted_at IS NULL",
                              (minute_id, org)).fetchone()
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

    def action_traceability(self, org, action_id=None):
        sql = "SELECT * FROM action_traceability WHERE organisation_id=?"
        params = [org]
        if action_id: sql += " AND id=?"; params.append(action_id)
        return [dict(row) for row in self.execute(sql + " ORDER BY action_year,action_number", params)]

    def resolution_traceability(self, org, resolution_id=None):
        sql = "SELECT * FROM resolution_traceability WHERE organisation_id=?"
        params = [org]
        if resolution_id: sql += " AND id=?"; params.append(resolution_id)
        return [dict(row) for row in self.execute(sql + " ORDER BY resolution_year,resolution_number", params)]

    def add_completion_evidence(self, org, actor, action_id, note, storage_key=None):
        action = self.execute("SELECT * FROM actions WHERE id=? AND organisation_id=? AND deleted_at IS NULL", (action_id, org)).fetchone()
        if not action: raise ValueError("action outside tenant")
        evidence_id, timestamp = uid(), now()
        self.execute("INSERT INTO completion_evidence VALUES(?,?,?,?,?,?,?,?)",
                     (evidence_id, org, action_id, actor, note, storage_key, timestamp, None))
        self.conn.commit(); self.audit(org, actor, "action.evidence_added", "completion_evidence", evidence_id,
                                       {"action_id": action_id})
        return evidence_id

    def complete_action(self, org, actor, action_id):
        evidence = self.execute("SELECT 1 FROM completion_evidence WHERE action_id=? AND organisation_id=? "
                                "AND deleted_at IS NULL", (action_id, org)).fetchone()
        if not evidence: raise ValueError("completion evidence is required")
        result = self.execute("UPDATE actions SET status='completed',completed_at=?,updated_at=? WHERE id=? "
                              "AND organisation_id=? AND deleted_at IS NULL AND status='open'", (now(), now(), action_id, org))
        if result.rowcount != 1: raise ValueError("action is not open")
        self.conn.commit(); self.audit(org, actor, "action.completed", "action", action_id)
