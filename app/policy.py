ROLE_PERMISSIONS={
'Super Admin':{'*'}, 'Organisation Admin':{'*'},
'Secretariat':{'meetings.read','meetings.write','agenda.write','documents.read','documents.write',
               'attendance.read','attendance.write','members.read','conflicts.manage','motions.write',
               'members.manage','votes.manage','resolutions.write','minutes.write','actions.write','evidence.manage'},
'Chairperson':{'meetings.read','agenda.write','documents.read','attendance.read','conflicts.manage',
               'motions.write','votes.manage','resolutions.write','minutes.write','actions.write','evidence.manage'},
'Commissioner/Board Member':{'meetings.read','documents.read','rsvp.write','conflicts.write','votes.write','evidence.write'},
'Observer':{'meetings.read','documents.read'}}
def allowed(roles, permission): return any('*' in ROLE_PERMISSIONS.get(r,set()) or permission in ROLE_PERMISSIONS.get(r,set()) for r in roles)

def permissions_for(roles):
    permissions = set()
    for role in roles:
        permissions.update(ROLE_PERMISSIONS.get(role, set()))
    return permissions
