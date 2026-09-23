import unittest
from app.db import Database,uid,now
from app.auth import hash_password
from app.policy import allowed
class PhaseTests(unittest.TestCase):
 def setUp(self):
  self.d=Database(); t=now(); self.o=uid(); self.o2=uid(); self.d.execute('INSERT INTO organisations VALUES(?,?,?,?,?,NULL)',(self.o,'One','one',t,t)); self.d.execute('INSERT INTO organisations VALUES(?,?,?,?,?,NULL)',(self.o2,'Two','two',t,t)); self.u=uid(); self.m=uid(); self.d.execute('INSERT INTO users VALUES(?,?,?,?,?,?,NULL)',(self.u,'a@example.test',hash_password('x'),'A',t,t)); self.d.execute('INSERT INTO members VALUES(?,?,?,?,?,?,?,NULL)',(self.m,self.o,self.u,None,'active',t,t)); self.d.conn.commit()
 def tearDown(self): self.d.conn.close()

 def test_tenant_isolation(self):
  x=self.d.create_meeting(self.o,self.m,'Private','2026-09-24T09:00Z','Harare'); self.assertIsNone(self.d.meeting(self.o2,x))
 def test_role_denial(self): self.assertFalse(allowed({'Observer'},'meetings.write')); self.assertTrue(allowed({'Secretariat'},'agenda.write'))
 def test_meeting_agenda_persistence(self):
  m=self.d.create_meeting(self.o,self.m,'M','2026-01-01T00:00Z','X'); a=self.d.add_agenda(self.o,self.m,m,'Top',0); self.d.reorder_agenda(self.o,self.m,m,[a]); self.assertEqual(self.d.execute('SELECT title FROM agenda_items WHERE id=?',(a,)).fetchone()['title'],'Top')
 def test_rsvp_attendance_and_quorum(self):
  m=self.d.create_meeting(self.o,self.m,'M','2026-01-01T00:00Z','X',quorum_percent=50); self.d.assign_attendee(self.o,self.m,m,self.m); self.d.attendance(self.o,self.m,m,self.m,'present'); self.d.rsvp(self.o,self.m,m,self.m,'yes'); self.assertEqual(self.d.quorum(self.o,m),{'eligible':1,'present':1,'required':1,'met':True})
 def test_document_versions_preserved(self):
  d=self.d.add_document(self.o,self.m,'Paper',b'one'); self.d.replace_document(self.o,self.m,d,b'two'); self.assertEqual(self.d.execute('SELECT count(*) n FROM document_versions WHERE document_id=?',(d,)).fetchone()['n'],2)
 def test_audit_immutable(self):
  m=self.d.create_meeting(self.o,self.m,'M','2026-01-01T00:00Z','X'); a=self.d.execute("SELECT * FROM audit_logs WHERE resource_id=?",(m,)).fetchone(); self.assertRaises(Exception,self.d.execute,'DELETE FROM audit_logs WHERE id=?',(a['id'],))
if __name__=='__main__': unittest.main()

class TenantBoundaryTests(unittest.TestCase):
 def setUp(self):
  self.d=Database(); t=now(); self.org_one=uid(); self.org_two=uid()
  for org,slug in ((self.org_one,'one'),(self.org_two,'two')):
   self.d.execute('INSERT INTO organisations VALUES(?,?,?,?,?,NULL)',(org,org,slug,t,t))
  self.members={}
  for org,email in ((self.org_one,'one@example.test'),(self.org_two,'two@example.test')):
   user,member=uid(),uid(); self.members[org]=member
   self.d.execute('INSERT INTO users VALUES(?,?,?,?,?,?,NULL)',(user,email,hash_password('x'),email,t,t))
   self.d.execute('INSERT INTO members VALUES(?,?,?,?,?,?,?,NULL)',(member,org,user,None,'active',t,t))
  self.d.conn.commit()
  self.meeting_one=self.d.create_meeting(self.org_one,self.members[self.org_one],'One','2026-01-01T00:00Z','Harare')
  self.meeting_two=self.d.create_meeting(self.org_two,self.members[self.org_two],'Two','2026-01-01T00:00Z','Bulawayo')

 def tearDown(self): self.d.conn.close()

 def test_tenant_bound_meeting_operations_reject_foreign_meeting(self):
  member=self.members[self.org_one]
  for operation in (
   lambda: self.d.add_agenda(self.org_one,member,self.meeting_two,'Foreign',0),
   lambda: self.d.assign_attendee(self.org_one,member,self.meeting_two,member),
   lambda: self.d.rsvp(self.org_one,member,self.meeting_two,member,'yes'),
   lambda: self.d.attendance(self.org_one,member,self.meeting_two,member,'present'),
  ):
   with self.assertRaisesRegex(ValueError,'meeting outside tenant'): operation()

 def test_document_versions_cannot_cross_tenant(self):
  document=self.d.add_document(self.org_one,self.members[self.org_one],'Private',b'one')
  with self.assertRaisesRegex(ValueError,'document outside tenant'):
   self.d.replace_document(self.org_two,self.members[self.org_two],document,b'two')

class RsvpAuthorisationTests(unittest.TestCase):
 def setUp(self):
  import os
  os.environ['APP_DATABASE']=':memory:'
  from app import web
  self.web=web; self.database=Database(); self.web.DB=self.database
  t=now(); self.org=uid(); self.database.execute('INSERT INTO organisations VALUES(?,?,?,?,?,NULL)',(self.org,'One','rsvp-one',t,t))
  self.commissioner=self._member('commissioner@example.test')
  self.other_member=self._member('other@example.test')
  self.secretariat=self._member('secretariat@example.test')
  role=uid(); secretariat_role=uid(); self.database.execute('INSERT INTO roles VALUES(?,?,?,?,?,NULL)',(role,self.org,'Commissioner/Board Member',t,t)); self.database.execute('INSERT INTO roles VALUES(?,?,?,?,?,NULL)',(secretariat_role,self.org,'Secretariat',t,t)); self.database.execute('INSERT INTO member_roles VALUES(?,?,?)',(self.commissioner,role,t)); self.database.execute('INSERT INTO member_roles VALUES(?,?,?)',(self.secretariat,secretariat_role,t)); self.database.conn.commit()
  self.meeting=self.database.create_meeting(self.org,self.commissioner,'Meeting','2026-01-01T00:00Z','Harare')

 def tearDown(self): self.database.conn.close()

 def _member(self,email):
  t=now(); user,member=uid(),uid()
  self.database.execute('INSERT INTO users VALUES(?,?,?,?,?,?,NULL)',(user,email,hash_password('x'),email,t,t))
  self.database.execute('INSERT INTO members VALUES(?,?,?,?,?,?,?,NULL)',(member,self.org,user,None,'active',t,t))
  return member

 def test_board_member_can_only_submit_own_rsvp(self):
  from io import BytesIO
  from app.auth import token
  payload=b'{"member_id":"'+self.other_member.encode()+b'","response":"yes"}'
  env={'PATH_INFO':f'/meetings/{self.meeting}/rsvp','REQUEST_METHOD':'POST','CONTENT_LENGTH':str(len(payload)),'wsgi.input':BytesIO(payload),'HTTP_X_CSRF_TOKEN':'test-csrf','HTTP_COOKIE':f'session={token({"user":"unused","org":self.org,"member":self.commissioner,"csrf":"test-csrf"},self.web.SECRET)}'}
  received=[]
  result=b''.join(self.web.app(env,lambda status,headers: received.append((status,headers))))
  self.assertEqual(received[0][0],'403 Forbidden')
  self.assertEqual(result,b'{"error": "forbidden"}')
  self.assertIsNone(self.database.execute('SELECT 1 FROM meeting_rsvps WHERE member_id=?',(self.other_member,)).fetchone())

 def test_json_meeting_mutation_requires_nonempty_matching_csrf_header(self):
  from io import BytesIO
  from app.auth import token
  payload=b'{"response":"yes"}'
  env={'PATH_INFO':f'/meetings/{self.meeting}/rsvp','REQUEST_METHOD':'POST','CONTENT_LENGTH':str(len(payload)),'wsgi.input':BytesIO(payload),'HTTP_COOKIE':f'session={token({"user":"unused","org":self.org,"member":self.commissioner,"csrf":"expected"},self.web.SECRET)}'}
  received=[]; result=b''.join(self.web.app(env,lambda status,headers: received.append(status)))
  self.assertEqual(received[0],'403 Forbidden')
  self.assertIn(b'csrf_failed',result)

 def test_only_conflict_managers_can_record_recusal_decision(self):
  from io import BytesIO
  from app.auth import token
  self.assertTrue(allowed({'Chairperson'},'conflicts.manage'))
  agenda=self.database.add_agenda(self.org,self.commissioner,self.meeting,'Decision',0)
  conflict=self.database.declare_conflict(self.org,self.commissioner,self.meeting,self.commissioner,'Interest','Pending',agenda)
  payload=b'{"status":"recusal_required"}'
  def request(member):
   env={'PATH_INFO':f'/meetings/{self.meeting}/conflicts/{conflict}/recusal','REQUEST_METHOD':'POST','CONTENT_LENGTH':str(len(payload)),'wsgi.input':BytesIO(payload),'HTTP_X_CSRF_TOKEN':'test-csrf','HTTP_COOKIE':f'session={token({"user":"unused","org":self.org,"member":member,"csrf":"test-csrf"},self.web.SECRET)}'}
   received=[]; result=b''.join(self.web.app(env,lambda status,headers: received.append((status,headers))))
   return received[0][0],result
  self.assertEqual(request(self.commissioner),('403 Forbidden',b'{"error": "forbidden"}'))
  self.assertEqual(request(self.secretariat),('200 OK',b'{"ok": true}'))
  self.assertEqual(self.database.execute('SELECT status FROM conflict_declarations WHERE id=?',(conflict,)).fetchone()['status'],'recusal_required')
