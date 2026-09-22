import argparse
from .db import Database, uid, now
from .auth import hash_password
ROLES=['Super Admin','Organisation Admin','Secretariat','Chairperson','Commissioner/Board Member','Observer']
def seed(db):
 org=uid(); t=now(); db.execute('INSERT INTO organisations VALUES(?,?,?,?,?,NULL)',(org,'National Competitiveness Commission','ncc',t,t))
 roles={}
 for name in ROLES:
  roles[name]=uid(); db.execute('INSERT INTO roles VALUES(?,?,?,?,?,NULL)',(roles[name],org,name,t,t))
 members=[]
 for i in range(17):
  user,member=uid(),uid(); name='NCC Secretariat' if i==0 else f'Commissioner {i:02}'
  email='secretariat@ncc.example' if i==0 else f'board{i:02}@ncc.example'
  db.execute('INSERT INTO users VALUES(?,?,?,?,?,?,NULL)',(user,email,hash_password('ChangeMe123!'),name,t,t)); db.execute('INSERT INTO members VALUES(?,?,?,?,?,?,?,NULL)',(member,org,user,None,'active',t,t)); role='Secretariat' if i==0 else ('Chairperson' if i==1 else 'Commissioner/Board Member'); db.execute('INSERT INTO member_roles VALUES(?,?,?)',(member,roles[role],t)); members.append(member)
 committee=uid(); db.execute('INSERT INTO committees VALUES(?,?,?,?,?,?,NULL)',(committee,org,'Commission','Full Commission',t,t));
 for m in members: db.execute('INSERT INTO committee_members VALUES(?,?,?,?,?,?,?,NULL)',(uid(),org,committee,m,'member',t,t))
 db.conn.commit(); meeting=db.create_meeting(org,members[0],'Quarterly Commission Meeting','2026-09-24T09:00:00+02:00','Harare',quorum_percent=50,video_provider='zoom',video_metadata={'meeting_id':'ncc-q3-2026'});
 for m in members: db.assign_attendee(org,members[0],meeting,m)
 db.add_agenda(org,members[0],meeting,'Opening and quorum',0); db.add_agenda(org,members[0],meeting,'Quarterly competitiveness review',1)
 return org
if __name__=='__main__':
 p=argparse.ArgumentParser(); p.add_argument('--database',default='.data/ncc-convene.db'); a=p.parse_args(); import os; os.makedirs(os.path.dirname(a.database) or '.',exist_ok=True); seed(Database(a.database)); print('Seeded National Competitiveness Commission')
