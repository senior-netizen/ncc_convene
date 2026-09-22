import unittest
from app.db import Database,uid,now
from app.auth import hash_password
from app.policy import allowed
class PhaseTests(unittest.TestCase):
 def setUp(self):
  self.d=Database(); t=now(); self.o=uid(); self.o2=uid(); self.d.execute('INSERT INTO organisations VALUES(?,?,?,?,?,NULL)',(self.o,'One','one',t,t)); self.d.execute('INSERT INTO organisations VALUES(?,?,?,?,?,NULL)',(self.o2,'Two','two',t,t)); self.u=uid(); self.m=uid(); self.d.execute('INSERT INTO users VALUES(?,?,?,?,?,?,NULL)',(self.u,'a@example.test',hash_password('x'),'A',t,t)); self.d.execute('INSERT INTO members VALUES(?,?,?,?,?,?,?,NULL)',(self.m,self.o,self.u,None,'active',t,t)); self.d.conn.commit()
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
