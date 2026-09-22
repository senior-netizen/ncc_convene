"""Dependency-free WSGI API; every workflow mutation is tenant-bound and audited."""
import json
import os
from html import escape
from http import cookies
from pathlib import Path
from urllib.parse import parse_qs
from wsgiref.simple_server import make_server

from .auth import read_token, token, verify_password
from .db import Database, DuplicateVoteError
from .policy import allowed

DB = Database(os.getenv("APP_DATABASE", ".data/ncc-convene.db"))
SECRET = os.getenv("APP_SESSION_SECRET", "development-only-secret")
COOKIE_SECURE = "; Secure" if os.getenv("APP_COOKIE_SECURE", "0").lower() in {"1", "true", "yes"} else ""


def wants_html(env):
    """Keep the JSON API stable while allowing the same URLs to serve browsers."""
    return "text/html" in env.get("HTTP_ACCEPT", "")


def page(title, content, session=None, roles=()):
    """Render the small, dependency-free browser shell used by the staff portal."""
    navigation = ""
    if session:
        links = ['<a href="/dashboard">Dashboard</a>']
        links.append('<a href="/members">Members</a>' if allowed(roles, 'members.read') else '')
        links.append('<a href="/activity-log">Activity log</a>' if allowed(roles, 'audit.read') else '')
        links.append('<a href="/administration">Administration</a>' if allowed(roles, 'administration.manage') else '')
        navigation = f"""
        <nav aria-label="Primary navigation">{''.join(links)}
          <form action="/logout" method="post"><button type="submit">Log out</button></form>
        </nav>"""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1"><title>{escape(title)} · NCC Convene</title>
    <style>body{{font:16px system-ui,sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem;color:#172033}}nav{{display:flex;gap:1rem;align-items:center;border-bottom:1px solid #d7dce5;padding-bottom:1rem;flex-wrap:wrap}}nav form{{margin:0}}main{{margin-top:2rem}}table{{border-collapse:collapse;width:100%}}th,td{{text-align:left;padding:.6rem;border-bottom:1px solid #d7dce5}}.empty{{padding:1rem;background:#f4f6f9;border-radius:.25rem}}label{{display:block;margin:.7rem 0}}input,textarea,select{{display:block;padding:.45rem;width:100%;max-width:34rem}}button{{padding:.45rem .7rem}}.workspace-nav{{display:flex;gap:.7rem;flex-wrap:wrap;margin:1rem 0}}.card{{border:1px solid #d7dce5;border-radius:.4rem;padding:1rem;margin:1rem 0}}</style>
    </head><body>{navigation}<main>{content}</main></body></html>"""


def html_send(start, status, content, headers=()):
    start(status, [("Content-Type", "text/html; charset=utf-8"), *headers])
    return [content.encode("utf-8")]


def binary_send(start, status, content, content_type, headers=()):
    start(status, [("Content-Type", content_type), ("Content-Length", str(len(content))), *headers])
    return [content]


def login_page(message=""):
    notice = f'<p role="alert">{escape(message)}</p>' if message else ""
    return page("Sign in", f"""<h1>Sign in</h1>{notice}
      <form action="/login" method="post"><label>Email <input name="email" type="email" required></label>
      <label>Password <input name="password" type="password" required></label><button type="submit">Sign in</button></form>""")


def browser_body(env):
    raw = env["wsgi.input"].read(int(env.get("CONTENT_LENGTH", "0") or 0)).decode("utf-8")
    return {key: values[0] for key, values in parse_qs(raw, keep_blank_values=True).items()}


def app(env, start):
    path, method = env["PATH_INFO"], env["REQUEST_METHOD"]
    browser = wants_html(env)
    cookie = cookies.SimpleCookie(env.get("HTTP_COOKIE", ""))
    session = read_token(cookie.get("session").value, SECRET) if cookie.get("session") else None

    def send(status, payload, headers=()):
        start(status, [("Content-Type", "application/json"), *headers])
        return [json.dumps(payload).encode()]

    def body():
        try:
            raw = env["wsgi.input"].read(int(env.get("CONTENT_LENGTH", "0") or 0))
            value = json.loads(raw or b"{}")
            if not isinstance(value, dict):
                raise ValueError
            return value
        except (json.JSONDecodeError, ValueError):
            raise ValueError("request body must be a JSON object")

    if path == "/login" and method == "GET" and browser:
        return html_send(start, "200 OK", login_page())

    if path == "/login" and method == "POST":
        try:
            data = browser_body(env) if browser else body()
        except ValueError as exc:
            if browser:
                return html_send(start, "400 Bad Request", login_page(str(exc)))
            return send("400 Bad Request", {"error": str(exc)})
        user = DB.execute("SELECT * FROM users WHERE email=? AND deleted_at IS NULL", (data.get("email"),)).fetchone()
        if not user or not verify_password(data.get("password", ""), user["password_hash"]):
            DB.audit(None, None, "login.denied", "session", payload={"email": data.get("email")})
            if browser:
                return html_send(start, "401 Unauthorized", login_page("Invalid email or password."))
            return send("401 Unauthorized", {"error": "invalid credentials"})
        member = DB.execute("SELECT * FROM members WHERE user_id=? AND status='active' AND deleted_at IS NULL", (user["id"],)).fetchone()
        if not member:
            if browser:
                return html_send(start, "403 Forbidden", login_page("No active organisation membership."))
            return send("403 Forbidden", {"error": "no active organisation membership"})
        DB.audit(member["organisation_id"], member["id"], "login", "session")
        value = token({"user": user["id"], "org": member["organisation_id"], "member": member["id"]}, SECRET)
        if browser:
            return html_send(start, "303 See Other", "", [("Location", "/dashboard"), ("Set-Cookie", f"session={value}; HttpOnly; SameSite=Lax; Path=/{COOKIE_SECURE}")])
        return send("200 OK", {"ok": True}, [("Set-Cookie", f"session={value}; HttpOnly; SameSite=Lax; Path=/{COOKIE_SECURE}")])

    if path == "/logout" and method == "POST":
        if session:
            DB.audit(session["org"], session["member"], "logout", "session")
        if browser:
            return html_send(start, "303 See Other", "", [("Location", "/login"), ("Set-Cookie", f"session=; Max-Age=0; HttpOnly; SameSite=Lax; Path=/{COOKIE_SECURE}")])
        return send("200 OK", {"ok": True}, [("Set-Cookie", f"session=; Max-Age=0; HttpOnly; SameSite=Lax; Path=/{COOKIE_SECURE}")])
    if not session or not DB.execute(
        "SELECT 1 FROM members WHERE id=? AND organisation_id=? AND status='active' AND deleted_at IS NULL",
        (session.get("member"), session.get("org")),
    ).fetchone():
        if browser:
            return html_send(start, "303 See Other", "", [("Location", "/login")])
        return send("401 Unauthorized", {"error": "authentication required"})

    org, actor = session["org"], session["member"]
    roles = DB.roles(actor, org)

    def require(permission):
        if allowed(roles, permission):
            return True
        DB.audit(org, actor, "access.denied", "route", path, {"permission": permission})
        return False

    def own_or(permission, member_id, management_permission):
        return (member_id == actor and allowed(roles, permission)) or allowed(roles, management_permission)

    def browser_require(permission):
        if require(permission):
            return None
        return html_send(start, "403 Forbidden", page("Access denied", "<h1>Access denied</h1><p>You do not have permission to view this page.</p>", session))

    try:
        # Organisation identity is deliberately never taken from a request value: all
        # member operations are bound to the organisation in the signed session.
        query = parse_qs(env.get("QUERY_STRING", ""), keep_blank_values=True)
        if browser and method == "GET" and path in {"/", "/dashboard"}:
            denied = browser_require("meetings.read")
            if denied: return denied
            meetings = [dict(row) for row in DB.execute(
                "SELECT id,title,starts_at,location,status FROM meetings WHERE organisation_id=? AND deleted_at IS NULL ORDER BY starts_at",
                (org,),
            )]
            if meetings:
                rows = "".join(f"<tr><td><a href=\"/meetings/{item['id']}\">{escape(item['title'])}</a></td><td>{escape(item['starts_at'])}</td><td>{escape(item['location'] or '')}</td><td>{escape(item['status'])}</td></tr>" for item in meetings)
                content = f"<h1>Dashboard</h1><h2>Meetings</h2><table><thead><tr><th>Meeting</th><th>Starts</th><th>Location</th><th>Status</th></tr></thead><tbody>{rows}</tbody></table>"
            else:
                content = "<h1>Dashboard</h1><div class=\"empty\"><h2>No meetings yet</h2><p>Scheduled meetings will appear here when this module is used.</p></div>"
            return html_send(start, "200 OK", page("Dashboard", content, session, roles))
        if browser and path.startswith('/documents/') and path.endswith('/download') and method == 'GET':
            denied = browser_require('documents.read')
            if denied: return denied
            document_id = path.strip('/').split('/')[1]
            document = DB.execute('SELECT d.title,v.storage_key,v.content_type,v.version_number FROM documents d JOIN document_versions v ON v.document_id=d.id AND v.organisation_id=d.organisation_id WHERE d.id=? AND d.organisation_id=? AND d.deleted_at IS NULL AND v.version_number=(SELECT max(v2.version_number) FROM document_versions v2 WHERE v2.document_id=d.id AND v2.organisation_id=?)', (document_id, org, org)).fetchone()
            if not document or not Path(document['storage_key']).is_file():
                return html_send(start, '404 Not Found', page('Document unavailable', '<h1>Document unavailable</h1><p>This board paper is not available.</p>', session, roles))
            content_type = document['content_type'] or 'application/octet-stream'
            return binary_send(start, '200 OK', Path(document['storage_key']).read_bytes(), content_type, [('Content-Disposition', f'inline; filename="{escape(document["title"])}"')])
        if browser and path.startswith('/documents/') and path.endswith('/versions') and method == 'GET':
            denied = browser_require('documents.read')
            if denied: return denied
            document_id = path.strip('/').split('/')[1]
            versions = DB.execute('SELECT version_number,size_bytes,sha256,content_type,created_at FROM document_versions WHERE document_id=? AND organisation_id=? ORDER BY version_number DESC', (document_id, org)).fetchall()
            rows = ''.join(f'<tr><td>{v["version_number"]}</td><td>{v["size_bytes"]} bytes</td><td>{escape(v["content_type"] or "")}</td><td>{escape(v["created_at"])}</td><td><code>{escape(v["sha256"][:12])}…</code></td></tr>' for v in versions)
            content = f'<h1>Document versions</h1><table><tr><th>Version</th><th>Size</th><th>Type</th><th>Uploaded</th><th>SHA-256</th></tr>{rows}</table>'
            return html_send(start, '200 OK', page('Document versions', content, session, roles))
        if browser and method == 'POST' and path.startswith('/meetings/') and path.endswith('/documents'):
            denied = browser_require('documents.write')
            if denied: return denied
            meeting_id = path.strip('/').split('/')[1]
            data = browser_body(env)
            try:
                document_id = DB.add_document(org, actor, data.get('title', '').strip(), data.get('content', '').encode('utf-8'), meeting_id, data.get('agenda_item_id') or None, data.get('classification', 'internal'), data.get('content_type') or 'text/plain')
            except (KeyError, ValueError) as exc:
                return html_send(start, '400 Bad Request', page('Upload failed', f'<h1>Upload failed</h1><p>{escape(str(exc))}</p>', session, roles))
            return html_send(start, '303 See Other', '', [('Location', f'/meetings/{meeting_id}')])
        if browser and method == 'POST' and path.startswith('/meetings/') and path.endswith('/agenda'):
            denied = browser_require('agenda.write')
            if denied: return denied
            meeting_id = path.strip('/').split('/')[1]
            data = browser_body(env)
            try:
                DB.add_agenda(org, actor, meeting_id, data.get('title', '').strip(), int(data.get('position', '1')), data.get('parent_id') or None)
            except (KeyError, ValueError) as exc:
                return html_send(start, '400 Bad Request', page('Agenda update failed', f'<h1>Agenda update failed</h1><p>{escape(str(exc))}</p>', session, roles))
            return html_send(start, '303 See Other', '', [('Location', f'/meetings/{meeting_id}#agenda')])
        if browser and method == "GET" and path.startswith("/meetings/") and len(path.strip('/').split('/')) == 2:
            denied = browser_require('meetings.read')
            if denied: return denied
            meeting_id = path.strip('/').split('/')[1]
            meeting = DB.meeting(org, meeting_id)
            if not meeting: return html_send(start, '404 Not Found', page('Not found', '<h1>Meeting not found</h1>', session, roles))
            agenda = DB.execute('SELECT * FROM agenda_items WHERE organisation_id=? AND meeting_id=? AND deleted_at IS NULL ORDER BY position', (org, meeting_id))
            papers = DB.execute('SELECT d.*, v.version_number, v.size_bytes, v.created_at version_created FROM documents d LEFT JOIN document_versions v ON v.document_id=d.id AND v.organisation_id=d.organisation_id WHERE d.organisation_id=? AND d.meeting_id=? AND d.deleted_at IS NULL AND (v.version_number IS NULL OR v.version_number=(SELECT max(v2.version_number) FROM document_versions v2 WHERE v2.document_id=d.id AND v2.organisation_id=?)) ORDER BY d.created_at', (org, meeting_id, org))
            participants = DB.execute('SELECT a.*, COALESCE(p.display_name,u.display_name) name FROM meeting_attendees a JOIN members m ON m.id=a.member_id LEFT JOIN member_profiles p ON p.member_id=m.id LEFT JOIN users u ON u.id=m.user_id WHERE a.organisation_id=? AND a.meeting_id=? AND a.deleted_at IS NULL ORDER BY name', (org, meeting_id))
            q = DB.quorum(org, meeting_id)
            agenda_rows = ''.join(f'<li>{escape(row["title"])} </li>' for row in agenda)
            paper_rows = ''.join(f'<tr><td>{escape(row["title"])}</td><td>{escape(row["classification"])}</td><td>{row["version_number"] or 0}</td><td>{row["size_bytes"] or 0} bytes</td><td><a href="/documents/{row["id"]}/download">View/download</a> · <a href="/documents/{row["id"]}/versions">Versions</a></td></tr>' for row in papers)
            paper_form = ''
            if allowed(roles, 'documents.write'):
                paper_form = f'<form class="card" method="post" action="/meetings/{meeting_id}/documents"><h3>Upload board paper</h3><label>Title <input name="title" required></label><label>Content type <input name="content_type" value="text/plain"></label><label>Classification <select name="classification"><option>internal</option><option>confidential</option><option>public</option></select></label><label>Content <textarea name="content" rows="6" required></textarea></label><button type="submit">Upload paper</button></form>'
            agenda_form = ''
            if allowed(roles, 'agenda.write'):
                agenda_form = f'<form class="card" method="post" action="/meetings/{meeting_id}/agenda"><h3>Add agenda item</h3><label>Title <input name="title" required></label><label>Position <input name="position" type="number" min="1" value="{len(agenda) + 1}" required></label><button type="submit">Add item</button></form>'
            participant_rows = ''.join(f'<tr><td>{escape(row["name"] or "")}</td><td>{"Observer" if row["observer"] else "Member"}</td><td>{escape(row["status"])}</td></tr>' for row in participants)
            join = ''
            if meeting['video_metadata'] and allowed(roles, 'meetings.read'):
                try: join_url = json.loads(meeting['video_metadata']).get('url')
                except (TypeError, ValueError): join_url = None
                if join_url: join = f'<p><a href="{escape(join_url)}">Join Meeting</a></p>'
            content = f'''<h1>{escape(meeting["title"])}</h1><p><strong>Status:</strong> {escape(meeting["status"])} · <strong>When:</strong> {escape(meeting["starts_at"])} · <strong>Location:</strong> {escape(meeting["location"] or "")}</p>{join}<div class="card"><h2>Quorum</h2><p>{q["present"]} / {q["eligible"]} present — <strong>{"MET" if q["met"] else "NOT MET"}</strong> (required {q["required"]})</p></div><nav class="workspace-nav"><a href="#agenda">Agenda</a><a href="#papers">Board papers</a><a href="#attendance">Attendance</a><a href="#conflicts">Conflicts</a><a href="#motions">Motions & voting</a><a href="#resolutions">Resolutions</a><a href="#minutes">Minutes</a><a href="#actions">Actions</a></nav><section id="agenda"><h2>Agenda ({len(agenda)})</h2><ol>{agenda_rows or "<li>No agenda items</li>"}</ol>{agenda_form}</section><section id="papers"><h2>Board papers ({len(list(DB.execute("SELECT 1 FROM documents WHERE organisation_id=? AND meeting_id=? AND deleted_at IS NULL", (org, meeting_id))))})</h2><table><tr><th>Title</th><th>Classification</th><th>Version</th><th>Size</th><th>Access</th></tr>{paper_rows}</table>{paper_form}</section><section id="attendance"><h2>Attendance & participants</h2><table><tr><th>Participant</th><th>Type</th><th>Status</th></tr>{participant_rows}</table></section><section id="conflicts"><h2>Conflicts</h2><p>Conflict declarations and recusal decisions are recorded through the secured API workflow.</p></section><section id="motions"><h2>Motions & voting</h2><p>Open motions, vote eligibility and immutable ballots are available through the secured workflow.</p></section><section id="resolutions"><h2>Resolutions</h2><p>Resolution traceability is maintained for this meeting.</p></section><section id="minutes"><h2>Minutes</h2><p>Structured minutes are maintained per agenda item.</p></section><section id="actions"><h2>Actions</h2><p>Open actions: {len([a for a in DB.action_traceability(org) if a["meeting_id"] == meeting_id and a["status"] in ("open", "in_progress")])}</p></section>'''
            return html_send(start, '200 OK', page('Meeting workspace', content, session, roles))
        if browser and method == "GET" and path == "/members":
            denied = browser_require("members.read")
            if denied: return denied
            members = DB.list_members(org, query.get("search", [None])[0], query.get("status", [None])[0])
            rows = "".join(f"<tr><td>{escape(item['display_name'])}</td><td>{escape(item['email'])}</td><td>{escape(item['title'] or '—')}</td><td>{escape(item['status'])}</td></tr>" for item in members)
            content = f"<h1>Members</h1><form method=\"get\"><label>Search <input name=\"search\" value=\"{escape(query.get('search', [''])[0])}\"></label><button type=\"submit\">Search</button></form>" + (f"<table><thead><tr><th>Name</th><th>Email</th><th>Title</th><th>Status</th></tr></thead><tbody>{rows}</tbody></table>" if rows else "<div class=\"empty\"><h2>No members found</h2><p>Try changing the search or filter.</p></div>")
            return html_send(start, "200 OK", page("Members", content, session))
        if browser and method == "GET" and path == "/activity-log":
            denied = browser_require("audit.read")
            if denied: return denied
            events = DB.execute("SELECT event_type,resource_type,created_at FROM audit_logs WHERE organisation_id=? ORDER BY created_at DESC", (org,)).fetchall()
            rows = "".join(f"<tr><td>{escape(event['created_at'])}</td><td>{escape(event['event_type'])}</td><td>{escape(event['resource_type'])}</td></tr>" for event in events)
            content = "<h1>Activity log</h1>" + (f"<table><thead><tr><th>When</th><th>Event</th><th>Resource</th></tr></thead><tbody>{rows}</tbody></table>" if rows else "<div class=\"empty\"><h2>No activity recorded</h2><p>Audit events will appear here as work is completed.</p></div>")
            return html_send(start, "200 OK", page("Activity log", content, session))
        if browser and method == "GET" and path == "/administration":
            denied = browser_require("administration.manage")
            if denied: return denied
            return html_send(start, "200 OK", page("Administration", "<h1>Administration</h1><div class=\"empty\"><h2>Administration tools are coming soon</h2><p>Organisation settings and configuration will be available here in a later module.</p></div>", session))
        if path == "/members" and method == "GET":
            if not require("members.read"): return send("403 Forbidden", {"error": "forbidden"})
            return send("200 OK", {"members": DB.list_members(org, query.get("search", [None])[0], query.get("status", [None])[0])})
        if path == "/members" and method == "POST":
            if not require("members.manage"): return send("403 Forbidden", {"error": "forbidden"})
            data = body()
            return send("201 Created", {"id": DB.create_member(org, actor, data["email"], data["display_name"], data.get("title"), data.get("profile"))})
        member_parts = path.strip("/").split("/")
        if len(member_parts) >= 2 and member_parts[0] == "members":
            member_id = member_parts[1]
            if len(member_parts) == 2 and method == "GET":
                if not require("members.read"): return send("403 Forbidden", {"error": "forbidden"})
                member = DB.member_profile(org, member_id)
                return send("200 OK", {"member": member}) if member else send("404 Not Found", {"error": "not found"})
            if len(member_parts) == 2 and method in {"PUT", "PATCH"}:
                if not require("members.manage"): return send("403 Forbidden", {"error": "forbidden"})
                member = DB.update_member(org, actor, member_id, body())
                return send("200 OK", {"member": member})
            if len(member_parts) == 3 and member_parts[2] == "deactivate" and method == "POST":
                if not require("members.manage"): return send("403 Forbidden", {"error": "forbidden"})
                DB.deactivate_member(org, actor, member_id); return send("200 OK", {"ok": True})
            if len(member_parts) == 3 and member_parts[2] == "roles" and method == "POST":
                if not require("members.manage"): return send("403 Forbidden", {"error": "forbidden"})
                data = body(); DB.assign_member_role(org, actor, member_id, data["role_id"])
                return send("200 OK", {"ok": True})
            return send("404 Not Found", {"error": "not found"})
        if path == "/home" and method == "GET":
            if not require("meetings.read"): return send("403 Forbidden", {"error": "forbidden"})
            meetings = DB.execute("SELECT * FROM meetings WHERE organisation_id=? AND deleted_at IS NULL", (org,))
            return send("200 OK", {"roles": sorted(roles), "meetings": [dict(x) for x in meetings]})
        if path == "/meetings" and method == "POST":
            if not require("meetings.write"): return send("403 Forbidden", {"error": "forbidden"})
            data = body(); extra = {k: v for k, v in data.items() if k not in {"title", "starts_at", "location"}}
            return send("201 Created", {"id": DB.create_meeting(org, actor, data["title"], data["starts_at"], data.get("location", ""), **extra)})

        parts = path.strip("/").split("/")
        if len(parts) < 3 or parts[0] != "meetings":
            return send("404 Not Found", {"error": "not found"})
        meeting_id, resource = parts[1], parts[2]
        if not DB.meeting(org, meeting_id): return send("404 Not Found", {"error": "not found"})
        data = body() if method == "POST" else {}
        if resource == "transition" and method == "POST":
            if not require("meetings.write"): return send("403 Forbidden", {"error": "forbidden"})
            DB.transition_meeting(org, actor, meeting_id, data["status"]); return send("200 OK", {"ok": True})
        if resource == "agenda" and method == "POST":
            if not require("agenda.write"): return send("403 Forbidden", {"error": "forbidden"})
            return send("201 Created", {"id": DB.add_agenda(org, actor, meeting_id, data["title"], data["position"], data.get("parent_id"), data.get("metadata"))})
        if resource == "documents" and method == "POST":
            if not require("documents.write"): return send("403 Forbidden", {"error": "forbidden"})
            content = data.get("content", "").encode()
            return send("201 Created", {"id": DB.add_document(org, actor, data["title"], content, meeting_id, data.get("agenda_item_id"), data.get("classification", "internal"))})
        if resource == "participants" and method == "POST":
            if not require("meetings.write"): return send("403 Forbidden", {"error": "forbidden"})
            DB.assign_attendee(org, actor, meeting_id, data["member_id"], data.get("observer", False)); return send("200 OK", {"ok": True})
        if resource == "attendance" and method == "POST":
            if not require("attendance.write"): return send("403 Forbidden", {"error": "forbidden"})
            DB.attendance(org, actor, meeting_id, data["member_id"], data["status"]); return send("200 OK", {"ok": True, "quorum": DB.quorum(org, meeting_id)})
        if resource == "rsvp" and method == "POST":
            member_id = data.get("member_id", actor)
            if not own_or("rsvp.write", member_id, "attendance.write"):
                DB.audit(org, actor, "access.denied", "route", path, {"permission": "rsvp.write"})
                return send("403 Forbidden", {"error": "forbidden"})
            DB.rsvp(org, actor, meeting_id, member_id, data["response"]); return send("200 OK", {"ok": True})
        if resource == "conflicts" and len(parts) == 5 and parts[4] == "recusal" and method == "POST":
            if not require("conflicts.manage"): return send("403 Forbidden", {"error": "forbidden"})
            DB.manage_conflict_recusal(org, actor, meeting_id, parts[3], data["status"])
            return send("200 OK", {"ok": True})
        if resource == "conflicts" and method == "POST":
            member_id = data.get("member_id", actor)
            if not own_or("conflicts.write", member_id, "conflicts.manage"): return send("403 Forbidden", {"error": "forbidden"})
            return send("201 Created", {"id": DB.declare_conflict(org, actor, meeting_id, member_id, data["interest"], data["management_action"], data.get("agenda_item_id"))})
        if resource == "motions" and method == "POST" and len(parts) == 3:
            if not require("motions.write"): return send("403 Forbidden", {"error": "forbidden"})
            return send("201 Created", {"id": DB.create_motion(org, actor, meeting_id, data["agenda_item_id"], data["proposer_member_id"], data["text"])})
        if resource == "resolutions" and method == "POST":
            if not require("resolutions.write"): return send("403 Forbidden", {"error": "forbidden"})
            return send("201 Created", {"id": DB.create_resolution(org, actor, meeting_id, data["agenda_item_id"], data["text"], data["outcome"], data.get("motion_id"), data.get("status", "approved"))})
        if resource == "resolutions" and method == "GET":
            if not require("meetings.read"): return send("403 Forbidden", {"error": "forbidden"})
            return send("200 OK", {"resolutions": DB.resolution_traceability(org)})
        if resource == "minutes" and method == "POST" and len(parts) == 3:
            if not require("minutes.write"): return send("403 Forbidden", {"error": "forbidden"})
            return send("201 Created", {"id": DB.save_minutes(org, actor, meeting_id, data["agenda_item_id"], data["body"], data.get("status", "draft"))})
        if resource == "actions" and method == "POST" and len(parts) == 3:
            if not require("actions.write"): return send("403 Forbidden", {"error": "forbidden"})
            return send("201 Created", {"id": DB.create_action(org, actor, meeting_id, data["agenda_item_id"], data["owner_member_id"], data["description"], data.get("due_at"), data.get("resolution_id"), data.get("priority", "normal"))})
        if resource == "actions" and method == "GET":
            if not require("meetings.read"): return send("403 Forbidden", {"error": "forbidden"})
            return send("200 OK", {"actions": DB.action_traceability(org)})
        if resource == "minutes" and len(parts) == 5 and parts[4] == "items" and method == "POST":
            if not require("minutes.write"): return send("403 Forbidden", {"error": "forbidden"})
            return send("201 Created", {"id": DB.add_minute_item(org, actor, parts[3], data["item_type"], data["body"], data["position"])})
        if resource == "motions" and len(parts) == 5 and parts[4] == "votes" and method == "POST":
            motion_id, member_id = parts[3], data.get("member_id", actor)
            if not own_or("votes.write", member_id, "votes.manage"): return send("403 Forbidden", {"error": "forbidden"})
            DB.cast_vote(org, actor, meeting_id, motion_id, member_id, data["choice"]); return send("200 OK", {"ok": True, "tally": DB.vote_tally(org, meeting_id, motion_id)})
        if resource == "motions" and len(parts) == 5 and parts[4] == "close" and method == "POST":
            if not require("motions.write"): return send("403 Forbidden", {"error": "forbidden"})
            return send("200 OK", {"tally": DB.close_motion(org, actor, meeting_id, parts[3])})
        if resource == "actions" and len(parts) == 5 and parts[4] == "evidence" and method == "POST":
            action = DB.execute("SELECT owner_member_id FROM actions WHERE id=? AND meeting_id=? AND organisation_id=? AND deleted_at IS NULL", (parts[3], meeting_id, org)).fetchone()
            if not action: return send("404 Not Found", {"error": "not found"})
            if not own_or("evidence.write", action["owner_member_id"], "evidence.manage"): return send("403 Forbidden", {"error": "forbidden"})
            return send("201 Created", {"id": DB.add_completion_evidence(org, actor, parts[3], data["note"], data.get("storage_key"))})
        if resource == "actions" and len(parts) == 5 and parts[4] == "updates" and method == "POST":
            action = DB.execute("SELECT owner_member_id FROM actions WHERE id=? AND meeting_id=? AND organisation_id=? AND deleted_at IS NULL", (parts[3], meeting_id, org)).fetchone()
            if not action: return send("404 Not Found", {"error": "not found"})
            if not own_or("evidence.write", action["owner_member_id"], "actions.write"): return send("403 Forbidden", {"error": "forbidden"})
            return send("201 Created", {"id": DB.add_action_update(org, actor, parts[3], data["body"], data.get("status"))})
        if resource == "actions" and len(parts) == 5 and parts[4] == "complete" and method == "POST":
            action = DB.execute("SELECT owner_member_id FROM actions WHERE id=? AND meeting_id=? AND organisation_id=? AND deleted_at IS NULL", (parts[3], meeting_id, org)).fetchone()
            if not action: return send("404 Not Found", {"error": "not found"})
            if not own_or("evidence.write", action["owner_member_id"], "actions.write"): return send("403 Forbidden", {"error": "forbidden"})
            DB.complete_action(org, actor, parts[3]); return send("200 OK", {"ok": True})
        return send("404 Not Found", {"error": "not found"})
    except DuplicateVoteError as exc:
        return send("409 Conflict", {"error": str(exc)})
    except (KeyError, TypeError, ValueError) as exc:
        return send("400 Bad Request", {"error": str(exc) or "invalid request"})


if __name__ == "__main__":
    print("Serving on http://127.0.0.1:8000")
    make_server("127.0.0.1", 8000, app).serve_forever()
