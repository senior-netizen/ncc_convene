import io
import os
import tempfile
import unittest
from http.cookies import SimpleCookie

from app import web
from app.auth import token
from app.db import Database
from app.seed import MEETING_ID, ORGANISATION_ID, PASSWORD, PERSONAS, seed


class BrowserWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.upload_dir = tempfile.TemporaryDirectory()
        self.previous_upload_dir = os.environ.get('APP_UPLOAD_DIR')
        os.environ['APP_UPLOAD_DIR'] = self.upload_dir.name
        self.db = Database()
        seed(self.db, reset=True)
        web.DB = self.db

    def tearDown(self):
        self.db.conn.close()
        if self.previous_upload_dir is None:
            os.environ.pop('APP_UPLOAD_DIR', None)
        else:
            os.environ['APP_UPLOAD_DIR'] = self.previous_upload_dir
        self.upload_dir.cleanup()

    def request(self, path, member_email, method='GET', body=b'', content_type='application/x-www-form-urlencoded'):
        member = self.db.execute(
            'SELECT m.id FROM members m JOIN users u ON u.id=m.user_id WHERE u.email=? AND m.organisation_id=?',
            (member_email, ORGANISATION_ID),
        ).fetchone()['id']
        cookie = token({'user': 'browser-test', 'org': ORGANISATION_ID, 'member': member, 'csrf': 'csrf'}, web.SECRET)
        captured = []
        env = {
            'PATH_INFO': path, 'REQUEST_METHOD': method, 'CONTENT_LENGTH': str(len(body)),
            'CONTENT_TYPE': content_type, 'HTTP_ACCEPT': 'text/html', 'HTTP_COOKIE': f'session={cookie}',
            'wsgi.input': io.BytesIO(body),
        }
        result = b''.join(web.app(env, lambda status, headers: captured.append((status, headers))))
        return captured[0][0], result.decode()

    def test_eligible_voter_sees_all_vote_choices(self):
        status, html = self.request(f'/meetings/{MEETING_ID}', 'commissioner01@ncc.example')
        self.assertEqual(status, '200 OK')
        # The seed has no open motion; create one and verify the workspace renderer
        agenda = self.db.execute('SELECT id FROM agenda_items WHERE meeting_id=? LIMIT 1', (MEETING_ID,)).fetchone()['id']
        member = self.db.execute("SELECT id FROM members WHERE organisation_id=? AND user_id=(SELECT id FROM users WHERE email='commissioner01@ncc.example')", (ORGANISATION_ID,)).fetchone()['id']
        self.db.create_motion(ORGANISATION_ID, member, MEETING_ID, agenda, member, 'Browser vote')
        status, html = self.request(f'/meetings/{MEETING_ID}', 'commissioner01@ncc.example')
        self.assertEqual(status, '200 OK')
        self.assertIn('Vote For', html)
        self.assertIn('Vote Against', html)
        self.assertIn('Abstain', html)

    def test_conflict_form_has_agenda_selection_and_manager_controls(self):
        status, html = self.request(f'/meetings/{MEETING_ID}', 'commissioner01@ncc.example')
        self.assertEqual(status, '200 OK')
        self.assertIn('name="agenda_item_id"', html)
        agenda = self.db.execute('SELECT id FROM agenda_items WHERE meeting_id=? LIMIT 1', (MEETING_ID,)).fetchone()['id']
        member = self.db.execute("SELECT id FROM members WHERE organisation_id=? AND user_id=(SELECT id FROM users WHERE email='commissioner01@ncc.example')", (ORGANISATION_ID,)).fetchone()['id']
        self.db.declare_conflict(ORGANISATION_ID, member, MEETING_ID, member, 'Interest', 'Review', agenda)
        status, html = self.request(f'/meetings/{MEETING_ID}', 'secretariat@ncc.example')
        self.assertEqual(status, '200 OK')
        self.assertIn('Save recusal', html)

    def test_action_owner_is_allowed_evidence_but_not_other_action(self):
        agenda = self.db.execute('SELECT id FROM agenda_items WHERE meeting_id=? LIMIT 1', (MEETING_ID,)).fetchone()['id']
        owner = self.db.execute("SELECT id FROM members WHERE organisation_id=? AND user_id=(SELECT id FROM users WHERE email='commissioner01@ncc.example')", (ORGANISATION_ID,)).fetchone()['id']
        action = self.db.create_action(ORGANISATION_ID, owner, MEETING_ID, agenda, owner, 'Browser action')
        status, _ = self.request(f'/actions/{action}/evidence', 'commissioner01@ncc.example', 'POST', b'csrf=csrf&note=done')
        self.assertEqual(status, '303 See Other')
        status, _ = self.request(f'/actions/{action}/complete', 'commissioner02@ncc.example', 'POST', b'csrf=csrf')
        self.assertEqual(status, '403 Forbidden')

    def test_missing_csrf_is_rejected(self):
        status, _ = self.request(f'/meetings/{MEETING_ID}/transition', 'secretariat@ncc.example', 'POST', b'status=published')
        self.assertEqual(status, '403 Forbidden')

    def test_closed_motion_shows_final_result_and_hides_vote_controls(self):
        agenda = self.db.execute('SELECT id FROM agenda_items WHERE meeting_id=? LIMIT 1', (MEETING_ID,)).fetchone()['id']
        member = self.db.execute("SELECT id FROM members WHERE organisation_id=? AND user_id=(SELECT id FROM users WHERE email='commissioner01@ncc.example')", (ORGANISATION_ID,)).fetchone()['id']
        motion = self.db.create_motion(ORGANISATION_ID, member, MEETING_ID, agenda, member, 'Closed browser motion')
        self.db.cast_vote(ORGANISATION_ID, member, MEETING_ID, motion, member, 'for')
        self.db.close_motion(ORGANISATION_ID, member, MEETING_ID, motion)
        status, html = self.request(f'/meetings/{MEETING_ID}', 'commissioner01@ncc.example')
        self.assertEqual(status, '200 OK')
        self.assertIn('Result: Carried', html)
        self.assertNotIn(f'/motions/{motion}/votes', html)

    def test_minutes_item_is_visible_and_manager_only(self):
        agenda = self.db.execute('SELECT id FROM agenda_items WHERE meeting_id=? LIMIT 1', (MEETING_ID,)).fetchone()['id']
        minute = self.db.save_minutes(ORGANISATION_ID, self._member('secretariat@ncc.example'), MEETING_ID, agenda, 'Browser minutes', 'draft')
        body = f'csrf=csrf&item_type=discussion&body=Procurement+discussed&position=0'.encode()
        status, _ = self.request(f'/meetings/{MEETING_ID}/minutes/{minute}/items', 'secretariat@ncc.example', 'POST', body)
        self.assertEqual(status, '303 See Other')
        status, html = self.request(f'/meetings/{MEETING_ID}', 'commissioner01@ncc.example')
        self.assertEqual(status, '200 OK')
        self.assertIn('Procurement discussed', html)
        self.assertNotIn('Add minute item', html)

    def test_action_owner_name_and_completed_timestamp_are_rendered(self):
        agenda = self.db.execute('SELECT id FROM agenda_items WHERE meeting_id=? LIMIT 1', (MEETING_ID,)).fetchone()['id']
        owner = self._member('commissioner01@ncc.example')
        action = self.db.create_action(ORGANISATION_ID, owner, MEETING_ID, agenda, owner, 'Named action')
        self.db.add_completion_evidence(ORGANISATION_ID, owner, action, 'Evidence', None)
        self.db.complete_action(ORGANISATION_ID, owner, action)
        status, html = self.request(f'/meetings/{MEETING_ID}', 'secretariat@ncc.example')
        self.assertEqual(status, '200 OK')
        self.assertIn('Commissioner 01', html)
        self.assertIn('Completed', html)

    def _member(self, email):
        return self.db.execute('SELECT m.id FROM members m JOIN users u ON u.id=m.user_id WHERE u.email=? AND m.organisation_id=?', (email, ORGANISATION_ID)).fetchone()['id']


if __name__ == '__main__':
    unittest.main()
