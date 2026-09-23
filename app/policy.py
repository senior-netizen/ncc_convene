ROLE_PERMISSIONS={
'Super Admin':{'*'}, 'Organisation Admin':{'*'},
'Secretariat':{'meetings.read','meetings.write','agenda.write','documents.read','documents.write',
               'attendance.read','attendance.write','members.read','conflicts.manage','motions.write',
               'members.manage','votes.manage','resolutions.write','minutes.write','actions.write','evidence.manage',
               'papers.submit','papers.review','papers.publish','annotations.write'},
'Chairperson':{'meetings.read','agenda.write','documents.read','attendance.read','conflicts.manage',
               'motions.write','votes.manage','resolutions.write','minutes.write','actions.write','evidence.manage',
               'papers.review','annotations.write'},
'Commissioner/Board Member':{'meetings.read','documents.read','rsvp.write','conflicts.write','votes.write','evidence.write','papers.submit','papers.review','annotations.write'},
'Observer':{'meetings.read','documents.read'}}

CONFERENCE_CAPABILITIES = {
    'Super Admin': {'conference.join','conference.moderate','conference.present'},
    'Organisation Admin': {'conference.join','conference.moderate','conference.present'},
    'Secretariat': {'conference.join','conference.moderate','conference.present'},
    'Chairperson': {'conference.join','conference.moderate','conference.present'},
    'Commissioner/Board Member': {'conference.join'}, 'Observer': {'conference.join'},
}

def conference_capabilities(roles):
    result=set()
    for role in roles: result.update(CONFERENCE_CAPABILITIES.get(role,set()))
    return result
def allowed(roles, permission): return any('*' in ROLE_PERMISSIONS.get(r,set()) or permission in ROLE_PERMISSIONS.get(r,set()) for r in roles)

def permissions_for(roles):
    permissions = set()
    for role in roles:
        permissions.update(ROLE_PERMISSIONS.get(role, set()))
    return permissions
