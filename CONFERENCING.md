# Embedded conferencing

## Architecture and security boundary

Python owns authentication, tenant membership, assignment, admission, capabilities and audit records. It creates five-minute, room-scoped participant tokens; the browser never receives the API secret or room-administration grants. Next.js retains one `LiveKitRoom` while workspace panels change. Audio, video, data and screen tracks travel directly between browsers and LiveKit's SFU. `conference_sessions` is separate from the governance meeting lifecycle, so ending a call never completes the meeting and connecting never records formal attendance.

The integration uses `livekit-api==1.0.7`, `livekit-client==2.15.6` and LiveKit React components `2.9.14`. The provider boundary is `app/conference.py`; Cloud and local deployments use the same API.

### Capabilities

| Role | Join when assigned | Moderate | Present initially | Record |
|---|---:|---:|---:|---:|
| Chairperson / Secretariat | yes | yes | yes | no |
| Organisation / Super Admin | explicit tenant operator | yes, audited | yes | no |
| Board member / Observer | yes | no | no (moderator can grant) | no |

Recording has no UI because egress and durable protected storage are not configured. A moderator grant is persisted and audited. Source-specific provider enforcement for presentation grants remains deferred; do not enable general screen sharing for untrusted users until that enforcement is added.

## API contract

All routes use the authenticated session's organisation and member. Mutation requests require `X-CSRF-Token` from `/api/v1/session/me`; errors are `{error:{code,message}}`. Tokens return `Cache-Control: no-store` and must never be placed in a URL or log.

* `GET /api/v1/meetings/{meeting}/conference` — status, caller state and moderator queue.
* `POST .../conference/start` — start a distinct call session (`{admission_required}`).
* `POST .../conference/admission` — enter the waiting queue; moderators are admitted.
* `POST .../conference/token` — issue an admitted, unblocked participant's five-minute token.
* `POST .../conference/lock` — lock/unlock new entry; already admitted members may reconnect.
* `POST .../conference/moderate` — `admit`, `reject`, `remove`, `grant_present`, `revoke_present`, `grant_moderate`, or `revoke_moderate` for a tenant/session-bound `member_id`.
* `POST .../conference/end` — delete the provider room, then end only the call session.

Removal is application-blocked before future token issuance and the connected participant is disconnected through LiveKit. Deleting the room disconnects everyone on end. Previously minted access tokens are bearer credentials: self-hosted LiveKit does not provide a general token-revocation list. An already removed client can attempt reconnect until its token expires (at most five minutes); provider removal prevents the current participant connection but deployments must not claim cryptographic immediate revocation. Locking prevents this application from refreshing/issuing a token, but cannot retroactively rewrite an issued JWT. Short TTL, disconnect and application blocking are defense in depth.

## Local setup

```bash
cp .env.example .env
docker compose -f docker-compose.livekit.yml up -d
set -a; . ./.env; set +a
python -m pip install -e .
python -m app.demo --data-dir .data/rehearsal --host 127.0.0.1 --port 8000
# another shell
cd web && npm install && npm run dev
```

Open `http://localhost:3000/meetings/<meeting-id>`. `localhost` is a secure-context exception. A plain `http://192.168.x.x` LAN URL is **not** equivalent: use trusted HTTPS and WSS for camera, microphone and display capture. Production must publish LiveKit's signaling HTTPS/WSS endpoint, UDP media ports and TURN/TLS (commonly TCP 443) through the firewall. Never reuse the checked-in development key.

For LiveKit Cloud, create a project, set its `wss://...livekit.cloud` URL and server API key/secret only on Python, restart Python, and confirm `/api/v1/health`. No `NEXT_PUBLIC_` LiveKit secret or URL is required. Check browser console/`chrome://webrtc-internals`, LiveKit server logs, WSS reachability, UDP/TURN firewall policy and system device permissions when connection fails.

## Five-minute demo

1. Sign in as Secretariat, open the Next.js meeting route and start an admission-required call.
2. In a separate browser profile sign in as a commissioner, choose devices in pre-join and request entry; confirm no room media is available while waiting.
3. Admit them, join from both profiles, verify remote synthetic or physical camera/audio tracks, mute/camera controls, active speaker and grid/focus layouts.
4. Share a tab/window, then stop from browser chrome and verify the grid returns. Switch to Papers and Agenda and show that the call stays mounted.
5. Remove the commissioner, show disconnection and blocked refresh, then end for everyone with the dedicated action. Confirm governance status and attendance did not change.

## Verification status and limits

Automated database tests cover tenant boundaries, permission denial, durable admission/blocking, opaque rooms, and lifecycle separation. The official room UI supplies preview/device selectors, mute/camera switching, adaptive streams, dynacast, active-speaker/grid/focus presentation, screen sharing, participant list, reconnect UI, autoplay recovery and device cleanup.

No LiveKit credentials/service or browser dependencies are bundled into the Python unit suite, so real SFU track exchange, competing screen shares, TURN, capacity and Cloud behavior are **not claimed as tested** by that suite. Before release, run two browsers against the local compose service with synthetic media, then perform a manual two-device test on different networks for intelligible microphone audio, camera video, screen sharing and TURN/TLS. Capacity for 17 board members plus staff must be load-measured on the chosen LiveKit plan/host; adaptive video is enabled but is not a capacity guarantee.

Deferred: provider-enforced one-presenter/source grants, admission/moderation UI panels, persistent chat/retention, raised-hand persistence, webhook reconciliation, recording/egress, captions/transcription, agenda-specific media exclusion, virtual backgrounds and breakouts. Chat must not be represented as minutes; recusal affects governance eligibility but does not currently exclude confidential media.
