import unittest

from app.auth import hash_password
from app.db import Database, now, uid


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.db = Database()
        timestamp = now()
        self.org = uid()
        self.db.execute("INSERT INTO organisations VALUES(?,?,?,?,?,NULL)", (self.org, "One", "workflow", timestamp, timestamp))
        self.secretariat = self.member("secretariat@example.test")
        self.board = self.member("board@example.test")
        self.unconflicted = self.member("unconflicted@example.test")
        self.db.conn.commit()

    def tearDown(self):
        self.db.conn.close()

    def member(self, email):
        timestamp = now()
        user_id, member_id = uid(), uid()
        self.db.execute("INSERT INTO users VALUES(?,?,?,?,?,?,NULL)", (user_id, email, hash_password("x"), email, timestamp, timestamp))
        self.db.execute("INSERT INTO members VALUES(?,?,?,?,?,?,?,NULL)", (member_id, self.org, user_id, None, "active", timestamp, timestamp))
        return member_id

    def test_complete_meeting_workflow_is_persisted_and_audited(self):
        meeting = self.db.create_meeting(self.org, self.secretariat, "Meeting", "2026-09-24T09:00Z", "Harare", quorum_percent=50)
        agenda = self.db.add_agenda(self.org, self.secretariat, meeting, "Decision", 0)
        paper = self.db.add_document(self.org, self.secretariat, "Board paper", b"content", meeting, agenda)
        self.db.assign_attendee(self.org, self.secretariat, meeting, self.secretariat)
        self.db.assign_attendee(self.org, self.secretariat, meeting, self.board)
        self.db.attendance(self.org, self.secretariat, meeting, self.secretariat, "present")
        self.db.attendance(self.org, self.secretariat, meeting, self.board, "present")
        conflict = self.db.declare_conflict(self.org, self.board, meeting, self.board, "Supplier interest", "Recuse", agenda)
        motion = self.db.create_motion(self.org, self.secretariat, meeting, agenda, self.board, "Approve the paper")
        self.db.cast_vote(self.org, self.board, meeting, motion, self.board, "for")
        self.db.cast_vote(self.org, self.secretariat, meeting, motion, self.secretariat, "for")
        self.assertTrue(self.db.close_motion(self.org, self.secretariat, meeting, motion)["passed"])
        resolution = self.db.create_resolution(self.org, self.secretariat, meeting, agenda, "Paper approved", "carried", motion)
        minutes = self.db.save_minutes(self.org, self.secretariat, meeting, agenda, "The paper was approved.", "approved")
        action = self.db.create_action(self.org, self.secretariat, meeting, agenda, self.board, "Implement decision", resolution_id=resolution)
        evidence = self.db.add_completion_evidence(self.org, self.board, action, "Implemented")
        self.db.complete_action(self.org, self.board, action)
        self.assertEqual(self.db.execute("SELECT status FROM actions WHERE id=?", (action,)).fetchone()["status"], "completed")
        self.assertEqual(self.db.execute("SELECT agenda_item_id FROM resolutions WHERE id=?", (resolution,)).fetchone()["agenda_item_id"], agenda)
        self.assertEqual(self.db.execute("SELECT agenda_item_id FROM minutes WHERE id=?", (minutes,)).fetchone()["agenda_item_id"], agenda)
        self.assertEqual(self.db.execute("SELECT resolution_id FROM actions WHERE id=?", (action,)).fetchone()["resolution_id"], resolution)
        for resource_id in (paper, conflict, motion, resolution, minutes, action, evidence):
            self.assertIsNotNone(self.db.execute("SELECT 1 FROM audit_logs WHERE resource_id=?", (resource_id,)).fetchone())

    def test_vote_and_completion_edge_cases_are_rejected(self):
        meeting = self.db.create_meeting(self.org, self.secretariat, "Meeting", "2026-09-24T09:00Z", "Harare")
        agenda = self.db.add_agenda(self.org, self.secretariat, meeting, "Decision", 0)
        self.db.assign_attendee(self.org, self.secretariat, meeting, self.board)
        motion = self.db.create_motion(self.org, self.secretariat, meeting, agenda, self.board, "Approve")
        with self.assertRaisesRegex(ValueError, "present"):
            self.db.cast_vote(self.org, self.board, meeting, motion, self.board, "for")
        self.db.attendance(self.org, self.secretariat, meeting, self.board, "present")
        self.db.cast_vote(self.org, self.board, meeting, motion, self.board, "for")
        self.db.close_motion(self.org, self.secretariat, meeting, motion)
        with self.assertRaisesRegex(ValueError, "not open"):
            self.db.cast_vote(self.org, self.board, meeting, motion, self.board, "against")
        action = self.db.create_action(self.org, self.secretariat, meeting, agenda, self.board, "Implement")
        with self.assertRaisesRegex(ValueError, "evidence"):
            self.db.complete_action(self.org, self.board, action)

    def test_zero_eligible_attendees_is_not_quorate(self):
        meeting = self.db.create_meeting(self.org, self.secretariat, "Meeting", "2026-09-24T09:00Z", "Harare")
        self.assertEqual(self.db.quorum(self.org, meeting), {"eligible": 0, "present": 0, "required": 0, "met": False})

    def test_recusals_block_only_affected_members_from_motion_votes(self):
        meeting = self.db.create_meeting(self.org, self.secretariat, "Meeting", "2026-09-24T09:00Z", "Harare")
        agenda = self.db.add_agenda(self.org, self.secretariat, meeting, "Conflicted decision", 0)
        other_agenda = self.db.add_agenda(self.org, self.secretariat, meeting, "Other decision", 1)
        for member in (self.secretariat, self.board, self.unconflicted):
            self.db.assign_attendee(self.org, self.secretariat, meeting, member)
            self.db.attendance(self.org, self.secretariat, meeting, member, "present")

        conflict = self.db.declare_conflict(self.org, self.board, meeting, self.board,
                                            "Supplier interest", "Pending", agenda)
        self.db.manage_conflict_recusal(self.org, self.secretariat, meeting, conflict,
                                        "recusal_required")
        motion = self.db.create_motion(self.org, self.secretariat, meeting, agenda,
                                       self.secretariat, "Approve supplier")
        with self.assertRaisesRegex(ValueError, "non-recused"):
            self.db.cast_vote(self.org, self.board, meeting, motion, self.board, "for")
        self.db.cast_vote(self.org, self.secretariat, meeting, motion, self.secretariat, "for")
        self.db.cast_vote(self.org, self.unconflicted, meeting, motion, self.unconflicted, "for")
        self.assertEqual(self.db.vote_tally(self.org, meeting, motion)["eligible"], 2)

        unaffected_motion = self.db.create_motion(self.org, self.secretariat, meeting,
                                                  other_agenda, self.board, "Other business")
        self.db.cast_vote(self.org, self.board, meeting, unaffected_motion, self.board, "for")

        general_conflict = self.db.declare_conflict(self.org, self.board, meeting, self.board,
                                                    "Meeting-wide interest", "Pending")
        self.db.manage_conflict_recusal(self.org, self.secretariat, meeting, general_conflict,
                                        "recusal_approved")
        with self.assertRaisesRegex(ValueError, "non-recused"):
            self.db.cast_vote(self.org, self.board, meeting, unaffected_motion, self.board, "against")
        self.assertEqual(self.db.quorum(self.org, meeting)["eligible"], 2)
        self.assertEqual(self.db.vote_tally(self.org, meeting, unaffected_motion)["for"], 0)
        self.assertIsNotNone(self.db.execute(
            "SELECT 1 FROM audit_logs WHERE resource_id=? AND event_type='conflict.recusal_managed'",
            (general_conflict,),
        ).fetchone())


if __name__ == "__main__":
    unittest.main()
