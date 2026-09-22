import unittest

from app.db import Database
from app.seed import MEETING_ID, ORGANISATION_ID, PASSWORD, PERSONAS, seed


class SeedTests(unittest.TestCase):
    def snapshot(self, database):
        tables = ("organisations", "roles", "users", "members", "committees",
                  "committee_members", "meetings", "agenda_items", "documents",
                  "document_versions", "meeting_attendees", "meeting_rsvps")
        snapshot = {}
        for table in tables:
            if table == "organisations":
                sql = "SELECT * FROM organisations WHERE id=? ORDER BY id"
            elif table == "users":
                sql = ("SELECT * FROM users WHERE id IN (SELECT user_id FROM members "
                       "WHERE organisation_id=?) ORDER BY id")
            else:
                sql = f"SELECT * FROM {table} WHERE organisation_id=? ORDER BY id"
            snapshot[table] = [tuple(row) for row in database.execute(sql, (ORGANISATION_ID,))]
        return snapshot

    def test_reset_is_deterministic_and_restores_demo_content(self):
        database = Database()
        self.assertEqual(seed(database, reset=True, development=True), ORGANISATION_ID)
        before = self.snapshot(database)
        self.assertEqual(len(before["members"]), len(PERSONAS))
        self.assertEqual(len(before["committee_members"]), 17)
        self.assertEqual(len(before["agenda_items"]), 8)
        self.assertEqual(len(before["documents"]), 4)
        self.assertEqual(len(before["document_versions"]), 4)
        self.assertEqual(len(before["meeting_attendees"]), 19)
        self.assertEqual(database.meeting(ORGANISATION_ID, MEETING_ID)["starts_at"], "2026-09-24T09:00:00+02:00")

        database.execute("UPDATE meetings SET title='changed' WHERE id=?", (MEETING_ID,))
        database.execute("UPDATE agenda_items SET title='changed' WHERE organisation_id=?", (ORGANISATION_ID,))
        database.conn.commit()
        seed(database, reset=True, development=True)
        self.assertEqual(self.snapshot(database), before)

    def test_production_reset_requires_super_admin(self):
        database = Database()
        seed(database)
        with self.assertRaises(PermissionError):
            seed(database, reset=True, development=False)
        admin = database.execute(
            "SELECT m.id FROM members m JOIN users u ON u.id=m.user_id WHERE u.email=?",
            ("superadmin@ncc.example",),
        ).fetchone()["id"]
        seed(database, reset=True, development=False, actor_member_id=admin)

    def test_documented_demo_password_authenticates(self):
        database = Database()
        seed(database)
        from app.auth import verify_password
        row = database.execute("SELECT password_hash FROM users WHERE email=?", ("secretariat@ncc.example",)).fetchone()
        self.assertTrue(verify_password(PASSWORD, row["password_hash"]))
