import os
import tempfile
import unittest

from app.auth import hash_password
from app.db import Database, StaleRevisionError, now, uid


class BoardPaperWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.uploads = tempfile.TemporaryDirectory()
        self.previous_uploads = os.environ.get("APP_UPLOAD_DIR")
        os.environ["APP_UPLOAD_DIR"] = self.uploads.name
        self.db = Database()
        timestamp = now(); self.org = uid()
        self.db.execute("INSERT INTO organisations VALUES(?,?,?,?,?,NULL)", (self.org, "NCC", uid(), timestamp, timestamp))
        self.author = self.member("author@example.test")
        self.reviewer = self.member("reviewer@example.test")
        self.secretariat = self.member("secretariat@example.test")
        self.committee = uid()
        self.db.execute("INSERT INTO committees VALUES(?,?,?,?,?,?,NULL)", (self.committee, self.org, "Board", "", timestamp, timestamp))
        self.db.conn.commit()
        self.meeting = self.db.create_meeting(self.org, self.secretariat, "Board", "2026-09-24T09:00:00+00:00", "Harare", committee_id=self.committee)
        self.agenda = self.db.add_agenda(self.org, self.secretariat, self.meeting, "Decision", 1)
        self.fields = {"title": "Competitiveness paper", "purpose": "Decision", "background": "Evidence", "recommendation": "Approve", "implications": "Budget"}

    def tearDown(self):
        self.db.close(); self.uploads.cleanup()
        if self.previous_uploads is None: os.environ.pop("APP_UPLOAD_DIR", None)
        else: os.environ["APP_UPLOAD_DIR"] = self.previous_uploads

    def member(self, email):
        timestamp = now(); user_id, member_id = uid(), uid()
        self.db.execute("INSERT INTO users VALUES(?,?,?,?,?,?,NULL)", (user_id, email, hash_password("x"), email, timestamp, timestamp))
        self.db.execute("INSERT INTO members VALUES(?,?,?,?,?,?,?,NULL)", (member_id, self.org, user_id, None, "active", timestamp, timestamp))
        return member_id

    def test_review_revision_and_pack_snapshot_are_immutable(self):
        paper_id = self.db.create_board_paper(self.org, self.author, self.meeting, self.agenda, self.fields, b"%PDF-1.4 first")
        paper = self.db.board_paper(self.org, paper_id)
        self.assertEqual(self.db.list_board_papers(self.org, self.meeting)[0]["id"], paper_id)
        submitted = self.db.transition_board_paper(self.org, self.author, paper_id, "submit", paper["current_revision_id"], paper["lock_version"])
        self.db.assign_paper_reviewer(self.org, self.secretariat, paper_id, self.reviewer, "2026-09-25T09:00:00+00:00")
        self.db.add_paper_comment(self.org, self.reviewer, paper_id, submitted["current_revision_id"], "Correct the figures")
        requested = self.db.review_board_paper(self.org, self.reviewer, paper_id, "changes_requested", submitted["current_revision_id"], "Figures require correction")
        revised = self.db.revise_board_paper(self.org, self.author, paper_id, self.fields, b"%PDF-1.4 corrected", requested["current_revision_id"])
        self.assertNotEqual(revised["current_revision_id"], submitted["current_revision_id"])
        resubmitted = self.db.transition_board_paper(self.org, self.author, paper_id, "submit", revised["current_revision_id"], revised["lock_version"])
        self.db.assign_paper_reviewer(self.org, self.secretariat, paper_id, self.reviewer)
        self.assertEqual(self.db.list_board_papers(self.org, actor=self.reviewer, reviewer_only=True)[0]["id"], paper_id)
        approved = self.db.review_board_paper(self.org, self.reviewer, paper_id, "approved", resubmitted["current_revision_id"], "Corrections verified")
        edition = self.db.publish_board_pack(self.org, self.secretariat, self.meeting, [paper_id], "Board pack v1")
        item = self.db.execute("SELECT revision_id FROM board_pack_items WHERE organisation_id=? AND edition_id=?", (self.org, edition)).fetchone()
        self.assertEqual(item["revision_id"], approved["current_revision_id"])
        changed = self.db.revise_board_paper(self.org, self.author, paper_id, self.fields, b"%PDF-1.4 later correction", approved["current_revision_id"])
        self.assertEqual(self.db.execute("SELECT revision_id FROM board_pack_items WHERE id IN (SELECT id FROM board_pack_items WHERE edition_id=?)", (edition,)).fetchone()["revision_id"], approved["current_revision_id"])
        self.assertEqual(changed["status"], "draft")

    def test_stale_and_unauthorized_review_decisions_are_rejected(self):
        paper_id = self.db.create_board_paper(self.org, self.author, self.meeting, self.agenda, self.fields, b"paper")
        paper = self.db.board_paper(self.org, paper_id)
        submitted = self.db.transition_board_paper(self.org, self.author, paper_id, "submit", paper["current_revision_id"], paper["lock_version"])
        self.db.assign_paper_reviewer(self.org, self.secretariat, paper_id, self.reviewer)
        with self.assertRaises(PermissionError):
            self.db.review_board_paper(self.org, self.author, paper_id, "approved", submitted["current_revision_id"], "Self approval")
        with self.assertRaises(StaleRevisionError):
            self.db.transition_board_paper(self.org, self.author, paper_id, "submit", submitted["current_revision_id"], submitted["lock_version"])

    def test_private_annotations_are_tenant_and_member_scoped(self):
        paper_id = self.db.create_board_paper(self.org, self.author, self.meeting, self.agenda, self.fields, b"paper")
        paper = self.db.board_paper(self.org, paper_id)
        annotation = self.db.add_annotation(self.org, self.author, paper["document_id"], paper["current_revision_id"], "note", "page:1", "Private")
        self.assertIsNotNone(self.db.execute("SELECT 1 FROM document_annotations WHERE id=? AND organisation_id=? AND member_id=?", (annotation, self.org, self.author)).fetchone())
        self.assertIsNone(self.db.execute("SELECT 1 FROM document_annotations WHERE id=? AND organisation_id=? AND member_id=?", (annotation, self.org, self.reviewer)).fetchone())


if __name__ == "__main__":
    unittest.main()
