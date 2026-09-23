import tempfile
import unittest
from unittest.mock import patch

from app.db import Database
from app.seed import seed, ORGANISATION_ID, MEETING_ID, _id


class ConferenceSecurityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(self.tmp.name + "/app.db")
        seed(self.db)
        self.secretary = _id("member", "secretariat@ncc.example")
        self.chair = _id("member", "chair@ncc.example")
        self.commissioner = _id("member", "commissioner01@ncc.example")

    def tearDown(self):
        self.db.close(); self.tmp.cleanup()

    def test_admission_and_block_survive_database_restart(self):
        conference = self.db.start_conference(ORGANISATION_ID, self.secretary, MEETING_ID)
        self.db.request_admission(ORGANISATION_ID, self.secretary, MEETING_ID, conference["id"])
        waiting = self.db.request_admission(ORGANISATION_ID, self.commissioner, MEETING_ID, conference["id"])
        self.assertEqual(waiting["admission_state"], "waiting")
        self.db.moderate_participant(ORGANISATION_ID, self.secretary, MEETING_ID, conference["id"], self.commissioner, "admit")
        removed = self.db.moderate_participant(ORGANISATION_ID, self.secretary, MEETING_ID, conference["id"], self.commissioner, "remove")
        self.assertEqual(removed["blocked"], 1)
        path = self.tmp.name + "/app.db"; self.db.close(); self.db = Database(path)
        persisted = self.db.conference_participant(ORGANISATION_ID, MEETING_ID, conference["id"], self.commissioner)
        self.assertEqual((persisted["blocked"], persisted["admission_state"]), (1, "admitted"))
        with self.assertRaisesRegex(ValueError, "blocked"):
            self.db.request_admission(ORGANISATION_ID, self.commissioner, MEETING_ID, conference["id"])

    def test_non_moderator_cannot_admit_or_cross_tenant(self):
        conference = self.db.start_conference(ORGANISATION_ID, self.secretary, MEETING_ID)
        self.db.request_admission(ORGANISATION_ID, self.commissioner, MEETING_ID, conference["id"])
        with self.assertRaises(PermissionError):
            self.db.moderate_participant(ORGANISATION_ID, self.commissioner, MEETING_ID, conference["id"], self.chair, "admit")
        self.assertIsNone(self.db.conference("another-tenant", MEETING_ID, conference["id"]))

    def test_end_does_not_complete_governance_meeting(self):
        conference = self.db.start_conference(ORGANISATION_ID, self.secretary, MEETING_ID)
        before = self.db.meeting(ORGANISATION_ID, MEETING_ID)["status"]
        self.db.end_conference(ORGANISATION_ID, self.secretary, MEETING_ID, conference["id"])
        self.assertEqual(self.db.meeting(ORGANISATION_ID, MEETING_ID)["status"], before)

    def test_room_identifier_is_opaque(self):
        conference = self.db.start_conference(ORGANISATION_ID, self.secretary, MEETING_ID)
        self.assertNotIn(ORGANISATION_ID, conference["provider_room"])
        self.assertNotIn(MEETING_ID, conference["provider_room"])

    def test_lock_rejects_new_entry_but_keeps_admitted_reconnect(self):
        conference = self.db.start_conference(ORGANISATION_ID, self.secretary, MEETING_ID)
        self.db.request_admission(ORGANISATION_ID, self.secretary, MEETING_ID, conference["id"])
        self.db.request_admission(ORGANISATION_ID, self.commissioner, MEETING_ID, conference["id"])
        self.db.moderate_participant(ORGANISATION_ID, self.secretary, MEETING_ID, conference["id"], self.commissioner, "admit")
        self.db.set_conference_lock(ORGANISATION_ID, self.secretary, MEETING_ID, conference["id"], True)
        self.assertEqual(self.db.request_admission(ORGANISATION_ID, self.commissioner, MEETING_ID, conference["id"])["admission_state"], "admitted")
        second = _id("member", "commissioner02@ncc.example")
        with self.assertRaisesRegex(ValueError, "locked"):
            self.db.request_admission(ORGANISATION_ID, second, MEETING_ID, conference["id"])


if __name__ == "__main__": unittest.main()
