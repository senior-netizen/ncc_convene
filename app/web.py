"""Dependency-free WSGI API; every workflow mutation is tenant-bound and audited."""
import json
import os
from html import escape
from http import cookies
from urllib.parse import parse_qs
from wsgiref.simple_server import make_server

from .auth import read_token, token, verify_password
from .db import Database, DuplicateVoteError
from .policy import allowed

DB = Database(os.getenv("APP_DATABASE", ".data/ncc-convene.db"))
SECRET = os.getenv("APP_SESSION_SECRET", "development-only-secret")


def wants_html(env):
    """Keep the JSON API stable while allowing the same URLs to serve browsers."""
    return "text/html" in env.get("HTTP_ACCEPT", "")


def page(title, content, session=None):
    """Render the small, dependency-free browser shell used by the staff portal."""
    navigation = ""
    if session:
        navigation = """
        <nav aria-label="Primary navigation">
          <a href="/dashboard">Dashboard</a><a href="/members">Members</a>
          <a href="/activity-log">Activity log</a><a href="/administration">Administration</a>
          <form action="/logout" method="post"><button type="submit">Log out</button></form>
        </nav>"""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1"><title>{escape(title)} · NCC Convene</title>
    <style>body{{font:16px system-ui,sans-serif;max-width:960px;margin:2rem auto;padding:0 1rem;color:#172033}}nav{{display:flex;gap:1rem;align-items:center;border-bottom:1px solid #d7dce5;padding-bottom:1rem}}nav form{{margin:0}}main{{margin-top:2rem}}table{{border-collapse:collapse;width:100%}}th,td{{text-align:left;padding:.6rem;border-bottom:1px solid #d7dce5}}.empty{{padding:1rem;background:#f4f6f9;border-radius:.25rem}}label{{display:block;margin:.7rem 0}}input{{display:block;padding:.45rem;width:100%;max-width:26rem}}button{{padding:.45rem .7rem}}</style>
    </head><body>{navigation}<main>{content}</main></body></html>"""


def html_send(start, status, content, headers=()):
    start(status, [("Content-Type", "text/html; charset=utf-8"), *headers])
    return [content.encode("utf-8")]


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
            return html_send(start, "303 See Other", "", [("Location", "/dashboard"), ("Set-Cookie", f"session={value}; HttpOnly; SameSite=Lax; Path=/")])
        return send("200 OK", {"ok": True}, [("Set-Cookie", f"session={value}; HttpOnly; SameSite=Lax; Path=/")])

    if path == "/logout" and method == "POST":
        if session:
            DB.audit(session["org"], session["member"], "logout", "session")
        if browser:
            return html_send(start, "303 See Other", "", [("Location", "/login"), ("Set-Cookie", "session=; Max-Age=0; HttpOnly; SameSite=Lax; Path=/")])
        return send("200 OK", {"ok": True}, [("Set-Cookie", "session=; Max-Age=0; HttpOnly; SameSite=Lax; Path=/")])
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
                "SELECT title,starts_at,location,status FROM meetings WHERE organisation_id=? AND deleted_at IS NULL ORDER BY starts_at",
                (org,),
            )]
            if meetings:
                rows = "".join(f"<tr><td>{escape(item['title'])}</td><td>{escape(item['starts_at'])}</td><td>{escape(item['location'])}</td><td>{escape(item['status'])}</td></tr>" for item in meetings)
                content = f"<h1>Dashboard</h1><h2>Meetings</h2><table><thead><tr><th>Meeting</th><th>Starts</th><th>Location</th><th>Status</th></tr></thead><tbody>{rows}</tbody></table>"
            else:
                content = "<h1>Dashboard</h1><div class=\"empty\"><h2>No meetings yet</h2><p>Scheduled meetings will appear here when this module is used.</p></div>"
            return html_send(start, "200 OK", page("Dashboard", content, session))
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
        if resource == "conflicts" and method == "POST":
            member_id = data.get("member_id", actor)
            if not own_or("conflicts.write", member_id, "conflicts.manage"): return send("403 Forbidden", {"error": "forbidden"})
            return send("201 Created", {"id": DB.declare_conflict(org, actor, meeting_id, member_id, data["interest"], data["management_action"], data.get("agenda_item_id"))})
        if resource == "motions" and method == "POST" and len(parts) == 3:
            if not require("motions.write"): return send("403 Forbidden", {"error": "forbidden"})
            return send("201 Created", {"id": DB.create_motion(org, actor, meeting_id, data["agenda_item_id"], data["proposer_member_id"], data["text"])})
        if resource == "resolutions" and method == "POST":
            if not require("resolutions.write"): return send("403 Forbidden", {"error": "forbidden"})
            return send("201 Created", {"id": DB.create_resolution(org, actor, meeting_id, data["agenda_item_id"], data["text"], data["outcome"], data.get("motion_id"))})
        if resource == "minutes" and method == "POST":
            if not require("minutes.write"): return send("403 Forbidden", {"error": "forbidden"})
            return send("201 Created", {"id": DB.save_minutes(org, actor, meeting_id, data["agenda_item_id"], data["body"], data.get("status", "draft"))})
        if resource == "actions" and method == "POST" and len(parts) == 3:
            if not require("actions.write"): return send("403 Forbidden", {"error": "forbidden"})
            return send("201 Created", {"id": DB.create_action(org, actor, meeting_id, data["agenda_item_id"], data["owner_member_id"], data["description"], data.get("due_at"), data.get("resolution_id"))})
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
