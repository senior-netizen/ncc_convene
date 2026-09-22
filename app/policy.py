ROLE_PERMISSIONS={
'Super Admin':{'*'}, 'Organisation Admin':{'*'}, 'Secretariat':{'meetings.write','agenda.write','documents.write','attendance.write','members.read'},
'Chairperson':{'meetings.read','agenda.write','documents.read','attendance.read'}, 'Commissioner/Board Member':{'meetings.read','documents.read','rsvp.write'}, 'Observer':{'meetings.read','documents.read'}}
def allowed(roles, permission): return any('*' in ROLE_PERMISSIONS.get(r,set()) or permission in ROLE_PERMISSIONS.get(r,set()) for r in roles)
