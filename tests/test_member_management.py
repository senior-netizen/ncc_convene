import json
import os
import unittest
from io import BytesIO

from app.auth import hash_password, token
from app.db import Database, now, uid


class MemberManagementTests(unittest.TestCase):
    def setUp(self):
        os.environ["APP_DATABASE"] = ":memory:"
        from app import web
        self.web, self.db = web, Database()
        self.web.DB = self.db
        timestamp = now(); self.org, self.other_org = uid(), uid()
        for organisation, slug in ((self.org, "member-one"), (self.other_org, "member-two")):
            self.db.execute("INSERT INTO organisations VALUES(?,?,?,?,?,NULL)", (organisation, slug, slug, timestamp, timestamp))
        self.admin = self._member(self.org, "admin@example.test")
        self.viewer = self._member(self.org, "viewer@example.test")
        self.foreign_member = self._member(self.other_org, "foreign@example.test")
        self.role, foreign_role = uid(), uid()
        self.db.execute("INSERT INTO roles VALUES(?,?,?,?,?,NULL)", (self.role, self.org, "Secretariat", timestamp, timestamp))
        self.db.execute("INSERT INTO roles VALUES(?,?,?,?,?,NULL)", (foreign_role, self.other_org, "Foreign", timestamp, timestamp))
        self.db.execute("INSERT INTO member_roles VALUES(?,?,?)", (self.admin, self.role, timestamp))
        self.db.conn.commit()

    def tearDown(self):
        self.db.conn.close()

    def _member(self, org, email):
        timestamp = now(); user_id, member_id = uid(), uid()
        self.db.execute("INSERT INTO users VALUES(?,?,?,?,?,?,NULL)", (user_id, email, hash_password("x"), email, timestamp, timestamp))
        self.db.execute("INSERT INTO members VALUES(?,?,?,?,?,?,?,NULL)", (member_id, org, user_id, None, "active", timestamp, timestamp))
        return member_id

    def request(self, path, method="GET", payload=None, member=None):
        raw = json.dumps(payload or {}).encode(); received = []
        env = {"PATH_INFO": path, "REQUEST_METHOD": method, "QUERY_STRING": "",
               "CONTENT_LENGTH": str(len(raw)), "wsgi.input": BytesIO(raw),
               "HTTP_COOKIE": f"session={token({'user': 'ignored', 'org': self.org, 'member': member or self.admin}, self.web.SECRET)}"}
        result = b"".join(self.web.app(env, lambda status, headers: received.append(status)))
        return received[0], json.loads(result)

    def test_member_profile_lifecycle_persists_and_is_audited(self):
        status, payload = self.request("/members", "POST", {"email": "new@example.test", "display_name": "New Member", "title": "Director", "profile": {"phone": "+2631", "biography": "Profile"}})
        self.assertEqual(status, "201 Created"); member_id = payload["id"]
        status, payload = self.request(f"/members/{member_id}")
        self.assertEqual(status, "200 OK"); self.assertEqual(payload["member"]["phone"], "+2631")
        self.request(f"/members/{member_id}", "PATCH", {"title": "Chair", "profile": {"address": "Harare"}})
        self.request(f"/members/{member_id}/roles", "POST", {"role_id": self.role})
        self.request(f"/members/{member_id}/deactivate", "POST")
        member = self.db.member_profile(self.org, member_id)
        self.assertEqual((member["title"], member["status"], member["address"]), ("Chair", "inactive", "Harare"))
        self.assertEqual(self.db.execute("SELECT count(*) FROM audit_logs WHERE resource_id=?", (member_id,)).fetchone()[0], 4)

    def test_member_routes_require_permission_and_reject_foreign_role_or_member(self):
        self.assertEqual(self.request("/members", member=self.viewer)[0], "403 Forbidden")
        self.assertEqual(self.request(f"/members/{self.foreign_member}/roles", "POST", {"role_id": self.role})[0], "400 Bad Request")
        foreign_role = self.db.execute("SELECT id FROM roles WHERE organisation_id=?", (self.other_org,)).fetchone()["id"]
        self.assertEqual(self.request(f"/members/{self.viewer}/roles", "POST", {"role_id": foreign_role})[0], "400 Bad Request")
