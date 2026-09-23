"""Dependency-free WSGI API; every workflow mutation is tenant-bound and audited."""
import json
import secrets
import os
import asyncio
from html import escape
from http import cookies
from pathlib import Path
from urllib.parse import parse_qs, quote
from datetime import datetime, timezone, timedelta
from email import policy
from email.parser import BytesParser
from wsgiref.simple_server import make_server

from .auth import read_token, token, verify_password
from .db import Database, DuplicateVoteError, StaleRevisionError, allowed_meeting_transitions
from .policy import allowed, permissions_for
from .policy import conference_capabilities
from .conference import provider, ConferenceConfigurationError

DB = Database(os.getenv("APP_DATABASE", ".data/ncc-convene.db"))
SECRET = os.getenv("APP_SESSION_SECRET")
if not SECRET:
    if os.getenv("APP_ENV", "development").lower() in {"production", "prod"}:
        raise RuntimeError("APP_SESSION_SECRET must be configured outside development/test")
    SECRET = "development-only-secret"
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
          <form action="/logout" method="post">{csrf_input(session)}<button type="submit">Log out</button></form>
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


def harare_datetime(value):
    """Convert a browser datetime-local value to an explicit Harare offset."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is not None:
        raise ValueError("browser due date must be local Harare time")
    return parsed.replace(tzinfo=timezone(timedelta(hours=2))).isoformat()


def download_name(title):
    """Return an RFC 5987 filename parameter without header injection."""
    safe = "".join(c for c in title if c not in "\r\n").strip() or "document"
    if not safe.lower().endswith(".pdf"):
        safe += ".pdf"
    return "UTF-8''" + quote(safe, safe="")


def login_page(message=""):
    notice = f'<p role="alert">{escape(message)}</p>' if message else ""
    return page("Sign in", f"""<h1>Sign in</h1>{notice}
      <form action="/login" method="post"><label>Email <input name="email" type="email" required></label>
      <label>Password <input name="password" type="password" required></label><button type="submit">Sign in</button></form>""")


def browser_body(env):
    """Parse normal browser forms and multipart file uploads without dependencies."""
    raw = env["wsgi.input"].read(int(env.get("CONTENT_LENGTH", "0") or 0))
    content_type = env.get("CONTENT_TYPE", "")
    if content_type.startswith("multipart/form-data"):
        message = BytesParser(policy=policy.default).parsebytes(
            b"Content-Type: " + content_type.encode() + b"\r\nMIME-Version: 1.0\r\n\r\n" + raw
        )
        values = {}
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            if not name:
                continue
            payload = part.get_payload(decode=True) or b""
            if part.get_filename():
                values[name] = {"filename": part.get_filename(), "content": payload,
                                "content_type": part.get_content_type()}
            else:
                values[name] = payload.decode("utf-8")
        return values
    text = raw.decode("utf-8")
    return {key: values[0] for key, values in parse_qs(text, keep_blank_values=True).items()}


def csrf_token(session):
    return session.get('csrf') if session else None


def csrf_input(session):
    value = csrf_token(session)
    return f'<input type="hidden" name="csrf" value="{escape(value or "")}">' if value else ''


def app(env, start):
    path, method = env["PATH_INFO"], env["REQUEST_METHOD"]
    browser = wants_html(env)
    cookie = cookies.SimpleCookie(env.get("HTTP_COOKIE", ""))
    session = read_token(cookie.get("session").value, SECRET) if cookie.get("session") else None

    def send(status, payload, headers=()):
        start(status, [("Content-Type", "application/json"), *headers])
        return [json.dumps(payload).encode()]

    def error(status, code, message):
        return send(status, {"error": {"code": code, "message": message}})

    def body():
        try:
            raw = env["wsgi.input"].read(int(env.get("CONTENT_LENGTH", "0") or 0))
            value = json.loads(raw or b"{}")
            if not isinstance(value, dict):
                raise ValueError
            return value
        except (json.JSONDecodeError, ValueError):
            raise ValueError("request body must be a JSON object")

    if path == "/api/v1/health" and method == "GET":
        return send("200 OK", {"status": "ok"})
    if path == "/api/v1/meta" and method == "GET":
        return send("200 OK", {"application": "NCC Convene", "api_version": "v1", "engine_version": "0.1.0"})

    if path == "/login" and method == "GET" and browser:
        return html_send(start, "200 OK", login_page())

    if path in {"/login", "/api/v1/session/login"} and method == "POST":
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
        csrf = secrets.token_urlsafe(32)
        value = token({"user": user["id"], "org": member["organisation_id"], "member": member["id"], "csrf": csrf}, SECRET)
        if browser:
            return html_send(start, "303 See Other", "", [("Location", "/dashboard"), ("Set-Cookie", f"session={value}; HttpOnly; SameSite=Lax; Path=/{COOKIE_SECURE}")])
        return send("200 OK", {"ok": True}, [("Set-Cookie", f"session={value}; HttpOnly; SameSite=Lax; Path=/{COOKIE_SECURE}")])

    if path in {"/logout", "/api/v1/session/logout"} and method == "POST":
        if browser:
            data = browser_body(env)
            if not session or not secrets.compare_digest(str(data.get('csrf', '')), session.get('csrf', '')):
                return html_send(start, '403 Forbidden', page('Security check failed', '<h1>Security check failed</h1>', session))
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

    if path == "/api/v1/session/me" and method == "GET":
        member = DB.execute("SELECT m.id,m.title,m.status,COALESCE(p.display_name,u.display_name) AS display_name,o.id AS organisation_id,o.name AS organisation_name FROM members m JOIN users u ON u.id=m.user_id JOIN organisations o ON o.id=m.organisation_id LEFT JOIN member_profiles p ON p.member_id=m.id AND p.organisation_id=m.organisation_id WHERE m.id=? AND m.organisation_id=? AND m.deleted_at IS NULL", (actor, org)).fetchone()
        return send("200 OK", {"member": dict(member), "roles": sorted(roles), "permissions": sorted(permissions_for(roles)), "csrf": session["csrf"]})
    if path == "/api/v1/session/permissions" and method == "GET":
        return send("200 OK", {"permissions": sorted(permissions_for(roles))})
    if path == "/api/v1/meetings" and method == "GET":
        if not allowed(roles, "meetings.read"):
            return error("403 Forbidden", "forbidden", "Permission denied.")
        query = parse_qs(env.get("QUERY_STRING", ""), keep_blank_values=True)
        try:
            limit = int(query.get("limit", ["50"])[0])
            offset = int(query.get("offset", ["0"])[0])
            if not 1 <= limit <= 100 or not 0 <= offset <= 9223372036854775807:
                raise ValueError
        except (ValueError, OverflowError):
            return error("400 Bad Request", "invalid_pagination", "limit must be 1-100 and offset must be a non-negative SQLite integer.")
        meetings = [dict(row) for row in DB.execute("SELECT id,title,starts_at,location,status,quorum_percent,video_provider FROM meetings WHERE organisation_id=? AND deleted_at IS NULL ORDER BY starts_at,id LIMIT ? OFFSET ?", (org, limit, offset))]
        return send("200 OK", {"items": meetings, "limit": limit, "offset": offset})
    api_parts = path.strip("/").split("/")
    if len(api_parts) == 5 and api_parts[:3] == ["api", "v1", "meetings"] and api_parts[4] == "agenda" and method == "GET":
        if not allowed(roles, "meetings.read"):
            return error("403 Forbidden", "forbidden", "Permission denied.")
        meeting_id = api_parts[3]
        if not DB.meeting(org, meeting_id):
            return error("404 Not Found", "not_found", "Meeting not found.")
        agenda = [dict(row) for row in DB.execute("SELECT id,parent_id,title,metadata,position,created_at,updated_at FROM agenda_items WHERE organisation_id=? AND meeting_id=? AND deleted_at IS NULL ORDER BY position,id", (org, meeting_id))]
        for item in agenda:
            item["metadata"] = json.loads(item["metadata"] or "{}")
        return send("200 OK", {"items": agenda})
    if len(api_parts) == 4 and api_parts[:3] == ["api", "v1", "meetings"] and method == "GET":
        if not allowed(roles, "meetings.read"):
            return error("403 Forbidden", "forbidden", "Permission denied.")
        meeting_id = api_parts[3]
        meeting = DB.meeting(org, meeting_id)
        if not meeting:
            return send("404 Not Found", {"error": {"code": "not_found", "message": "Meeting not found."}})
        q = DB.quorum(org, meeting_id)
        participants = DB.execute("SELECT count(*) AS total, sum(CASE WHEN status='present' THEN 1 ELSE 0 END) AS present FROM meeting_attendees WHERE organisation_id=? AND meeting_id=? AND deleted_at IS NULL", (org, meeting_id)).fetchone()
        agenda = DB.execute("SELECT id,parent_id,title,metadata,position,created_at,updated_at FROM agenda_items WHERE organisation_id=? AND meeting_id=? AND deleted_at IS NULL ORDER BY position,id", (org, meeting_id)).fetchall()
        agenda_items = [dict(item) for item in agenda]
        for item in agenda_items:
            item["metadata"] = json.loads(item["metadata"] or "{}")
        public_meeting = {key: meeting[key] for key in ("id", "committee_id", "title", "starts_at", "location", "status", "recurrence_rule", "quorum_percent", "video_provider", "video_metadata", "created_at", "updated_at")}
        if public_meeting["video_metadata"]:
            public_meeting["video_metadata"] = json.loads(public_meeting["video_metadata"])
        return send("200 OK", {"meeting": public_meeting, "quorum": dict(q), "participants": {"total": participants["total"] or 0, "present": participants["present"] or 0}, "agenda": agenda_items, "allowed_transitions": allowed_meeting_transitions(meeting["status"])})

    if len(api_parts) == 5 and api_parts[:3] == ["api", "v1", "meetings"] and api_parts[4] == "workspace" and method == "GET":
        if not allowed(roles, "meetings.read"):
            return error("403 Forbidden", "forbidden", "Permission denied.")
        meeting_id = api_parts[3]
        meeting = DB.meeting(org, meeting_id)
        if not meeting:
            return error("404 Not Found", "not_found", "Meeting not found.")
        def rows(sql, params=(org, meeting_id)):
            return [dict(row) for row in DB.execute(sql, params)]
        agenda = rows("SELECT id,parent_id,title,metadata,position,created_at,updated_at FROM agenda_items WHERE organisation_id=? AND meeting_id=? AND deleted_at IS NULL ORDER BY position,id")
        for item in agenda:
            item["metadata"] = json.loads(item["metadata"] or "{}")
        participants = rows("SELECT a.id,a.member_id,a.status,a.observer,a.created_at,a.updated_at,COALESCE(p.display_name,u.display_name) AS display_name FROM meeting_attendees a JOIN members m ON m.id=a.member_id AND m.organisation_id=a.organisation_id LEFT JOIN member_profiles p ON p.member_id=m.id AND p.organisation_id=m.organisation_id LEFT JOIN users u ON u.id=m.user_id WHERE a.organisation_id=? AND a.meeting_id=? AND a.deleted_at IS NULL ORDER BY display_name,a.id")
        documents = rows("SELECT d.id,d.meeting_id,d.agenda_item_id,d.title,d.classification,d.status,d.created_at,d.updated_at,v.version_number,v.content_type,v.size_bytes,v.sha256,v.created_at AS version_created_at FROM documents d LEFT JOIN document_versions v ON v.document_id=d.id AND v.organisation_id=d.organisation_id AND v.version_number=(SELECT max(v2.version_number) FROM document_versions v2 WHERE v2.document_id=d.id AND v2.organisation_id=d.organisation_id) WHERE d.organisation_id=? AND d.meeting_id=? AND d.deleted_at IS NULL ORDER BY d.created_at,d.id") if allowed(roles, "documents.read") else []
        conflicts = rows("SELECT id,agenda_item_id,member_id,interest,management_action,status,created_at,updated_at FROM conflict_declarations WHERE organisation_id=? AND meeting_id=? AND deleted_at IS NULL ORDER BY created_at,id")
        motions = rows("SELECT id,agenda_item_id,proposer_member_id,text,status,created_at,updated_at FROM motions WHERE organisation_id=? AND meeting_id=? AND deleted_at IS NULL ORDER BY created_at,id")
        for motion in motions:
            motion["tally"] = DB.vote_tally(org, meeting_id, motion["id"])
        resolutions = DB.resolution_traceability(org, meeting_id=meeting_id)
        minutes = rows("SELECT id,agenda_item_id,body,status,created_at,updated_at FROM minutes WHERE organisation_id=? AND meeting_id=? AND deleted_at IS NULL ORDER BY created_at,id")
        for minute in minutes:
            minute["items"] = rows("SELECT id,item_type,body,position,created_at,updated_at FROM minute_items WHERE organisation_id=? AND minute_id=? AND deleted_at IS NULL ORDER BY position,id", (org, minute["id"]))
        actions = DB.action_traceability(org, meeting_id=meeting_id)
        public_meeting = {key: meeting[key] for key in ("id", "committee_id", "title", "starts_at", "location", "status", "recurrence_rule", "quorum_percent", "video_provider", "created_at", "updated_at")}
        return send("200 OK", {"meeting": public_meeting, "agenda": agenda, "participants": participants,
            "quorum": DB.quorum(org, meeting_id), "documents": documents, "conflicts": conflicts,
            "motions": motions, "resolutions": resolutions, "minutes": minutes, "actions": actions})

    # Conference routes intentionally derive organisation, identity and grants
    # from the signed application session. No browser-supplied room or role is used.
    if len(api_parts) >= 5 and api_parts[:3] == ["api", "v1", "meetings"] and api_parts[4] == "conference":
        meeting_id = api_parts[3]
        if not DB.meeting(org, meeting_id): return error("404 Not Found", "not_found", "Meeting not found.")
        caps = conference_capabilities(roles)
        if "conference.join" not in caps: return error("403 Forbidden", "forbidden", "Conference access denied.")
        def conference_body():
            if method not in {"POST", "PATCH", "DELETE"}: return {}
            if not secrets.compare_digest(env.get("HTTP_X_CSRF_TOKEN", ""), session.get("csrf", "")):
                raise PermissionError("CSRF validation failed")
            return body()
        try:
            suffix = api_parts[5:]
            if not suffix and method == "GET":
                row = DB.conference(org, meeting_id)
                participant = DB.conference_participant(org, meeting_id, row["id"], actor) if row else None
                queue = []
                if row and "conference.moderate" in caps:
                    queue = [dict(x) for x in DB.execute("SELECT member_id,admission_state,blocked,can_moderate,can_present,hand_raised_at,requested_at FROM conference_participants WHERE organisation_id=? AND meeting_id=? AND conference_session_id=? ORDER BY requested_at", (org, meeting_id, row["id"]))]
                return send("200 OK", {"conference": dict(row) if row else None, "participant": dict(participant) if participant else None, "capabilities": sorted(caps), "participants": queue, "provider_configured": provider.configured})
            data = conference_body()
            if suffix == ["start"] and method == "POST":
                if "conference.moderate" not in caps: return error("403 Forbidden", "forbidden", "Moderation denied.")
                return send("201 Created", {"conference": DB.start_conference(org, actor, meeting_id, data.get("admission_required", True))})
            row = DB.conference(org, meeting_id, data.get("session_id"))
            if not row: return error("404 Not Found", "not_found", "Conference not found.")
            session_id = row["id"]
            if suffix == ["admission"] and method == "POST":
                return send("200 OK", {"participant": DB.request_admission(org, actor, meeting_id, session_id)})
            if suffix == ["token"] and method == "POST":
                p = DB.conference_participant(org, meeting_id, session_id, actor)
                if row["state"] != "active" or not p or p["admission_state"] != "admitted" or p["blocked"]:
                    return error("403 Forbidden", "not_admitted", "Participant is not admitted.")
                identity = f"{session_id}:{actor}"
                member = DB.member_profile(org, actor)
                jwt = provider.participant_token(row["provider_room"], identity, member["display_name"], can_publish=True, metadata=json.dumps({"member_id": actor, "can_present": bool(p["can_present"]), "can_moderate": bool(p["can_moderate"])}))
                return send("200 OK", {"token": jwt, "url": provider.url, "expires_in": 300, "identity": identity}, [("Cache-Control", "no-store, private"), ("Pragma", "no-cache")])
            if suffix == ["lock"] and method == "POST":
                if "conference.moderate" not in caps: return error("403 Forbidden", "forbidden", "Moderation denied.")
                DB.set_conference_lock(org, actor, meeting_id, session_id, bool(data["locked"])); return send("200 OK", {"ok": True})
            if suffix == ["moderate"] and method == "POST":
                updated = DB.moderate_participant(org, actor, meeting_id, session_id, data["member_id"], data["action"])
                if data["action"] == "remove" and provider.configured:
                    asyncio.run(provider.remove(row["provider_room"], f"{session_id}:{data['member_id']}"))
                return send("200 OK", {"participant": updated})
            if suffix == ["end"] and method == "POST":
                if "conference.moderate" not in caps: return error("403 Forbidden", "forbidden", "Moderation denied.")
                if provider.configured: asyncio.run(provider.delete_room(row["provider_room"]))
                DB.end_conference(org, actor, meeting_id, session_id); return send("200 OK", {"ok": True})
            return error("404 Not Found", "not_found", "Conference endpoint not found.")
        except PermissionError as exc:
            return error("403 Forbidden", "forbidden", str(exc))
        except ConferenceConfigurationError as exc:
            return error("503 Service Unavailable", "provider_unavailable", str(exc))

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
        return html_send(start, "403 Forbidden", page("Access denied", "<h1>Access denied</h1><p>You do not have permission to view this page.</p>", session, roles))

    def browser_csrf(data):
        if not session.get('csrf') or not secrets.compare_digest(str(data.get('csrf', '')), session['csrf']):
            return html_send(start, '403 Forbidden', page('Security check failed', '<h1>Security check failed</h1><p>Reload the page and try again.</p>', session, roles))
        return None

    def action_access(action_id, permission):
        action = DB.execute('SELECT * FROM actions WHERE id=? AND organisation_id=? AND deleted_at IS NULL', (action_id, org)).fetchone()
        if not action:
            return False
        owner_allowed = action['owner_member_id'] == actor and (allowed(roles, permission) or allowed(roles, 'evidence.write'))
        return owner_allowed or allowed(roles, 'actions.write')

    def api_csrf():
        expected = session.get("csrf")
        supplied = env.get("HTTP_X_CSRF_TOKEN")
        return bool(expected and supplied and secrets.compare_digest(supplied, expected))

    # Same-origin is not, by itself, a mutation defence. The Next.js client
    # obtains this value from the authenticated session response and every
    # legacy JSON meeting mutation must prove possession of it. Browser HTML
    # forms continue to use their existing signed-session form field below.
    if not browser and method in {"POST", "PUT", "PATCH", "DELETE"} and path.startswith("/meetings") and not api_csrf():
        return error("403 Forbidden", "csrf_failed", "CSRF validation failed.")

    try:
        # Organisation identity is deliberately never taken from a request value: all
        # member operations are bound to the organisation in the signed session.
        query = parse_qs(env.get("QUERY_STRING", ""), keep_blank_values=True)
        paper_parts = path.strip("/").split("/")
        if paper_parts[:3] == ["api", "v1", "papers"]:
            if not allowed(roles, "documents.read"):
                return error("403 Forbidden", "forbidden", "Paper access denied.")
            if len(paper_parts) == 3 and method == "GET":
                meeting_id = query.get("meeting_id", [None])[0]
                mine = query.get("assigned_to_me", ["0"])[0] == "1"
                return send("200 OK", {"items": DB.list_board_papers(org, meeting_id, actor if mine else None, mine)})
            if len(paper_parts) == 3 and method == "POST":
                if not allowed(roles, "papers.submit"): return error("403 Forbidden", "forbidden", "Paper submission denied.")
                if not api_csrf(): return error("403 Forbidden", "csrf_failed", "CSRF validation failed.")
                data = body(); paper_id = DB.create_board_paper(org, actor, data["meeting_id"], data["agenda_item_id"], data, data.get("content", "").encode(), data.get("classification", "confidential"), data.get("content_type", "application/pdf"))
                return send("201 Created", {"id": paper_id, "paper": DB.board_paper(org, paper_id)})
            if len(paper_parts) < 4: return error("404 Not Found", "not_found", "Paper endpoint not found.")
            paper_id = paper_parts[3]; paper = DB.board_paper(org, paper_id)
            if not paper: return error("404 Not Found", "not_found", "Paper not found.")
            if len(paper_parts) == 4 and method == "GET":
                comments = [dict(row) for row in DB.execute("SELECT id,revision_id,author_member_id,body,created_at FROM paper_review_comments WHERE organisation_id=? AND paper_id=? AND deleted_at IS NULL ORDER BY created_at,id", (org, paper_id))]
                reviews = [dict(row) for row in DB.execute("SELECT id,revision_id,reviewer_member_id,deadline,status,assigned_by,assigned_at,decided_at,reason FROM paper_review_assignments WHERE organisation_id=? AND paper_id=? ORDER BY assigned_at,id", (org, paper_id))]
                return send("200 OK", {"paper": paper, "comments": comments, "reviews": reviews})
            if method != "POST": return error("405 Method Not Allowed", "method_not_allowed", "Method not allowed.")
            if not api_csrf(): return error("403 Forbidden", "csrf_failed", "CSRF validation failed.")
            data = body(); suffix = paper_parts[4:]
            if suffix == ["submit"]:
                if not allowed(roles, "papers.submit"): return error("403 Forbidden", "forbidden", "Paper submission denied.")
                return send("200 OK", {"paper": DB.transition_board_paper(org, actor, paper_id, "submit", data["revision_id"], int(data["lock_version"]), data.get("reason"))})
            if suffix == ["revision"]:
                return send("201 Created", {"paper": DB.revise_board_paper(org, actor, paper_id, data, data.get("content", "").encode(), data["revision_id"], data.get("content_type", "application/pdf"))})
            if suffix == ["reviewers"]:
                if not allowed(roles, "papers.review"): return error("403 Forbidden", "forbidden", "Review assignment denied.")
                assignment_id = DB.assign_paper_reviewer(org, actor, paper_id, data["reviewer_member_id"], data.get("deadline"))
                return send("201 Created", {"id": assignment_id, "paper": DB.board_paper(org, paper_id)})
            if suffix == ["decision"]:
                if not allowed(roles, "papers.review"): return error("403 Forbidden", "forbidden", "Review decision denied.")
                return send("200 OK", {"paper": DB.review_board_paper(org, actor, paper_id, data["decision"], data["revision_id"], data["reason"])})
            if suffix == ["comments"]:
                return send("201 Created", {"id": DB.add_paper_comment(org, actor, paper_id, data["revision_id"], data["body"])})
            return error("404 Not Found", "not_found", "Paper endpoint not found.")
        if paper_parts[:4] == ["api", "v1", "board-packs", "publish"] and method == "POST":
            if not allowed(roles, "papers.publish"): return error("403 Forbidden", "forbidden", "Publication denied.")
            if not api_csrf(): return error("403 Forbidden", "csrf_failed", "CSRF validation failed.")
            data = body(); return send("201 Created", {"id": DB.publish_board_pack(org, actor, data["meeting_id"], data["paper_ids"], data.get("label"))})
        if paper_parts[:3] == ["api", "v1", "annotations"]:
            if not allowed(roles, "annotations.write"): return error("403 Forbidden", "forbidden", "Annotation access denied.")
            if len(paper_parts) == 3 and method == "GET":
                revision_id = query.get("revision_id", [None])[0]
                items = [dict(row) for row in DB.execute("SELECT id,document_id,revision_id,kind,locator,body,shared,created_at,updated_at FROM document_annotations WHERE organisation_id=? AND member_id=? AND revision_id=? AND deleted_at IS NULL ORDER BY created_at,id", (org, actor, revision_id))]
                return send("200 OK", {"items": items})
            if len(paper_parts) == 3 and method == "POST":
                if not api_csrf(): return error("403 Forbidden", "csrf_failed", "CSRF validation failed.")
                data = body(); return send("201 Created", {"id": DB.add_annotation(org, actor, data["document_id"], data["revision_id"], data["kind"], data["locator"], data.get("body"))})
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
        document_parts = path.strip('/').split('/')
        if len(document_parts) == 3 and document_parts[0] == 'documents' and document_parts[2] == 'download' and method == 'GET':
            if not allowed(roles, 'documents.read'):
                return error('403 Forbidden', 'forbidden', 'Permission denied.')
            document_id = document_parts[1]
            requested = query.get('version', [None])[0]
            try:
                version = int(requested) if requested is not None else None
                if version is not None and not 1 <= version <= 9223372036854775807: raise ValueError
            except (ValueError, OverflowError):
                return error('400 Bad Request', 'invalid_version', 'version must be a positive SQLite integer.')
            version_clause = 'v.version_number=?' if version is not None else 'v.version_number=(SELECT max(v2.version_number) FROM document_versions v2 WHERE v2.document_id=d.id AND v2.organisation_id=?)'
            params = (document_id, org, version) if version is not None else (document_id, org, org)
            document = DB.execute(f'SELECT d.title,v.storage_key,v.content_type,v.version_number FROM documents d JOIN document_versions v ON v.document_id=d.id AND v.organisation_id=d.organisation_id WHERE d.id=? AND d.organisation_id=? AND d.deleted_at IS NULL AND {version_clause}', params).fetchone()
            if not document or not Path(document['storage_key']).is_file():
                return error('404 Not Found', 'not_found', 'Document version is unavailable.')
            content_type = document['content_type'] or 'application/octet-stream'
            return binary_send(start, '200 OK', Path(document['storage_key']).read_bytes(), content_type, [('Content-Disposition', f"inline; filename*={download_name(document['title'])}")])
        if len(document_parts) == 3 and document_parts[0] == 'documents' and document_parts[2] == 'versions' and method == 'GET':
            if not allowed(roles, 'documents.read'):
                return error('403 Forbidden', 'forbidden', 'Permission denied.')
            document_id = document_parts[1]
            exists = DB.execute('SELECT 1 FROM documents WHERE id=? AND organisation_id=? AND deleted_at IS NULL', (document_id, org)).fetchone()
            if not exists: return error('404 Not Found', 'not_found', 'Document not found.')
            versions = DB.execute('SELECT version_number,size_bytes,sha256,content_type,created_at FROM document_versions WHERE document_id=? AND organisation_id=? ORDER BY version_number DESC', (document_id, org)).fetchall()
            if not browser:
                return send('200 OK', {'items': [dict(v) for v in versions]})
            rows = ''.join(f'<tr><td>{v["version_number"]}</td><td>{v["size_bytes"]} bytes</td><td>{escape(v["content_type"] or "")}</td><td>{escape(v["created_at"])}</td><td><code>{escape(v["sha256"][:12])}…</code></td><td><a href="/documents/{document_id}/download?version={v["version_number"]}">Download</a></td></tr>' for v in versions)
            content = f'<h1>Document versions</h1><table><tr><th>Version</th><th>Size</th><th>Type</th><th>Uploaded</th><th>SHA-256</th><th>File</th></tr>{rows}</table>'
            return html_send(start, '200 OK', page('Document versions', content, session, roles))
        if browser and method == 'POST' and path.startswith('/meetings/') and path.endswith('/documents'):
            denied = browser_require('documents.write')
            if denied: return denied
            meeting_id = path.strip('/').split('/')[1]
            data = browser_body(env)
            denied = browser_csrf(data)
            if denied: return denied
            upload = data.get('file') or {}
            content = upload.get('content') if isinstance(upload, dict) else None
            title = data.get('title', '').strip() or (upload.get('filename') if isinstance(upload, dict) else '')
            try:
                document_id = DB.add_document(org, actor, title, content, meeting_id, data.get('agenda_item_id') or None, data.get('classification', 'internal'), upload.get('content_type') if isinstance(upload, dict) else data.get('content_type'))
            except (KeyError, TypeError, ValueError) as exc:
                return html_send(start, '400 Bad Request', page('Upload failed', f'<h1>Upload failed</h1><p>{escape(str(exc))}</p>', session, roles))
            return html_send(start, '303 See Other', '', [('Location', f'/meetings/{meeting_id}')])
        if browser and method == 'POST' and path.startswith('/documents/') and path.endswith('/replace'):
            denied = browser_require('documents.write')
            if denied: return denied
            document_id = path.strip('/').split('/')[1]
            data = browser_body(env)
            denied = browser_csrf(data)
            if denied: return denied
            upload = data.get('file') or {}
            try:
                DB.replace_document(org, actor, document_id, upload.get('content'), upload.get('content_type'))
                meeting = DB.execute('SELECT meeting_id FROM documents WHERE id=? AND organisation_id=?', (document_id, org)).fetchone()
                return html_send(start, '303 See Other', '', [('Location', f'/meetings/{meeting["meeting_id"]}')])
            except (KeyError, TypeError, ValueError) as exc:
                return html_send(start, '400 Bad Request', page('Replacement failed', f'<h1>Replacement failed</h1><p>{escape(str(exc))}</p>', session, roles))
        if browser and method == 'POST' and path.startswith('/meetings/') and path.endswith('/agenda'):
            denied = browser_require('agenda.write')
            if denied: return denied
            meeting_id = path.strip('/').split('/')[1]
            data = browser_body(env)
            denied = browser_csrf(data)
            if denied: return denied
            try:
                DB.add_agenda(org, actor, meeting_id, data.get('title', '').strip(), int(data.get('position', '1')), data.get('parent_id') or None)
            except (KeyError, ValueError) as exc:
                return html_send(start, '400 Bad Request', page('Agenda update failed', f'<h1>Agenda update failed</h1><p>{escape(str(exc))}</p>', session, roles))
            return html_send(start, '303 See Other', '', [('Location', f'/meetings/{meeting_id}#agenda')])
        if browser and method == 'POST' and path.startswith('/actions/'):
            action_id = path.strip('/').split('/')[1]
            permission = 'evidence.write' if path.endswith('/evidence') else 'actions.write'
            if not action_access(action_id, permission):
                return html_send(start, '403 Forbidden', page('Access denied', '<h1>Access denied</h1>', session, roles))
            data = browser_body(env)
            denied = browser_csrf(data)
            if denied: return denied
            action_id = path.strip('/').split('/')[1]
            try:
                if path.endswith('/evidence'):
                    upload = data.get('file') or {}
                    storage_key = None
                    if isinstance(upload, dict) and upload.get('content'):
                        DB._validate_upload(upload['content'], upload.get('content_type'))
                        storage_path = DB._safe_upload_path(org, action_id)
                        storage_path.write_bytes(upload['content'])
                        storage_key = str(storage_path)
                    DB.add_completion_evidence(org, actor, action_id, data.get('note') or upload.get('filename') or 'Evidence', storage_key)
                elif path.endswith('/complete'):
                    DB.complete_action(org, actor, action_id)
                elif path.endswith('/updates'):
                    DB.add_action_update(org, actor, action_id, data['body'], data.get('status') or None)
                else: raise ValueError('unsupported action workflow')
            except (KeyError, TypeError, ValueError) as exc:
                return html_send(start, '400 Bad Request', page('Action update failed', f'<h1>Action update failed</h1><p>{escape(str(exc))}</p>', session, roles))
            meeting = DB.execute('SELECT meeting_id FROM actions WHERE id=? AND organisation_id=?', (action_id, org)).fetchone()
            return html_send(start, '303 See Other', '', [('Location', f'/meetings/{meeting["meeting_id"]}')])
        if browser and method == 'POST' and path.startswith('/meetings/'):
            data = browser_body(env)
            denied = browser_csrf(data)
            if denied: return denied
            meeting_id = path.strip('/').split('/')[1]
            try:
                if path.endswith('/transition'):
                    if not require('meetings.write'): return html_send(start, '403 Forbidden', page('Access denied', '<h1>Access denied</h1>', session, roles))
                    DB.transition_meeting(org, actor, meeting_id, data['status'])
                elif path.endswith('/participants'):
                    if not require('meetings.write'): return html_send(start, '403 Forbidden', page('Access denied', '<h1>Access denied</h1>', session, roles))
                    DB.assign_attendee(org, actor, meeting_id, data['member_id'], data.get('observer') == '1')
                elif path.endswith('/attendance'):
                    if not require('attendance.write'): return html_send(start, '403 Forbidden', page('Access denied', '<h1>Access denied</h1>', session, roles))
                    DB.attendance(org, actor, meeting_id, data['member_id'], data['status'])
                elif path.endswith('/rsvp'):
                    member_id = data.get('member_id', actor)
                    if not own_or('rsvp.write', member_id, 'attendance.write'): return html_send(start, '403 Forbidden', page('Access denied', '<h1>Access denied</h1>', session, roles))
                    DB.rsvp(org, actor, meeting_id, member_id, data['response'])
                elif path.endswith('/conflicts'):
                    member_id = data.get('member_id', actor)
                    if not own_or('conflicts.write', member_id, 'conflicts.manage'): return html_send(start, '403 Forbidden', page('Access denied', '<h1>Access denied</h1>', session, roles))
                    DB.declare_conflict(org, actor, meeting_id, member_id, data['interest'], data['management_action'], data.get('agenda_item_id') or None)
                elif '/conflicts/' in path and path.endswith('/recusal'):
                    if not require('conflicts.manage'): return html_send(start, '403 Forbidden', page('Access denied', '<h1>Access denied</h1>', session, roles))
                    DB.manage_conflict_recusal(org, actor, meeting_id, path.strip('/').split('/')[3], data['status'])
                elif path.endswith('/motions'):
                    if not require('motions.write'): return html_send(start, '403 Forbidden', page('Access denied', '<h1>Access denied</h1>', session, roles))
                    DB.create_motion(org, actor, meeting_id, data['agenda_item_id'], data['proposer_member_id'], data['text'])
                elif '/motions/' in path and path.endswith('/votes'):
                    motion_id = path.strip('/').split('/')[3]; member_id = data.get('member_id', actor)
                    if not own_or('votes.write', member_id, 'votes.manage'): return html_send(start, '403 Forbidden', page('Access denied', '<h1>Access denied</h1>', session, roles))
                    DB.cast_vote(org, actor, meeting_id, motion_id, member_id, data['choice'])
                elif '/motions/' in path and path.endswith('/close'):
                    if not require('votes.manage'): return html_send(start, '403 Forbidden', page('Access denied', '<h1>Access denied</h1>', session, roles))
                    DB.close_motion(org, actor, meeting_id, path.strip('/').split('/')[3])
                elif path.endswith('/resolutions'):
                    if not require('resolutions.write'): return html_send(start, '403 Forbidden', page('Access denied', '<h1>Access denied</h1>', session, roles))
                    DB.create_resolution(org, actor, meeting_id, data['agenda_item_id'], data['text'], data['outcome'], data.get('motion_id') or None, data.get('status', 'draft'))
                elif '/minutes/' in path and path.endswith('/items'):
                    if not require('minutes.write'): return html_send(start, '403 Forbidden', page('Access denied', '<h1>Access denied</h1>', session, roles))
                    minute_id = path.strip('/').split('/')[3]
                    DB.add_minute_item(org, actor, minute_id, data['item_type'], data['body'], int(data.get('position', '0')), meeting_id)
                elif path.endswith('/minutes'):
                    if not require('minutes.write'): return html_send(start, '403 Forbidden', page('Access denied', '<h1>Access denied</h1>', session, roles))
                    DB.save_minutes(org, actor, meeting_id, data['agenda_item_id'], data['body'], data.get('status', 'draft'))
                elif path.endswith('/actions'):
                    if not require('actions.write'): return html_send(start, '403 Forbidden', page('Access denied', '<h1>Access denied</h1>', session, roles))
                    due_at = harare_datetime(data['due_at']) if data.get('due_at') else None
                    DB.create_action(org, actor, meeting_id, data['agenda_item_id'], data['owner_member_id'], data['description'], due_at, data.get('resolution_id') or None, data.get('priority', 'normal'))
                elif '/actions/' in path and path.endswith('/updates'):
                    if not require('actions.write'): return html_send(start, '403 Forbidden', page('Access denied', '<h1>Access denied</h1>', session, roles))
                    DB.add_action_update(org, actor, path.strip('/').split('/')[3], data['body'], data.get('status') or None)
                elif '/actions/' in path and path.endswith('/evidence'):
                    if not require('actions.write'): return html_send(start, '403 Forbidden', page('Access denied', '<h1>Access denied</h1>', session, roles))
                    upload = data.get('file') or {}
                    DB.add_completion_evidence(org, actor, path.strip('/').split('/')[3], data.get('note') or upload.get('filename') or 'Evidence', upload.get('storage_key') if isinstance(upload, dict) else None)
                elif '/actions/' in path and path.endswith('/complete'):
                    if not require('actions.write'): return html_send(start, '403 Forbidden', page('Access denied', '<h1>Access denied</h1>', session, roles))
                    DB.complete_action(org, actor, path.strip('/').split('/')[3])
                else:
                    raise ValueError('unsupported browser workflow action')
            except DuplicateVoteError as exc:
                return html_send(start, '409 Conflict', page('Vote not accepted', f'<h1>Vote not accepted</h1><p>{escape(str(exc))}</p>', session, roles))
            except (KeyError, ValueError) as exc:
                return html_send(start, '400 Bad Request', page('Workflow update failed', f'<h1>Workflow update failed</h1><p>{escape(str(exc))}</p><p><a href="/meetings/{meeting_id}">Return to meeting</a></p>', session, roles))
            return html_send(start, '303 See Other', '', [('Location', f'/meetings/{meeting_id}')])
        if browser and method == "GET" and path.startswith("/meetings/") and len(path.strip('/').split('/')) == 2:
            denied = browser_require('meetings.read')
            if denied: return denied
            meeting_id = path.strip('/').split('/')[1]
            meeting = DB.meeting(org, meeting_id)
            if not meeting: return html_send(start, '404 Not Found', page('Not found', '<h1>Meeting not found</h1>', session, roles))
            agenda = list(DB.execute('SELECT * FROM agenda_items WHERE organisation_id=? AND meeting_id=? AND deleted_at IS NULL ORDER BY position', (org, meeting_id)))
            papers = list(DB.execute('SELECT d.*, v.version_number, v.size_bytes, v.created_at version_created FROM documents d LEFT JOIN document_versions v ON v.document_id=d.id AND v.organisation_id=d.organisation_id WHERE d.organisation_id=? AND d.meeting_id=? AND d.deleted_at IS NULL AND (v.version_number IS NULL OR v.version_number=(SELECT max(v2.version_number) FROM document_versions v2 WHERE v2.document_id=d.id AND v2.organisation_id=?)) ORDER BY d.created_at', (org, meeting_id, org)))
            participants = list(DB.execute('SELECT a.*, COALESCE(p.display_name,u.display_name) name FROM meeting_attendees a JOIN members m ON m.id=a.member_id LEFT JOIN member_profiles p ON p.member_id=m.id LEFT JOIN users u ON u.id=m.user_id WHERE a.organisation_id=? AND a.meeting_id=? AND a.deleted_at IS NULL ORDER BY name', (org, meeting_id)))
            organisation_members = list(DB.execute('SELECT m.id member_id, COALESCE(p.display_name,u.display_name) name FROM members m LEFT JOIN member_profiles p ON p.member_id=m.id LEFT JOIN users u ON u.id=m.user_id WHERE m.organisation_id=? AND m.status=\'active\' AND m.deleted_at IS NULL ORDER BY name', (org,)))
            conflicts = list(DB.execute('SELECT c.*, COALESCE(p.display_name,u.display_name) name, a.title agenda_title FROM conflict_declarations c JOIN members m ON m.id=c.member_id LEFT JOIN member_profiles p ON p.member_id=m.id LEFT JOIN users u ON u.id=m.user_id LEFT JOIN agenda_items a ON a.id=c.agenda_item_id WHERE c.organisation_id=? AND c.meeting_id=? AND c.deleted_at IS NULL ORDER BY c.created_at', (org, meeting_id)))
            motions = list(DB.execute('SELECT m.*, a.title agenda_title FROM motions m JOIN agenda_items a ON a.id=m.agenda_item_id WHERE m.organisation_id=? AND m.meeting_id=? AND m.deleted_at IS NULL ORDER BY m.created_at', (org, meeting_id)))
            resolutions = list(DB.execute('SELECT * FROM resolution_traceability WHERE organisation_id=? AND meeting_id=?', (org, meeting_id)))
            actions = list(DB.execute('SELECT a.*, COALESCE(p.display_name,u.display_name) owner_name FROM action_traceability a JOIN members m ON m.id=a.owner_member_id LEFT JOIN member_profiles p ON p.member_id=m.id LEFT JOIN users u ON u.id=m.user_id WHERE a.organisation_id=? AND a.meeting_id=? ORDER BY a.action_year,a.action_number', (org, meeting_id)))
            q = DB.quorum(org, meeting_id)
            minutes = list(DB.execute('SELECT m.*, a.title agenda_title FROM minutes m JOIN agenda_items a ON a.id=m.agenda_item_id WHERE m.organisation_id=? AND m.meeting_id=? AND m.deleted_at IS NULL ORDER BY a.position', (org, meeting_id)))
            minute_rows = ''.join(f'<li><strong>{escape(m["agenda_title"])}</strong> — {escape(m["status"])}: {escape(m["body"])}<ul>' + ''.join(f'<li>{escape(i["item_type"])}: {escape(i["body"])}</li>' for i in DB.execute('SELECT item_type,body FROM minute_items WHERE minute_id=? AND organisation_id=? AND deleted_at IS NULL ORDER BY position', (m['id'], org))) + (f'</ul><form method="post" action="/meetings/{meeting_id}/minutes/{m["id"]}/items">{csrf_input(session)}<label>Item type <select name="item_type"><option>discussion</option><option>decision</option><option>action</option><option>note</option></select></label><label>Minute item <input name="body" required></label><label>Position <input type="number" name="position" min="0" value="0" required></label><button type="submit">Add minute item</button></form>' if allowed(roles, 'minutes.write') else '</ul>') + '</li>' for m in minutes)
            agenda_rows = ''.join(f'<li>{escape(row["title"])} </li>' for row in agenda)
            paper_rows = ''.join(f'<tr><td>{escape(row["title"])}</td><td>{escape(row["classification"])}</td><td>{row["version_number"] or 0}</td><td>{row["size_bytes"] or 0} bytes</td><td><a href="/documents/{row["id"]}/download">View/download</a> · <a href="/documents/{row["id"]}/versions">Versions</a></td></tr>' for row in papers)
            paper_form = ''
            if allowed(roles, 'documents.write'):
                paper_form = f'<form class="card" method="post" action="/meetings/{meeting_id}/documents" enctype="multipart/form-data">{csrf_input(session)}<h3>Upload board paper</h3><label>Title <input name="title"></label><label>Classification <select name="classification"><option>internal</option><option>confidential</option><option>public</option></select></label><label>PDF or file <input type="file" name="file" accept=".pdf,application/pdf" required></label><button type="submit">Upload paper</button></form>'
            agenda_form = ''
            if allowed(roles, 'agenda.write'):
                agenda_form = f'<form class="card" method="post" action="/meetings/{meeting_id}/agenda">{csrf_input(session)}<h3>Add agenda item</h3><label>Title <input name="title" required></label><label>Position <input name="position" type="number" min="1" value="{len(agenda) + 1}" required></label><button type="submit">Add item</button></form>'
            participant_rows = ''.join(f'<tr><td>{escape(row["name"] or "")}</td><td>{"Observer" if row["observer"] else "Member"}</td><td>{escape(row["status"])}</td></tr>' for row in participants)
            join = ''
            if meeting['video_metadata'] and allowed(roles, 'meetings.read'):
                try: join_url = json.loads(meeting['video_metadata']).get('url')
                except (TypeError, ValueError): join_url = None
                if join_url: join = f'<p><a href="{escape(join_url)}">Join Meeting</a></p>'
            conflict_rows = ''.join('<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>'.format(escape(c['name'] or ''), escape(c['agenda_title'] or 'Whole meeting / general'), escape(c['interest']), escape(c['management_action']), escape(c['status']), (f'<form method="post" action="/meetings/{meeting_id}/conflicts/{c["id"]}/recusal">{csrf_input(session)}<select name="status"><option value="recusal_required">Recusal required</option><option value="recusal_approved">Recusal approved</option></select><button type="submit">Save recusal</button></form>' if allowed(roles, 'conflicts.manage') else '')) for c in conflicts)
            def motion_summary(m):
                tally = DB.vote_tally(org, meeting_id, m['id'])
                result = 'Carried' if tally['passed'] else 'Not Carried'
                quorum = 'Met' if tally['quorum_met'] else 'Not Met'
                return f'For {tally["for"]} · Against {tally["against"]} · Abstain {tally["abstain"]} · Eligible {tally["eligible"]} · Quorum: {quorum}' + (f' · Result: {result}' if m['status'] == 'closed' else '')
            motion_rows = ''.join('<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>'.format(escape(m['agenda_title']), escape(m['text']), escape(m['status']), motion_summary(m)) for m in motions)
            resolution_rows = ''.join('<tr><td>NCC/RES/{}/{:03d}</td><td>{}</td><td>{}</td><td>{}</td></tr>'.format(r['resolution_year'], r['resolution_number'], escape(r['text']), escape(r['outcome']), escape(r['status'])) for r in resolutions)
            action_rows = ''.join('<tr><td>NCC/ACT/{}/{:03d}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>'.format(a['action_year'], a['action_number'], escape(a['description']), escape(a['owner_name'] or ''), escape(a['priority']), escape(a['due_at'] or '—'), escape(a['status']) + (' · OVERDUE' if a['is_overdue'] else ''), a['update_count'], escape(a['completed_at'] or '—')) for a in actions)
            action_forms = ''.join((f'<div class="card"><strong>NCC/ACT/{a["action_year"]}/{a["action_number"]:03d}</strong><form method="post" action="/actions/{a["id"]}/updates">{csrf_input(session)}<label>Progress update <textarea name="body" required></textarea></label><label>Status <select name="status"><option value="">Keep status</option>{"<option>in_progress</option>" if a["status"] == "open" else ""}<option>cancelled</option></select></label><button type="submit">Add Progress Update</button></form><form method="post" action="/actions/{a["id"]}/evidence" enctype="multipart/form-data">{csrf_input(session)}<label>Evidence note <input name="note" required></label><label>Evidence PDF <input type="file" name="file" accept=".pdf,application/pdf"></label><button type="submit">Add Completion Evidence</button></form><form method="post" action="/actions/{a["id"]}/complete">{csrf_input(session)}<button type="submit">Complete Action</button></form></div>' if a['status'] in ('open', 'in_progress') and action_access(a['id'], 'actions.write') else '') for a in actions)
            assigned_ids = {p['member_id'] for p in participants}
            participant_options = ''.join(f'<option value="{m["member_id"]}">{escape(m["name"] or "")}</option>' for m in organisation_members if m['member_id'] not in assigned_ids)
            proposer_options = ''.join(f'<option value="{p["member_id"]}">{escape(p["name"] or "")}</option>' for p in participants if not p['observer'] and any(m['member_id'] == p['member_id'] for m in organisation_members))
            owner_options = ''.join(f'<option value="{m["member_id"]}">{escape(m["name"] or "")}</option>' for m in organisation_members)
            agenda_options = '<option value="">Whole Meeting / General Conflict</option>' + ''.join(f'<option value="{a["id"]}">{escape(a["title"])}</option>' for a in agenda)
            agenda_options_required = ''.join(f'<option value="{a["id"]}">{escape(a["title"])}</option>' for a in agenda)
            lifecycle = ''
            if allowed(roles, 'meetings.write'):
                next_statuses = {'draft': ('scheduled', 'cancelled'), 'scheduled': ('published', 'cancelled'), 'published': ('completed', 'cancelled')}.get(meeting['status'], ())
                lifecycle = ''.join(f'<form method="post" action="/meetings/{meeting_id}/transition" style="display:inline">{csrf_input(session)}<input type="hidden" name="status" value="{s}"><button type="submit">{s.title()} meeting</button></form>' for s in next_statuses)
            participant_form = f'<form class="card" method="post" action="/meetings/{meeting_id}/participants">{csrf_input(session)}<h3>Assign participant</h3><label>Active member <select name="member_id" required>{participant_options}</select></label><label><input type="checkbox" name="observer" value="1"> Observer</label><button type="submit">Assign</button></form>' if allowed(roles, 'meetings.write') else ''
            attendance_forms = ''.join(f'<form method="post" action="/meetings/{meeting_id}/attendance">{csrf_input(session)}<input type="hidden" name="member_id" value="{p["member_id"]}"><select name="status"><option>present</option><option>absent</option><option>apology</option></select><button type="submit">Save attendance for {escape(p["name"] or "")}</button></form>' for p in participants) if allowed(roles, 'attendance.write') else ''
            rsvp_form = f'<form class="card" method="post" action="/meetings/{meeting_id}/rsvp">{csrf_input(session)}<h3>My RSVP</h3><select name="response"><option>yes</option><option>maybe</option><option>no</option></select><button type="submit">Save RSVP</button></form>' if allowed(roles, 'rsvp.write') else ''
            conflict_form = f'<form class="card" method="post" action="/meetings/{meeting_id}/conflicts">{csrf_input(session)}<h3>Declare conflict</h3><label>Agenda item <select name="agenda_item_id">{agenda_options}</select></label><label>Interest <textarea name="interest" required></textarea></label><label>Management action <input name="management_action" required></label><button type="submit">Declare conflict</button></form>' if allowed(roles, 'conflicts.write') else ''
            motion_form = f'<form class="card" method="post" action="/meetings/{meeting_id}/motions">{csrf_input(session)}<h3>Create motion</h3><label>Agenda item <select name="agenda_item_id" required>{agenda_options_required}</select></label><label>Proposer <select name="proposer_member_id" required>{proposer_options}</select></label><label>Motion text <textarea name="text" required></textarea></label><button type="submit">Create motion</button></form>' if allowed(roles, 'motions.write') else ''
            def can_vote(motion):
                attendee = DB.execute('SELECT 1 FROM meeting_attendees WHERE meeting_id=? AND member_id=? AND organisation_id=? AND status=\'present\' AND observer=0 AND deleted_at IS NULL', (meeting_id, actor, org)).fetchone()
                recused = DB.execute('SELECT 1 FROM conflict_declarations WHERE meeting_id=? AND member_id=? AND organisation_id=? AND (agenda_item_id IS NULL OR agenda_item_id=?) AND status IN (\'recusal_required\',\'recusal_approved\') AND deleted_at IS NULL', (meeting_id, actor, org, motion['agenda_item_id'])).fetchone()
                voted = DB.execute('SELECT 1 FROM votes WHERE motion_id=? AND member_id=? AND organisation_id=?', (motion['id'], actor, org)).fetchone()
                return attendee and not recused and not voted and allowed(roles, 'votes.write')
            def motion_controls_for(m):
                vote_form = ''
                if can_vote(m):
                    vote_form = ''.join(f'<form method="post" action="/meetings/{meeting_id}/motions/{m["id"]}/votes" style="display:inline">{csrf_input(session)}<input type="hidden" name="choice" value="{choice}"><button type="submit">{label}</button></form> ' for choice, label in (("for", "Vote For"), ("against", "Vote Against"), ("abstain", "Abstain")))
                close_form = f'<form method="post" action="/meetings/{meeting_id}/motions/{m["id"]}/close">{csrf_input(session)}<button type="submit">Close Motion</button></form>' if allowed(roles, 'votes.manage') else ''
                return vote_form + close_form
            motion_controls = ''.join(motion_controls_for(m) for m in motions if m['status'] == 'open')
            closed_motions = [m for m in motions if m['status'] == 'closed']
            motion_options = ''.join(f'<option value="{m["id"]}">{escape(m["agenda_title"])}: {escape(m["text"])}</option>' for m in closed_motions)
            resolution_form = f'<form class="card" method="post" action="/meetings/{meeting_id}/resolutions">{csrf_input(session)}<h3>Create resolution</h3><select name="agenda_item_id" required>{agenda_options_required}</select><select name="motion_id"><option value="">No related motion</option>{motion_options}</select><input name="text" required><select name="outcome"><option>noted</option><option>carried</option><option>not_carried</option></select><select name="status"><option>draft</option><option>approved</option><option>published</option></select><button type="submit">Create resolution</button></form>' if allowed(roles, 'resolutions.write') else ''
            minutes_form = f'<form class="card" method="post" action="/meetings/{meeting_id}/minutes">{csrf_input(session)}<h3>Save minutes</h3><select name="agenda_item_id" required>{agenda_options_required}</select><textarea name="body" required></textarea><select name="status"><option>draft</option><option>in_review</option><option>approved</option></select><button type="submit">Save minutes</button></form>' if allowed(roles, 'minutes.write') else ''
            resolution_options = ''.join(f'<option value="{r["id"]}">NCC/RES/{r["resolution_year"]}/{r["resolution_number"]:03d} — {escape(r["text"])}</option>' for r in resolutions)
            action_form = f'<form class="card" method="post" action="/meetings/{meeting_id}/actions">{csrf_input(session)}<h3>Create action</h3><label>Agenda item <select name="agenda_item_id" required>{agenda_options_required}</select></label><label>Resolution <select name="resolution_id"><option value="">No linked resolution</option>{resolution_options}</select></label><label>Owner <select name="owner_member_id" required>{owner_options}</select></label><label>Priority <select name="priority"><option>normal</option><option>low</option><option>high</option><option>critical</option></select></label><label>Description <input name="description" required></label><label>Due date (Harare time, UTC+02:00) <input name="due_at" type="datetime-local"></label><button type="submit">Create action</button></form>' if allowed(roles, 'actions.write') else ''
            action_controls = ''
            replace_forms = ''.join(f'<form method="post" action="/documents/{p["id"]}/replace" enctype="multipart/form-data">{csrf_input(session)}<input type="file" name="file" accept=".pdf,application/pdf" required><button type="submit">Replace version</button></form>' for p in papers) if allowed(roles, 'documents.write') else ''
            content = f'<h1>{escape(meeting["title"])}</h1><p><strong>Status:</strong> {escape(meeting["status"])} · <strong>When:</strong> {escape(meeting["starts_at"])} · <strong>Location:</strong> {escape(meeting["location"] or "")}</p><p>{lifecycle}</p>{join}<div class="card"><h2>Quorum</h2><p>{q["present"]} / {q["eligible"]} present — <strong>{"MET" if q["met"] else "NOT MET"}</strong></p></div><nav class="workspace-nav"><a href="#agenda">Agenda</a><a href="#papers">Board papers</a><a href="#attendance">Attendance</a><a href="#conflicts">Conflicts</a><a href="#motions">Motions & voting</a><a href="#resolutions">Resolutions</a><a href="#minutes">Minutes</a><a href="#actions">Actions</a></nav><section id="agenda"><h2>Agenda ({len(agenda)})</h2><ol>{agenda_rows or "<li>No agenda items</li>"}</ol>{agenda_form}</section><section id="papers"><h2>Board papers</h2><table><tr><th>Title</th><th>Classification</th><th>Version</th><th>Size</th><th>Access</th></tr>{paper_rows}</table>{replace_forms}{paper_form}</section><section id="attendance"><h2>Attendance & participants</h2><table><tr><th>Participant</th><th>Type</th><th>Status</th></tr>{participant_rows}</table>{participant_form}{attendance_forms}{rsvp_form}</section><section id="conflicts"><h2>Conflicts</h2><table><tr><th>Member</th><th>Agenda</th><th>Interest</th><th>Management</th><th>Status</th></tr>{conflict_rows}</table>{conflict_form}</section><section id="motions"><h2>Motions & voting</h2><table><tr><th>Agenda</th><th>Motion</th><th>Status</th><th>Tally</th></tr>{motion_rows}</table>{motion_controls}{motion_form}</section><section id="resolutions"><h2>Resolutions</h2><table><tr><th>Number</th><th>Text</th><th>Outcome</th><th>Status</th></tr>{resolution_rows}</table>{resolution_form}</section><section id="minutes"><h2>Minutes</h2><ul>{minute_rows or '<li>No minutes saved</li>'}</ul>{minutes_form}</section><section id="actions"><h2>Actions</h2><table><tr><th>Number</th><th>Description</th><th>Owner</th><th>Priority</th><th>Due</th><th>Status</th><th>Updates</th><th>Completed</th></tr>{action_rows}</table>{action_forms}{action_form}</section>'
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
            events = DB.execute("SELECT l.event_type,l.resource_type,l.resource_id,l.payload,l.created_at,COALESCE(p.display_name,u.display_name,'System') actor FROM audit_logs l LEFT JOIN members m ON m.id=l.actor_member_id LEFT JOIN member_profiles p ON p.member_id=m.id LEFT JOIN users u ON u.id=m.user_id WHERE l.organisation_id=? ORDER BY l.created_at DESC", (org,)).fetchall()
            rows = "".join(f"<tr><td>{escape(event['created_at'])}</td><td>{escape(event['actor'])}</td><td>{escape(event['event_type'])}</td><td>{escape(event['resource_type'])} {escape(event['resource_id'] or '')}</td><td><code>{escape(event['payload'])}</code></td></tr>" for event in events)
            content = "<h1>Activity log</h1><p>Append-only governance activity for this organisation.</p>" + (f"<table><thead><tr><th>When</th><th>Actor</th><th>Event</th><th>Resource</th><th>Context</th></tr></thead><tbody>{rows}</tbody></table>" if rows else "<div class=\"empty\"><h2>No activity recorded</h2><p>Audit events will appear here as work is completed.</p></div>")
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
            return send("200 OK", {"resolutions": DB.resolution_traceability(org, meeting_id=meeting_id)})
        if resource == "minutes" and method == "POST" and len(parts) == 3:
            if not require("minutes.write"): return send("403 Forbidden", {"error": "forbidden"})
            return send("201 Created", {"id": DB.save_minutes(org, actor, meeting_id, data["agenda_item_id"], data["body"], data.get("status", "draft"))})
        if resource == "actions" and method == "POST" and len(parts) == 3:
            if not require("actions.write"): return send("403 Forbidden", {"error": "forbidden"})
            return send("201 Created", {"id": DB.create_action(org, actor, meeting_id, data["agenda_item_id"], data["owner_member_id"], data["description"], data.get("due_at"), data.get("resolution_id"), data.get("priority", "normal"))})
        if resource == "actions" and method == "GET":
            if not require("meetings.read"): return send("403 Forbidden", {"error": "forbidden"})
            return send("200 OK", {"actions": DB.action_traceability(org, meeting_id=meeting_id)})
        if resource == "minutes" and len(parts) == 5 and parts[4] == "items" and method == "POST":
            if not require("minutes.write"): return send("403 Forbidden", {"error": "forbidden"})
            return send("201 Created", {"id": DB.add_minute_item(org, actor, parts[3], data["item_type"], data["body"], data["position"], meeting_id)})
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
    except StaleRevisionError as exc:
        return error("409 Conflict", "stale_revision", str(exc))
    except PermissionError as exc:
        return error("403 Forbidden", "forbidden", str(exc))
    except DuplicateVoteError as exc:
        if browser:
            return html_send(start, "409 Conflict", page("Vote not accepted", f"<h1>Vote not accepted</h1><p>{escape(str(exc))}</p>", session, roles))
        return error("409 Conflict", "duplicate_vote", str(exc))
    except (KeyError, TypeError, ValueError) as exc:
        if browser:
            return html_send(start, "400 Bad Request", page("Request not accepted", f"<h1>Request not accepted</h1><p>{escape(str(exc) or 'Invalid request')}</p>", session, roles))
        return error("400 Bad Request", "invalid_request", str(exc) or "Invalid request.")


if __name__ == "__main__":
    print("Serving on http://127.0.0.1:8000")
    make_server("127.0.0.1", 8000, app).serve_forever()
