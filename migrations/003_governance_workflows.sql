PRAGMA foreign_keys = ON;

-- A paper is the workflow envelope around an existing versioned document.  The
-- document remains the source of the immutable binary revisions.
CREATE TABLE IF NOT EXISTS board_papers (
  id TEXT PRIMARY KEY,
  organisation_id TEXT NOT NULL REFERENCES organisations(id),
  document_id TEXT NOT NULL REFERENCES documents(id),
  meeting_id TEXT NOT NULL REFERENCES meetings(id),
  committee_id TEXT NOT NULL REFERENCES committees(id),
  agenda_item_id TEXT NOT NULL REFERENCES agenda_items(id),
  author_member_id TEXT NOT NULL REFERENCES members(id),
  title TEXT NOT NULL,
  purpose TEXT NOT NULL,
  background TEXT NOT NULL,
  recommendation TEXT NOT NULL,
  implications TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('draft','submitted','under_review','changes_requested','resubmitted','approved','published')),
  current_revision_id TEXT NOT NULL REFERENCES document_versions(id),
  lock_version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted_at TEXT,
  UNIQUE(organisation_id, document_id)
);
CREATE INDEX IF NOT EXISTS idx_board_papers_meeting ON board_papers(organisation_id,meeting_id,status) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS paper_review_assignments (
  id TEXT PRIMARY KEY,
  organisation_id TEXT NOT NULL REFERENCES organisations(id),
  paper_id TEXT NOT NULL REFERENCES board_papers(id),
  revision_id TEXT NOT NULL REFERENCES document_versions(id),
  reviewer_member_id TEXT NOT NULL REFERENCES members(id),
  deadline TEXT,
  status TEXT NOT NULL CHECK(status IN ('assigned','changes_requested','approved','superseded')),
  assigned_by TEXT NOT NULL REFERENCES members(id),
  assigned_at TEXT NOT NULL,
  decided_at TEXT,
  reason TEXT,
  UNIQUE(paper_id,revision_id,reviewer_member_id)
);
CREATE INDEX IF NOT EXISTS idx_paper_reviews_due ON paper_review_assignments(organisation_id,reviewer_member_id,status,deadline);

CREATE TABLE IF NOT EXISTS paper_review_comments (
  id TEXT PRIMARY KEY,
  organisation_id TEXT NOT NULL REFERENCES organisations(id),
  paper_id TEXT NOT NULL REFERENCES board_papers(id),
  revision_id TEXT NOT NULL REFERENCES document_versions(id),
  author_member_id TEXT NOT NULL REFERENCES members(id),
  body TEXT NOT NULL,
  created_at TEXT NOT NULL,
  deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS board_pack_editions (
  id TEXT PRIMARY KEY,
  organisation_id TEXT NOT NULL REFERENCES organisations(id),
  meeting_id TEXT NOT NULL REFERENCES meetings(id),
  edition_number INTEGER NOT NULL,
  label TEXT NOT NULL,
  published_by TEXT NOT NULL REFERENCES members(id),
  published_at TEXT NOT NULL,
  UNIQUE(organisation_id,meeting_id,edition_number)
);
CREATE TABLE IF NOT EXISTS board_pack_items (
  id TEXT PRIMARY KEY,
  organisation_id TEXT NOT NULL REFERENCES organisations(id),
  edition_id TEXT NOT NULL REFERENCES board_pack_editions(id),
  paper_id TEXT NOT NULL REFERENCES board_papers(id),
  revision_id TEXT NOT NULL REFERENCES document_versions(id),
  position INTEGER NOT NULL,
  UNIQUE(edition_id,paper_id),
  UNIQUE(edition_id,position)
);

-- Reader annotations are private by default and are always revision anchored.
CREATE TABLE IF NOT EXISTS document_annotations (
  id TEXT PRIMARY KEY,
  organisation_id TEXT NOT NULL REFERENCES organisations(id),
  document_id TEXT NOT NULL REFERENCES documents(id),
  revision_id TEXT NOT NULL REFERENCES document_versions(id),
  member_id TEXT NOT NULL REFERENCES members(id),
  kind TEXT NOT NULL CHECK(kind IN ('bookmark','highlight','note')),
  locator TEXT NOT NULL,
  body TEXT,
  shared INTEGER NOT NULL DEFAULT 0 CHECK(shared IN (0,1)),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_annotations_private ON document_annotations(organisation_id,member_id,revision_id) WHERE deleted_at IS NULL;
