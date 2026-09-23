-- Additive LiveKit conferencing state. Governance meeting lifecycle and formal
-- attendance remain deliberately independent from these tables.
CREATE TABLE IF NOT EXISTS conference_sessions (
 id TEXT PRIMARY KEY, organisation_id TEXT NOT NULL REFERENCES organisations(id),
 meeting_id TEXT NOT NULL REFERENCES meetings(id), provider TEXT NOT NULL,
 provider_room TEXT NOT NULL UNIQUE, state TEXT NOT NULL CHECK(state IN ('waiting','active','ended')),
 admission_required INTEGER NOT NULL DEFAULT 1, locked INTEGER NOT NULL DEFAULT 0,
 created_by TEXT NOT NULL REFERENCES members(id), created_at TEXT NOT NULL,
 started_at TEXT, ended_at TEXT, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conference_meeting ON conference_sessions(organisation_id,meeting_id,created_at);
CREATE TABLE IF NOT EXISTS conference_participants (
 id TEXT PRIMARY KEY, organisation_id TEXT NOT NULL REFERENCES organisations(id),
 meeting_id TEXT NOT NULL REFERENCES meetings(id), conference_session_id TEXT NOT NULL REFERENCES conference_sessions(id),
 member_id TEXT NOT NULL REFERENCES members(id), admission_state TEXT NOT NULL CHECK(admission_state IN ('waiting','admitted','rejected')),
 blocked INTEGER NOT NULL DEFAULT 0, can_moderate INTEGER NOT NULL DEFAULT 0,
 can_present INTEGER NOT NULL DEFAULT 0, can_record INTEGER NOT NULL DEFAULT 0,
 hand_raised_at TEXT, requested_at TEXT NOT NULL, admitted_at TEXT, removed_at TEXT,
 updated_at TEXT NOT NULL, UNIQUE(conference_session_id,member_id)
);
CREATE INDEX IF NOT EXISTS idx_conference_participants_tenant ON conference_participants(organisation_id,meeting_id,conference_session_id);
CREATE TABLE IF NOT EXISTS conference_connections (
 id TEXT PRIMARY KEY, organisation_id TEXT NOT NULL REFERENCES organisations(id), meeting_id TEXT NOT NULL REFERENCES meetings(id),
 conference_session_id TEXT NOT NULL REFERENCES conference_sessions(id), member_id TEXT NOT NULL REFERENCES members(id),
 provider_participant_id TEXT, connected_at TEXT NOT NULL, disconnected_at TEXT, last_event_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conference_connections ON conference_connections(organisation_id,conference_session_id,member_id);
CREATE TABLE IF NOT EXISTS conference_messages (
 id TEXT PRIMARY KEY, organisation_id TEXT NOT NULL REFERENCES organisations(id), meeting_id TEXT NOT NULL REFERENCES meetings(id),
 conference_session_id TEXT NOT NULL REFERENCES conference_sessions(id), sender_member_id TEXT NOT NULL REFERENCES members(id),
 body TEXT NOT NULL CHECK(length(body) BETWEEN 1 AND 4000), created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conference_messages ON conference_messages(organisation_id,conference_session_id,created_at);
CREATE TABLE IF NOT EXISTS conference_webhook_events (
 event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL, received_at TEXT NOT NULL, occurred_at TEXT, payload TEXT NOT NULL
);
