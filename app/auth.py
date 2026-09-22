import base64, hashlib, hmac, json, os, time
ITERATIONS=600_000
def hash_password(password, salt=None):
 salt=salt or os.urandom(16); digest=hashlib.pbkdf2_hmac('sha256',password.encode(),salt,ITERATIONS); return base64.b64encode(salt+digest).decode()
def verify_password(password, stored):
 raw=base64.b64decode(stored); return hmac.compare_digest(hash_password(password,raw[:16]),stored)
def token(payload, secret):
 payload={**payload,'exp':int(time.time())+28800}; raw=base64.urlsafe_b64encode(json.dumps(payload,separators=(',',':')).encode()).decode(); sig=hmac.new(secret.encode(),raw.encode(),hashlib.sha256).hexdigest(); return raw+'.'+sig
def read_token(value, secret):
 try:
  raw,sig=value.rsplit('.',1)
  if not hmac.compare_digest(sig,hmac.new(secret.encode(),raw.encode(),hashlib.sha256).hexdigest()): return None
  data=json.loads(base64.urlsafe_b64decode(raw)); return data if data['exp']>=time.time() else None
 except Exception: return None
