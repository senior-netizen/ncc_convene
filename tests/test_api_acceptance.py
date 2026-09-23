import http.cookiejar
import json
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from app.seed import MEETING_ID, PASSWORD


class HttpAcceptanceTests(unittest.TestCase):
    """Exercise authentication and reads over a real TCP/HTTP boundary."""

    def setUp(self):
        self.data = tempfile.TemporaryDirectory()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        self.cookies = http.cookiejar.CookieJar()
        self.client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))
        self.start_server()

    def tearDown(self):
        self.stop_server()
        self.data.cleanup()

    def start_server(self):
        self.server = subprocess.Popen(
            [sys.executable, "-m", "app.demo", "--data-dir", self.data.name,
             "--host", "127.0.0.1", "--port", str(self.port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, text=True,
        )
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                self.open("/api/v1/health")
                return
            except (urllib.error.URLError, ConnectionError):
                if self.server.poll() is not None:
                    self.fail("demo server exited during startup")
                time.sleep(0.05)
        self.fail("demo server did not start")

    def stop_server(self):
        if getattr(self, "server", None) and self.server.poll() is None:
            self.server.terminate()
            self.server.wait(timeout=10)

    def open(self, path, data=None, headers=None):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=data,
            headers=headers or {"Accept": "application/json"},
        )
        return self.client.open(request, timeout=5)

    def login(self):
        body = json.dumps({"email": "secretariat@ncc.example", "password": PASSWORD}).encode()
        response = self.open("/api/v1/session/login", body,
                             {"Content-Type": "application/json", "Accept": "application/json"})
        self.assertEqual(response.status, 200)

    def test_seeded_read_contract_and_session_survive_restart(self):
        with self.assertRaises(urllib.error.HTTPError) as denied:
            self.open("/api/v1/meetings")
        self.assertEqual(denied.exception.code, 401)
        denied.exception.close()
        self.login()
        for path in ("/api/v1/meetings", f"/api/v1/meetings/{MEETING_ID}",
                     f"/api/v1/meetings/{MEETING_ID}/agenda",
                     f"/api/v1/meetings/{MEETING_ID}/workspace"):
            with self.open(path) as response:
                self.assertEqual(response.status, 200, path)
                json.load(response)
        with self.assertRaises(urllib.error.HTTPError) as invalid:
            self.open("/api/v1/meetings?offset=9223372036854775808")
        self.assertEqual(invalid.exception.code, 400)
        invalid.exception.close()

        document = json.load(self.open(f"/api/v1/meetings/{MEETING_ID}/workspace"))["documents"][0]
        with self.open(f"/documents/{document['id']}/download",
                       headers={"Accept": "application/pdf"}) as response:
            self.assertTrue(response.read().startswith(b"%PDF-"))

        self.stop_server()
        self.start_server()
        # The generated signing secret is persistent, so the existing cookie is
        # accepted after restart; seeding also leaves the existing database alone.
        with self.open("/api/v1/session/me") as response:
            self.assertEqual(response.status, 200)

    def test_seeded_quorum_has_seventeen_voting_board_members(self):
        self.login()
        workspace = json.load(self.open(f"/api/v1/meetings/{MEETING_ID}/workspace"))
        self.assertEqual(workspace["quorum"]["eligible"], 17)
        self.assertEqual(len(workspace["participants"]), 19)


if __name__ == "__main__":
    unittest.main()
