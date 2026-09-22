"""Small dependency-free WSGI application; all API writes are authenticated and tenant-bound."""
import json, os
from http import cookies
from wsgiref.simple_server import make_server
from .db import Database
from .auth import token, read_token, verify_password
from .policy import allowed
DB=Database(os.getenv('APP_DATABASE','.data/ncc-convene.db')); SECRET=os.getenv('APP_SESSION_SECRET','development-only-secret')
def app(env,start):
 path,method=env['PATH_INFO'],env['REQUEST_METHOD']; cookie=cookies.SimpleCookie(env.get('HTTP_COOKIE','')); session=read_token(cookie.get('session').value,SECRET) if cookie.get('session') else None
 def send(status,body,headers=[]): start(status,[('Content-Type','application/json'),*headers]); return [json.dumps(body).encode()]
 def body(): return json.loads(env['wsgi.input'].read(int(env.get('CONTENT_LENGTH','0') or 0)) or b'{}')
 if path=='/login' and method=='POST':
  d=body(); u=DB.execute('SELECT * FROM users WHERE email=? AND deleted_at IS NULL',(d.get('email'),)).fetchone()
  if not u or not verify_password(d.get('password',''),u['password_hash']): DB.audit(None,None,'login.denied','session',payload={'email':d.get('email')}); return send('401 Unauthorized',{'error':'invalid credentials'})
  m=DB.execute('SELECT * FROM members WHERE user_id=? AND deleted_at IS NULL',(u['id'],)).fetchone(); DB.audit(m['organisation_id'],m['id'],'login','session'); return send('200 OK',{'ok':True},[('Set-Cookie',f'session={token({"user":u["id"],"org":m["organisation_id"],"member":m["id"]},SECRET)}; HttpOnly; SameSite=Lax; Path=/')])
 if path=='/logout' and method=='POST':
  if session: DB.audit(session['org'],session['member'],'logout','session')
  return send('200 OK',{'ok':True},[('Set-Cookie','session=; Max-Age=0; HttpOnly; SameSite=Lax; Path=/')])
 if not session: return send('401 Unauthorized',{'error':'authentication required'})
 roles=DB.roles(session['member'], session['org'])
 def require(permission):
  if allowed(roles,permission): return True
  DB.audit(session['org'],session['member'],'access.denied','route',path,{'permission':permission}); return False
 if path=='/home': return send('200 OK',{'roles':sorted(roles),'meetings':[dict(x) for x in DB.execute('SELECT * FROM meetings WHERE organisation_id=? AND deleted_at IS NULL',(session['org'],))]})
 if path=='/meetings' and method=='POST':
  if not require('meetings.write'): return send('403 Forbidden',{'error':'forbidden'})
  d=body(); extra={k:v for k,v in d.items() if k not in {'title','starts_at','location'}}; return send('201 Created',{'id':DB.create_meeting(session['org'],session['member'],d['title'],d['starts_at'],d.get('location',''),**extra)})
 parts=path.strip('/').split('/')
 if len(parts)>=3 and parts[0]=='meetings':
  meeting=parts[1]
  if not DB.meeting(session['org'],meeting): return send('404 Not Found',{'error':'not found'})
  if parts[2]=='transition' and method=='POST':
   if not require('meetings.write'): return send('403 Forbidden',{'error':'forbidden'})
   DB.transition_meeting(session['org'],session['member'],meeting,body()['status']); return send('200 OK',{'ok':True})
  if parts[2]=='agenda' and method=='POST':
   if not require('agenda.write'): return send('403 Forbidden',{'error':'forbidden'})
   d=body(); return send('201 Created',{'id':DB.add_agenda(session['org'],session['member'],meeting,d['title'],d['position'],d.get('parent_id'),d.get('metadata'))})
  if parts[2]=='participants' and method=='POST':
   if not require('meetings.write'): return send('403 Forbidden',{'error':'forbidden'})
   d=body(); DB.assign_attendee(session['org'],session['member'],meeting,d['member_id'],d.get('observer',False)); return send('200 OK',{'ok':True})
  if parts[2]=='rsvp' and method=='POST':
   d=body(); member_id=d.get('member_id',session['member'])
   can_manage=allowed(roles,'attendance.write')
   can_respond=allowed(roles,'rsvp.write') and member_id==session['member']
   if not (can_manage or can_respond):
    DB.audit(session['org'],session['member'],'access.denied','route',path,{'permission':'rsvp.write'})
    return send('403 Forbidden',{'error':'forbidden'})
   DB.rsvp(session['org'],session['member'],meeting,member_id,d['response']); return send('200 OK',{'ok':True})
 return send('404 Not Found',{'error':'not found'})
if __name__=='__main__': print('Serving on http://127.0.0.1:8000'); make_server('127.0.0.1',8000,app).serve_forever()
