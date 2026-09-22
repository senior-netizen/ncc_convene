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

    def test_numbered_traceable_records_and_structured_minutes(self):
        meeting = self.db.create_meeting(self.org, self.secretariat, "Meeting", "2026-09-24T09:00Z", "Harare")
        agenda = self.db.add_agenda(self.org, self.secretariat, meeting, "Decision", 0)
        minute = self.db.save_minutes(self.org, self.secretariat, meeting, agenda, "Summary", "in_review")
        item = self.db.add_minute_item(self.org, self.secretariat, minute, "decision", "Approved", 0)
        self.assertIsNotNone(self.db.execute("SELECT 1 FROM minute_items WHERE id=?", (item,)).fetchone())

        resolution = self.db.create_resolution(self.org, self.secretariat, meeting, agenda, "Noted", "noted")
        resolution_record = self.db.resolution_traceability(self.org, resolution)[0]
        self.assertEqual((resolution_record["resolution_year"], resolution_record["resolution_number"]), (2026, 1))

        action = self.db.create_action(self.org, self.secretariat, meeting, agenda, self.board,
                                       "Implement", "2001-01-01T00:00:00Z", resolution, "high")
        action_record = self.db.action_traceability(self.org, action)[0]
        self.assertEqual((action_record["action_year"], action_record["action_number"]), (2026, 1))
        self.assertEqual(action_record["priority"], "high")
        self.assertEqual(action_record["is_overdue"], 1)
        update = self.db.add_action_update(self.org, self.board, action, "Started", "in_progress")
        self.assertEqual(self.db.action_traceability(self.org, action)[0]["update_count"], 1)
        self.assertIsNotNone(self.db.execute("SELECT 1 FROM action_updates WHERE id=?", (update,)).fetchone())
        with self.assertRaisesRegex(ValueError, "due_at"):
            self.db.create_action(self.org, self.secretariat, meeting, agenda, self.board, "Bad date", "tomorrow")

    def test_carried_motion_cannot_generate_two_carried_resolutions(self):
        meeting = self.db.create_meeting(self.org, self.secretariat, "Meeting", "2026-09-24T09:00Z", "Harare")
        agenda = self.db.add_agenda(self.org, self.secretariat, meeting, "Decision", 0)
        self.db.assign_attendee(self.org, self.secretariat, meeting, self.secretariat)
        self.db.attendance(self.org, self.secretariat, meeting, self.secretariat, "present")
        motion = self.db.create_motion(self.org, self.secretariat, meeting, agenda, self.secretariat, "Approve")
        self.db.cast_vote(self.org, self.secretariat, meeting, motion, self.secretariat, "for")
        self.db.close_motion(self.org, self.secretariat, meeting, motion)
        self.db.create_resolution(self.org, self.secretariat, meeting, agenda, "Approved", "carried", motion)
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.db.create_resolution(self.org, self.secretariat, meeting, agenda, "Approved again", "carried", motion)


if __name__ == "__main__":
    unittest.main()
