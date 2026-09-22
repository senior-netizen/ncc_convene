import json, sqlite3, uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = Path(__file__).parent.parent / 'migrations/001_phase_1_2.sql'
def now(): return datetime.now(timezone.utc).isoformat()
def uid(): return str(uuid.uuid4())
class Database:
 def __init__(self, path=':memory:'):
  self.conn=sqlite3.connect(path); self.conn.row_factory=sqlite3.Row; self.conn.execute('PRAGMA foreign_keys=ON'); self.conn.executescript(SCHEMA.read_text())
 def execute(self, sql, params=()): return self.conn.execute(sql, params)
 def audit(self, organisation_id, actor, event, resource, resource_id=None, payload=None):
  self.execute('INSERT INTO audit_logs VALUES (?,?,?,?,?,?,?,?)',(uid(),organisation_id,actor,event,resource,resource_id,json.dumps(payload or {},sort_keys=True),now())); self.conn.commit()
 def member(self, org, user): return self.execute('SELECT * FROM members WHERE organisation_id=? AND user_id=? AND deleted_at IS NULL',(org,user)).fetchone()
 def roles(self, member_id): return {r['name'] for r in self.execute('SELECT r.name FROM roles r JOIN member_roles mr ON mr.role_id=r.id WHERE mr.member_id=? AND r.deleted_at IS NULL',(member_id,))}
 def create_meeting(self, org, actor, title, starts_at, location, **kw):
  x=uid(); t=now(); self.execute('INSERT INTO meetings(id,organisation_id,title,starts_at,location,status,recurrence_rule,quorum_percent,video_provider,video_metadata,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(x,org,title,starts_at,location,'draft',kw.get('recurrence_rule'),kw.get('quorum_percent',50),kw.get('video_provider'),json.dumps(kw.get('video_metadata',{})),actor,t,t)); self.conn.commit(); self.audit(org,actor,'meeting.created','meeting',x); return x
 def meeting(self, org, id): return self.execute('SELECT * FROM meetings WHERE id=? AND organisation_id=? AND deleted_at IS NULL',(id,org)).fetchone()
 def transition_meeting(self, org, actor, meeting_id, status):
  if status not in {'draft','scheduled','published','completed','cancelled'}: raise ValueError('invalid lifecycle status')
  if not self.meeting(org,meeting_id): raise ValueError('meeting outside tenant')
  self.execute('UPDATE meetings SET status=?,updated_at=? WHERE id=? AND organisation_id=?',(status,now(),meeting_id,org)); self.conn.commit(); self.audit(org,actor,'meeting.lifecycle_changed','meeting',meeting_id,{'status':status})
 def add_agenda(self, org, actor, meeting_id, title, position, parent_id=None, metadata=None):
  x=uid(); t=now(); self.execute('INSERT INTO agenda_items VALUES(?,?,?,?,?,?,?,?,?,?)',(x,org,meeting_id,parent_id,title,json.dumps(metadata or {}),position,t,t,None)); self.conn.commit(); self.audit(org,actor,'agenda.created','agenda_item',x); return x
 def reorder_agenda(self, org, actor, meeting_id, ordered_ids):
  for p,x in enumerate(ordered_ids):
   if not self.execute('SELECT 1 FROM agenda_items WHERE id=? AND meeting_id=? AND organisation_id=? AND deleted_at IS NULL',(x,meeting_id,org)).fetchone(): raise ValueError('agenda item outside tenant/meeting')
   self.execute('UPDATE agenda_items SET position=?,updated_at=? WHERE id=?',(p,now(),x))
  self.conn.commit(); self.audit(org,actor,'agenda.reordered','meeting',meeting_id,{'order':ordered_ids})
 def assign_attendee(self, org, actor, meeting, member, observer=False):
  if not self.execute('SELECT 1 FROM members WHERE id=? AND organisation_id=? AND deleted_at IS NULL',(member,org)).fetchone(): raise ValueError('member outside tenant')
  self.execute('INSERT INTO meeting_attendees VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(meeting_id,member_id) DO UPDATE SET observer=excluded.observer,updated_at=excluded.updated_at',(uid(),org,meeting,member,'pending',int(observer),now(),now(),None)); self.conn.commit(); self.audit(org,actor,'meeting.participant_assigned','meeting',meeting,{'member_id':member,'observer':observer})
 def rsvp(self, org, actor, meeting, member, response):
  if response not in {'yes','no','maybe'}: raise ValueError('invalid RSVP')
  t=now(); self.execute('INSERT INTO meeting_rsvps VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(meeting_id,member_id) DO UPDATE SET response=excluded.response,responded_at=excluded.responded_at,updated_at=excluded.updated_at',(uid(),org,meeting,member,response,t,t,t,None)); self.conn.commit(); self.audit(org,actor,'meeting.rsvp_updated','meeting',meeting,{'member_id':member,'response':response})
 def attendance(self, org, actor, meeting, member, status):
  if status not in {'present','absent','apology'}: raise ValueError('invalid attendance')
  self.execute('UPDATE meeting_attendees SET status=?,updated_at=? WHERE meeting_id=? AND member_id=? AND organisation_id=?',(status,now(),meeting,member,org)); self.conn.commit(); self.audit(org,actor,'meeting.attendance_updated','meeting',meeting,{'member_id':member,'status':status})
 def quorum(self, org, meeting):
  row=self.meeting(org,meeting); eligible=self.execute("SELECT count(*) n FROM meeting_attendees WHERE meeting_id=? AND organisation_id=? AND observer=0 AND deleted_at IS NULL",(meeting,org)).fetchone()['n']; present=self.execute("SELECT count(*) n FROM meeting_attendees WHERE meeting_id=? AND organisation_id=? AND observer=0 AND status='present' AND deleted_at IS NULL",(meeting,org)).fetchone()['n']; return {'eligible':eligible,'present':present,'required':(eligible*row['quorum_percent']+99)//100,'met':present*100 >= eligible*row['quorum_percent']}
 def add_document(self, org, actor, title, content, meeting=None, agenda=None, classification='internal'):
  import hashlib
  d,v=uid(),uid(); t=now(); self.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,?,?)',(d,org,meeting,agenda,title,classification,'draft',actor,t,t,None)); self.execute('INSERT INTO document_versions VALUES(?,?,?,?,?,?,?,?,?)',(v,org,d,1,'inline/'+v,hashlib.sha256(content).hexdigest(),len(content),actor,t)); self.conn.commit(); self.audit(org,actor,'document.uploaded','document',d); return d
 def replace_document(self, org, actor, document, content):
  import hashlib
  row=self.execute('SELECT coalesce(max(version_number),0)+1 n FROM document_versions WHERE document_id=? AND organisation_id=?',(document,org)).fetchone(); v=uid(); self.execute('INSERT INTO document_versions VALUES(?,?,?,?,?,?,?,?,?)',(v,org,document,row['n'],'inline/'+v,hashlib.sha256(content).hexdigest(),len(content),actor,now())); self.conn.commit(); self.audit(org,actor,'document.replaced','document',document,{'version':row['n']})
