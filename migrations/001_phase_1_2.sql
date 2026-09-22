PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS organisations (id TEXT PRIMARY KEY, name TEXT NOT NULL, slug TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT);
-- Kept separate from organisations so this migration remains compatible with an
-- already-created base organisations table.  These fields are organisation-owned,
-- rather than being supplied by an untrusted request.
CREATE TABLE IF NOT EXISTS organisation_profiles (organisation_id TEXT PRIMARY KEY REFERENCES organisations(id), legal_name TEXT, registration_number TEXT, contact_email TEXT, contact_phone TEXT, address TEXT, website TEXT, logo_url TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_organisation_profiles_tenant ON organisation_profiles(organisation_id);
CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, display_name TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT);
-- A member may be an unprovisioned governance participant.  user_id is nullable
-- so account provisioning is not required to retain their membership history.
CREATE TABLE IF NOT EXISTS members (id TEXT PRIMARY KEY, organisation_id TEXT NOT NULL REFERENCES organisations(id), user_id TEXT REFERENCES users(id), title TEXT, status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT, UNIQUE(organisation_id,user_id));
-- Member identity/contact belongs to the organisation, not the optional user account.
-- Keeping it in a one-to-one table makes the added fields safe for deployments that
-- already have the original members table and preserves a member after deactivation.
CREATE TABLE IF NOT EXISTS member_profiles (member_id TEXT PRIMARY KEY REFERENCES members(id), organisation_id TEXT NOT NULL REFERENCES organisations(id), display_name TEXT NOT NULL, email TEXT, phone TEXT, address TEXT, biography TEXT, profile_image_url TEXT, term_starts_on TEXT, term_ends_on TEXT, deactivated_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_member_profiles_tenant ON member_profiles(organisation_id, member_id);
CREATE TABLE IF NOT EXISTS roles (id TEXT PRIMARY KEY, organisation_id TEXT REFERENCES organisations(id), name TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT, UNIQUE(organisation_id,name));
CREATE TABLE IF NOT EXISTS permissions (id TEXT PRIMARY KEY, code TEXT NOT NULL UNIQUE, description TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT);
CREATE TABLE IF NOT EXISTS member_roles (member_id TEXT NOT NULL REFERENCES members(id), role_id TEXT NOT NULL REFERENCES roles(id), created_at TEXT NOT NULL, PRIMARY KEY(member_id,role_id));
CREATE TABLE IF NOT EXISTS role_permissions (role_id TEXT NOT NULL REFERENCES roles(id), permission_id TEXT NOT NULL REFERENCES permissions(id), created_at TEXT NOT NULL, PRIMARY KEY(role_id,permission_id));
CREATE TABLE IF NOT EXISTS committees (id TEXT PRIMARY KEY, organisation_id TEXT NOT NULL REFERENCES organisations(id), name TEXT NOT NULL, description TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT);
CREATE TABLE IF NOT EXISTS committee_members (id TEXT PRIMARY KEY, organisation_id TEXT NOT NULL REFERENCES organisations(id), committee_id TEXT NOT NULL REFERENCES committees(id), member_id TEXT NOT NULL REFERENCES members(id), role TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT, UNIQUE(committee_id,member_id));
CREATE TABLE IF NOT EXISTS meetings (id TEXT PRIMARY KEY, organisation_id TEXT NOT NULL REFERENCES organisations(id), committee_id TEXT REFERENCES committees(id), title TEXT NOT NULL, starts_at TEXT NOT NULL, location TEXT, status TEXT NOT NULL DEFAULT 'draft', recurrence_rule TEXT, quorum_percent INTEGER NOT NULL DEFAULT 50, video_provider TEXT, video_metadata TEXT, created_by TEXT NOT NULL REFERENCES members(id), created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT);
CREATE TABLE IF NOT EXISTS meeting_attendees (id TEXT PRIMARY KEY, organisation_id TEXT NOT NULL REFERENCES organisations(id), meeting_id TEXT NOT NULL REFERENCES meetings(id), member_id TEXT NOT NULL REFERENCES members(id), status TEXT NOT NULL DEFAULT 'pending', observer INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT, UNIQUE(meeting_id,member_id));
CREATE TABLE IF NOT EXISTS meeting_rsvps (id TEXT PRIMARY KEY, organisation_id TEXT NOT NULL REFERENCES organisations(id), meeting_id TEXT NOT NULL REFERENCES meetings(id), member_id TEXT NOT NULL REFERENCES members(id), response TEXT NOT NULL, responded_at TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT, UNIQUE(meeting_id,member_id));
CREATE TABLE IF NOT EXISTS agenda_items (id TEXT PRIMARY KEY, organisation_id TEXT NOT NULL REFERENCES organisations(id), meeting_id TEXT NOT NULL REFERENCES meetings(id), parent_id TEXT REFERENCES agenda_items(id), title TEXT NOT NULL, metadata TEXT NOT NULL DEFAULT '{}', position INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT);
CREATE TABLE IF NOT EXISTS documents (id TEXT PRIMARY KEY, organisation_id TEXT NOT NULL REFERENCES organisations(id), meeting_id TEXT REFERENCES meetings(id), agenda_item_id TEXT REFERENCES agenda_items(id), title TEXT NOT NULL, classification TEXT NOT NULL DEFAULT 'internal', status TEXT NOT NULL DEFAULT 'draft', created_by TEXT NOT NULL REFERENCES members(id), created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT);
CREATE TABLE IF NOT EXISTS document_versions (id TEXT PRIMARY KEY, organisation_id TEXT NOT NULL REFERENCES organisations(id), document_id TEXT NOT NULL REFERENCES documents(id), version_number INTEGER NOT NULL, storage_key TEXT NOT NULL, sha256 TEXT NOT NULL, size_bytes INTEGER NOT NULL, created_by TEXT NOT NULL REFERENCES members(id), created_at TEXT NOT NULL, UNIQUE(document_id,version_number));
CREATE TABLE IF NOT EXISTS document_notes (id TEXT PRIMARY KEY, organisation_id TEXT NOT NULL REFERENCES organisations(id), document_id TEXT NOT NULL REFERENCES documents(id), body TEXT NOT NULL, created_by TEXT NOT NULL REFERENCES members(id), created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT);
CREATE TABLE IF NOT EXISTS notifications (id TEXT PRIMARY KEY, organisation_id TEXT NOT NULL REFERENCES organisations(id), member_id TEXT NOT NULL REFERENCES members(id), type TEXT NOT NULL, payload TEXT NOT NULL, read_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT);
CREATE TABLE IF NOT EXISTS audit_logs (id TEXT PRIMARY KEY, organisation_id TEXT REFERENCES organisations(id), actor_member_id TEXT REFERENCES members(id), event_type TEXT NOT NULL, resource_type TEXT NOT NULL, resource_id TEXT, payload TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS audit_logs_immutable_update BEFORE UPDATE ON audit_logs BEGIN SELECT RAISE(ABORT, 'audit logs are immutable'); END;
CREATE TRIGGER IF NOT EXISTS audit_logs_immutable_delete BEFORE DELETE ON audit_logs BEGIN SELECT RAISE(ABORT, 'audit logs are immutable'); END;
CREATE INDEX IF NOT EXISTS idx_meetings_tenant ON meetings(organisation_id, starts_at) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_audit_tenant ON audit_logs(organisation_id, created_at);
-- Workflow records are deliberately tenant-scoped and anchored to an agenda item.
CREATE TABLE IF NOT EXISTS conflict_declarations (id TEXT PRIMARY KEY, organisation_id TEXT NOT NULL REFERENCES organisations(id), meeting_id TEXT NOT NULL REFERENCES meetings(id), agenda_item_id TEXT REFERENCES agenda_items(id), member_id TEXT NOT NULL REFERENCES members(id), interest TEXT NOT NULL, management_action TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('declared','acknowledged','managed','recusal_required','recusal_approved')), created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT);
CREATE TABLE IF NOT EXISTS motions (id TEXT PRIMARY KEY, organisation_id TEXT NOT NULL REFERENCES organisations(id), meeting_id TEXT NOT NULL REFERENCES meetings(id), agenda_item_id TEXT NOT NULL REFERENCES agenda_items(id), proposer_member_id TEXT NOT NULL REFERENCES members(id), text TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('open','closed','withdrawn')), created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT);
CREATE TABLE IF NOT EXISTS votes (id TEXT PRIMARY KEY, organisation_id TEXT NOT NULL REFERENCES organisations(id), motion_id TEXT NOT NULL REFERENCES motions(id), member_id TEXT NOT NULL REFERENCES members(id), choice TEXT NOT NULL CHECK(choice IN ('for','against','abstain')), cast_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(motion_id,member_id));
-- Per-organisation/year counters are incremented with a single UPSERT statement by
-- the repository.  They make human-facing numbers stable without trusting clients.
CREATE TABLE IF NOT EXISTS resolution_number_sequences (organisation_id TEXT NOT NULL REFERENCES organisations(id), year INTEGER NOT NULL CHECK(year BETWEEN 2000 AND 9999), last_number INTEGER NOT NULL DEFAULT 0 CHECK(last_number >= 0), PRIMARY KEY(organisation_id, year));
CREATE TABLE IF NOT EXISTS action_number_sequences (organisation_id TEXT NOT NULL REFERENCES organisations(id), year INTEGER NOT NULL CHECK(year BETWEEN 2000 AND 9999), last_number INTEGER NOT NULL DEFAULT 0 CHECK(last_number >= 0), PRIMARY KEY(organisation_id, year));
CREATE TABLE IF NOT EXISTS resolutions (id TEXT PRIMARY KEY, organisation_id TEXT NOT NULL REFERENCES organisations(id), meeting_id TEXT NOT NULL REFERENCES meetings(id), agenda_item_id TEXT NOT NULL REFERENCES agenda_items(id), motion_id TEXT REFERENCES motions(id), resolution_year INTEGER NOT NULL CHECK(resolution_year BETWEEN 2000 AND 9999), resolution_number INTEGER NOT NULL CHECK(resolution_number > 0), text TEXT NOT NULL, outcome TEXT NOT NULL CHECK(outcome IN ('carried','not_carried','noted')), status TEXT NOT NULL CHECK(status IN ('draft','approved','published')), created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT, UNIQUE(organisation_id, resolution_year, resolution_number));
-- A carried motion can produce one resolution only.  The partial index still allows
-- not-carried/noted records to reference a motion when that is required for minutes.
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_carried_resolution_per_motion ON resolutions(motion_id) WHERE motion_id IS NOT NULL AND outcome='carried' AND deleted_at IS NULL;
CREATE TABLE IF NOT EXISTS minutes (id TEXT PRIMARY KEY, organisation_id TEXT NOT NULL REFERENCES organisations(id), meeting_id TEXT NOT NULL REFERENCES meetings(id), agenda_item_id TEXT NOT NULL REFERENCES agenda_items(id), body TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('draft','in_review','approved')), created_by TEXT NOT NULL REFERENCES members(id), created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT, UNIQUE(meeting_id,agenda_item_id));
-- Each agenda minute can contain structured, ordered discussion/decision/action items.
CREATE TABLE IF NOT EXISTS minute_items (id TEXT PRIMARY KEY, organisation_id TEXT NOT NULL REFERENCES organisations(id), minute_id TEXT NOT NULL REFERENCES minutes(id), item_type TEXT NOT NULL CHECK(item_type IN ('discussion','decision','action','note')), body TEXT NOT NULL, position INTEGER NOT NULL CHECK(position >= 0), created_by TEXT NOT NULL REFERENCES members(id), created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT, UNIQUE(minute_id, position));
CREATE TABLE IF NOT EXISTS actions (id TEXT PRIMARY KEY, organisation_id TEXT NOT NULL REFERENCES organisations(id), meeting_id TEXT NOT NULL REFERENCES meetings(id), agenda_item_id TEXT NOT NULL REFERENCES agenda_items(id), resolution_id TEXT REFERENCES resolutions(id), action_year INTEGER NOT NULL CHECK(action_year BETWEEN 2000 AND 9999), action_number INTEGER NOT NULL CHECK(action_number > 0), owner_member_id TEXT NOT NULL REFERENCES members(id), description TEXT NOT NULL, priority TEXT NOT NULL DEFAULT 'normal' CHECK(priority IN ('low','normal','high','critical')), due_at TEXT CHECK(due_at IS NULL OR datetime(due_at) IS NOT NULL), status TEXT NOT NULL CHECK(status IN ('open','in_progress','completed','cancelled')), created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT, deleted_at TEXT, UNIQUE(organisation_id, action_year, action_number));
CREATE TABLE IF NOT EXISTS action_updates (id TEXT PRIMARY KEY, organisation_id TEXT NOT NULL REFERENCES organisations(id), action_id TEXT NOT NULL REFERENCES actions(id), author_member_id TEXT NOT NULL REFERENCES members(id), body TEXT NOT NULL, status TEXT CHECK(status IS NULL OR status IN ('open','in_progress','completed','cancelled')), created_at TEXT NOT NULL, deleted_at TEXT);
CREATE TABLE IF NOT EXISTS completion_evidence (id TEXT PRIMARY KEY, organisation_id TEXT NOT NULL REFERENCES organisations(id), action_id TEXT NOT NULL REFERENCES actions(id), submitted_by TEXT NOT NULL REFERENCES members(id), note TEXT NOT NULL, storage_key TEXT, created_at TEXT NOT NULL, deleted_at TEXT);
CREATE INDEX IF NOT EXISTS idx_motions_tenant_meeting ON motions(organisation_id, meeting_id) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_actions_owner ON actions(organisation_id, owner_member_id) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_action_updates_action ON action_updates(organisation_id, action_id, created_at) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_minute_items_minute ON minute_items(organisation_id, minute_id, position) WHERE deleted_at IS NULL;
-- Read-only traceability projections.  Consumers must filter these views by
-- organisation_id exactly as they do all other tenant-scoped application queries.
CREATE VIEW IF NOT EXISTS action_traceability AS
SELECT a.id, a.organisation_id, a.action_year, a.action_number, a.meeting_id,
       a.agenda_item_id, a.resolution_id, a.owner_member_id, a.description,
       a.priority, a.due_at, a.status,
       CASE WHEN a.status IN ('open','in_progress') AND a.due_at IS NOT NULL
                 AND datetime(a.due_at) < datetime('now') THEN 1 ELSE 0 END AS is_overdue,
       a.completed_at, a.created_at, a.updated_at,
       COUNT(au.id) AS update_count, MAX(au.created_at) AS last_update_at
FROM actions a LEFT JOIN action_updates au ON au.action_id=a.id
    AND au.organisation_id=a.organisation_id AND au.deleted_at IS NULL
WHERE a.deleted_at IS NULL
GROUP BY a.id;
CREATE VIEW IF NOT EXISTS resolution_traceability AS
SELECT r.id, r.organisation_id, r.resolution_year, r.resolution_number,
       r.meeting_id, r.agenda_item_id, r.motion_id, r.text, r.outcome, r.status,
       r.created_at, r.updated_at, COUNT(a.id) AS action_count
FROM resolutions r LEFT JOIN actions a ON a.resolution_id=r.id
    AND a.organisation_id=r.organisation_id AND a.deleted_at IS NULL
WHERE r.deleted_at IS NULL
GROUP BY r.id;
-- PostgreSQL: ALTER TABLE meetings ENABLE ROW LEVEL SECURITY;
-- CREATE POLICY tenant_meetings ON meetings USING (organisation_id = current_setting('app.organisation_id', true)::uuid);
