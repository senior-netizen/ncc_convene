"""Dependency-free WSGI API; every workflow mutation is tenant-bound and audited."""
import json
import os
from http import cookies
from wsgiref.simple_server import make_server

from .auth import read_token, token, verify_password
from .db import Database
from .policy import allowed

DB = Database(os.getenv("APP_DATABASE", ".data/ncc-convene.db"))
SECRET = os.getenv("APP_SESSION_SECRET", "development-only-secret")


def app(env, start):
    path, method = env["PATH_INFO"], env["REQUEST_METHOD"]
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

    if path == "/login" and method == "POST":
        try:
            data = body()
        except ValueError as exc:
            return send("400 Bad Request", {"error": str(exc)})
        user = DB.execute("SELECT * FROM users WHERE email=? AND deleted_at IS NULL", (data.get("email"),)).fetchone()
        if not user or not verify_password(data.get("password", ""), user["password_hash"]):
            DB.audit(None, None, "login.denied", "session", payload={"email": data.get("email")})
            return send("401 Unauthorized", {"error": "invalid credentials"})
        member = DB.execute("SELECT * FROM members WHERE user_id=? AND deleted_at IS NULL", (user["id"],)).fetchone()
        if not member:
            return send("403 Forbidden", {"error": "no active organisation membership"})
        DB.audit(member["organisation_id"], member["id"], "login", "session")
        value = token({"user": user["id"], "org": member["organisation_id"], "member": member["id"]}, SECRET)
        return send("200 OK", {"ok": True}, [("Set-Cookie", f"session={value}; HttpOnly; SameSite=Lax; Path=/")])

    if path == "/logout" and method == "POST":
        if session:
            DB.audit(session["org"], session["member"], "logout", "session")
        return send("200 OK", {"ok": True}, [("Set-Cookie", "session=; Max-Age=0; HttpOnly; SameSite=Lax; Path=/")])
    if not session or not DB.execute(
        "SELECT 1 FROM members WHERE id=? AND organisation_id=? AND deleted_at IS NULL",
        (session.get("member"), session.get("org")),
    ).fetchone():
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

    try:
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
    except (KeyError, TypeError, ValueError) as exc:
        return send("400 Bad Request", {"error": str(exc) or "invalid request"})


if __name__ == "__main__":
    print("Serving on http://127.0.0.1:8000")
    make_server("127.0.0.1", 8000, app).serve_forever()
