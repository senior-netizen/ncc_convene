import argparse
from .db import Database, uid, now
from .auth import hash_password

ROLES = ['Super Admin', 'Organisation Admin', 'Secretariat', 'Chairperson', 'Commissioner/Board Member', 'Observer']


def seed(db):
    org = uid(); timestamp = now()
    db.execute('INSERT INTO organisations VALUES(?,?,?,?,?,NULL)',
               (org, 'National Competitiveness Commission', 'ncc', timestamp, timestamp))
    db.execute('INSERT INTO organisation_profiles VALUES(?,?,?,?,?,?,?,?,?,?)',
               (org, 'National Competitiveness Commission', 'NCC', 'contact@ncc.example',
                '+263-242-000-000', 'Harare, Zimbabwe', 'https://www.ncc.example', None,
                timestamp, timestamp))
    roles = {}
    for name in ROLES:
        roles[name] = uid()
        db.execute('INSERT INTO roles VALUES(?,?,?,?,?,NULL)', (roles[name], org, name, timestamp, timestamp))
    members = []
    for i in range(17):
        user, member = uid(), uid()
        name = 'NCC Secretariat' if i == 0 else f'Commissioner {i:02}'
        email = 'secretariat@ncc.example' if i == 0 else f'board{i:02}@ncc.example'
        # Seed a provisioned account for the demo while retaining organisation-scoped identity/contact.
        db.execute('INSERT INTO users VALUES(?,?,?,?,?,?,NULL)',
                   (user, email, hash_password('ChangeMe123!'), name, timestamp, timestamp))
        db.execute('INSERT INTO members(id,organisation_id,user_id,title,status,created_at,updated_at,deleted_at) VALUES(?,?,?,?,?,?,?,NULL)',
                   (member, org, user, 'Secretariat' if i == 0 else 'Commissioner', 'active', timestamp, timestamp))
        db.execute('INSERT INTO member_profiles(member_id,organisation_id,display_name,email,phone,address,biography,profile_image_url,term_starts_on,term_ends_on,deactivated_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (member, org, name, email, '+263-242-000-000', 'Harare, Zimbabwe', None,
                    None, '2026-01-01', '2028-12-31', None, timestamp, timestamp))
        role = 'Secretariat' if i == 0 else ('Chairperson' if i == 1 else 'Commissioner/Board Member')
        db.execute('INSERT INTO member_roles VALUES(?,?,?)', (member, roles[role], timestamp)); members.append(member)
    committee = uid()
    db.execute('INSERT INTO committees VALUES(?,?,?,?,?,?,NULL)',
               (committee, org, 'Commission', 'Full Commission', timestamp, timestamp))
    for member in members:
        db.execute('INSERT INTO committee_members VALUES(?,?,?,?,?,?,?,NULL)',
                   (uid(), org, committee, member, 'member', timestamp, timestamp))
    db.conn.commit()
    meeting = db.create_meeting(org, members[0], 'Quarterly Commission Meeting',
                                '2026-09-24T09:00:00+02:00', 'Harare', quorum_percent=50,
                                video_provider='zoom', video_metadata={'meeting_id': 'ncc-q3-2026'})
    for member in members:
        db.assign_attendee(org, members[0], meeting, member)
    db.add_agenda(org, members[0], meeting, 'Opening and quorum', 0)
    db.add_agenda(org, members[0], meeting, 'Quarterly competitiveness review', 1)
    return org


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--database', default='.data/ncc-convene.db')
    args = parser.parse_args(); import os; os.makedirs(os.path.dirname(args.database) or '.', exist_ok=True)
    seed(Database(args.database)); print('Seeded National Competitiveness Commission')
