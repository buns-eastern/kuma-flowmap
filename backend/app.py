"""
Kuma FlowMap backend.

Serves the dark drag-and-drop canvas and streams live monitor status to the
browser over a WebSocket. Layout (the diagram you build) is persisted to a JSON
file on a mounted volume so it survives restarts.
"""

import asyncio
import json
import os
import pathlib

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import auth_store
import config_store
from kuma_client import build_client, test_connection

BASE_DIR = pathlib.Path(__file__).resolve().parent
FRONTEND_DIR = (BASE_DIR.parent / "frontend").resolve()
DATA_DIR = pathlib.Path(os.getenv("DATA_DIR", BASE_DIR.parent / "data")).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
LAYOUT_FILE = DATA_DIR / "layout.json"

BROADCAST_INTERVAL = float(os.getenv("BROADCAST_INTERVAL", "1"))

app = FastAPI(title="Kuma FlowMap")

client = build_client()


class Hub:
    def __init__(self):
        self.active: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        async with self._lock:
            self.active.add(ws)

    async def disconnect(self, ws: WebSocket):
        async with self._lock:
            self.active.discard(ws)

    async def broadcast(self, message: dict):
        payload = json.dumps(message)
        dead = []
        async with self._lock:
            targets = list(self.active)
        for ws in targets:
            try:
                await ws.send_text(payload)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self.active.discard(ws)


hub = Hub()
_last_sent = {"sig": None}


def _signature(snapshot: dict) -> str:
    # Only re-broadcast when something meaningful changed
    parts = [f"{snapshot.get('connected')}|{snapshot.get('mode')}"]
    for s in snapshot.get("servers", []):
        parts.append(
            f"S:{s.get('id')}:{int(bool(s.get('connected')))}:{s.get('error') or ''}:{s.get('alias', '')}"
        )
    for m in snapshot.get("monitors", []):
        parts.append(
            f"{m['id']}:{m['status']}:{int(m['active'])}:{m['name']}:{m.get('serverAlias', '')}"
        )
    return "\n".join(parts)


async def _status_loop():
    while True:
        try:
            snapshot = client.get_snapshot()
            sig = _signature(snapshot)
            if sig != _last_sent["sig"]:
                _last_sent["sig"] = sig
                await hub.broadcast({"type": "snapshot", "data": snapshot})
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(BROADCAST_INTERVAL)


@app.on_event("startup")
async def _startup():
    client.start()
    asyncio.create_task(_status_loop())


@app.on_event("shutdown")
async def _shutdown():
    client.stop()


# ------------------------------------------------------------------ API ----- #
# --------------------------------------------------------------- auth ------- #
# App login (see auth_store). The page itself (/), static assets, health, and the
# auth endpoints are open so the login screen can load; everything with real data is
# gated behind a valid session cookie.
COOKIE = "kfm_session"


def _set_session(resp, token):
    # No Secure flag: this must also work over plain http:// on a LAN. HttpOnly +
    # SameSite=Lax still keeps it out of JS and off cross-site requests.
    resp.set_cookie(COOKIE, token, max_age=auth_store.SESSION_TTL, httponly=True, samesite="lax", path="/")


def require_user(request: Request) -> str:
    u = auth_store.read_token(request.cookies.get(COOKIE))
    if not u:
        raise HTTPException(status_code=401, detail="authentication required")
    return u


@app.get("/api/auth/state")
async def auth_state(request: Request):
    if not auth_store.has_account():
        return {"authenticated": False, "needs_setup": True}
    u = auth_store.read_token(request.cookies.get(COOKIE))
    return {"authenticated": bool(u), "needs_setup": False, "username": u}


@app.post("/api/auth/setup")
async def auth_setup(payload: dict):
    if auth_store.has_account():
        return JSONResponse({"ok": False, "error": "An admin account already exists."}, status_code=400)
    payload = payload or {}
    u = (payload.get("username") or "").strip()
    pw = payload.get("password") or ""
    if len(u) < 3:
        return JSONResponse({"ok": False, "error": "Username must be at least 3 characters."}, status_code=400)
    if len(pw) < 8:
        return JSONResponse({"ok": False, "error": "Password must be at least 8 characters."}, status_code=400)
    acct = auth_store.create_account(u, pw)
    resp = JSONResponse({"ok": True, "username": u})
    _set_session(resp, auth_store.make_token(acct))
    return resp


@app.post("/api/auth/login")
async def auth_login(payload: dict):
    payload = payload or {}
    acct = auth_store.verify_login(payload.get("username") or "", payload.get("password") or "")
    if not acct:
        return JSONResponse({"ok": False, "error": "Wrong username or password."}, status_code=401)
    resp = JSONResponse({"ok": True, "username": acct["username"]})
    _set_session(resp, auth_store.make_token(acct))
    return resp


@app.post("/api/auth/logout")
async def auth_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE, path="/")
    return resp


@app.post("/api/auth/change-password")
async def auth_change_password(payload: dict, user: str = Depends(require_user)):
    payload = payload or {}
    new = payload.get("new") or ""
    if len(new) < 8:
        return JSONResponse({"ok": False, "error": "New password must be at least 8 characters."}, status_code=400)
    if not auth_store.change_password(user, payload.get("current") or "", new):
        return JSONResponse({"ok": False, "error": "Current password is incorrect."}, status_code=400)
    resp = JSONResponse({"ok": True})
    _set_session(resp, auth_store.make_token(auth_store.get_account()))  # keep this session signed in
    return resp


@app.get("/api/health")
async def health():
    snap = client.get_snapshot()
    return {
        "ok": True,
        "mode": snap["mode"],
        "connected": snap["connected"],
        "error": snap["error"],
        "monitor_count": len(snap["monitors"]),
    }


@app.get("/api/monitors")
async def monitors(user: str = Depends(require_user)):
    return client.get_snapshot()


# -------------------------------------------------------------- servers ----- #
# Manage the configured Uptime Kuma servers from the web UI. Passwords are stored
# encrypted (see config_store) and are NEVER returned by these endpoints — the client
# only ever learns whether a password is set (has_password).
def _servers_with_live_status():
    servers = config_store.list_public()
    snap = client.get_snapshot()
    status = {s["id"]: s for s in snap.get("servers", [])}
    for s in servers:
        st = status.get(s["id"])
        s["connected"] = bool(st and st.get("connected"))
        s["live_error"] = st.get("error") if st else None
        s["monitor_count"] = st.get("count", 0) if st else 0
    return servers, (snap.get("mode") == "demo")


@app.get("/api/servers")
async def list_servers(user: str = Depends(require_user)):
    servers, demo = _servers_with_live_status()
    return {"servers": servers, "demo": demo}


@app.post("/api/servers")
async def add_server(payload: dict, user: str = Depends(require_user)):
    rec = config_store.add_server(payload or {})
    await asyncio.to_thread(client.reload)
    return {"ok": True, "server": rec}


@app.put("/api/servers/{server_id}")
async def update_server(server_id: str, payload: dict, user: str = Depends(require_user)):
    rec = config_store.update_server(server_id, payload or {})
    if rec is None:
        return JSONResponse({"ok": False, "error": "server not found"}, status_code=404)
    await asyncio.to_thread(client.reload)
    return {"ok": True, "server": rec}


@app.delete("/api/servers/{server_id}")
async def remove_server(server_id: str, user: str = Depends(require_user)):
    ok = config_store.delete_server(server_id)
    if ok:
        await asyncio.to_thread(client.reload)
        return {"ok": True}
    return JSONResponse({"ok": False, "error": "server not found"}, status_code=404)


@app.post("/api/servers/test")
async def test_server(payload: dict, user: str = Depends(require_user)):
    payload = payload or {}
    url = (payload.get("url") or "").strip()
    username = (payload.get("username") or "").strip()
    password = payload.get("password")
    # Testing an already-saved server without re-typing its password: reuse the stored one.
    sid = payload.get("id")
    if sid and not password:
        for s in config_store.list_decrypted():
            if s.get("id") == sid:
                url = url or s.get("url", "")
                username = username or s.get("username", "")
                password = s.get("password", "")
                break
    if not (url and username and password):
        return {"ok": False, "error": "URL, username and password are all required to test."}
    return await asyncio.to_thread(test_connection, url, username, password)


@app.get("/api/layout")
async def get_layout(user: str = Depends(require_user)):
    if LAYOUT_FILE.exists():
        try:
            return JSONResponse(json.loads(LAYOUT_FILE.read_text()))
        except Exception:  # noqa: BLE001
            return JSONResponse({"drawflow": None})
    return JSONResponse({"drawflow": None})


@app.post("/api/layout")
async def save_layout(payload: dict, user: str = Depends(require_user)):
    tmp = LAYOUT_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload))
    tmp.replace(LAYOUT_FILE)
    return {"ok": True}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    # reject the live feed for anyone without a valid session cookie
    if not auth_store.read_token(ws.cookies.get(COOKIE)):
        await ws.close(code=1008)
        return
    await hub.connect(ws)
    try:
        # send current state immediately on connect
        await ws.send_text(json.dumps({"type": "snapshot", "data": client.get_snapshot()}))
        while True:
            # we don't need client messages; keep the socket alive
            await ws.receive_text()
    except WebSocketDisconnect:
        await hub.disconnect(ws)
    except Exception:  # noqa: BLE001
        await hub.disconnect(ws)


# --------------------------------------------------------------- frontend --- #
@app.get("/")
async def index():
    return FileResponse(str(FRONTEND_DIR / "index.html"))


# --- PWA: the service worker must be served from the ROOT so it can control the whole app.
#     Served here (not from /static) with a root scope + no-cache so updates roll out. ---
@app.get("/sw.js")
async def service_worker():
    return FileResponse(
        str(FRONTEND_DIR / "sw.js"),
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )


@app.get("/manifest.webmanifest")
async def manifest():
    return FileResponse(
        str(FRONTEND_DIR / "manifest.webmanifest"),
        media_type="application/manifest+json",
    )


# Common root-level icon requests (iOS / browsers ask for these at the root).
@app.get("/apple-touch-icon.png")
@app.get("/apple-touch-icon-precomposed.png")
async def apple_icon():
    return FileResponse(str(FRONTEND_DIR / "apple-touch-icon.png"), media_type="image/png")


@app.get("/favicon.ico")
async def favicon():
    return FileResponse(str(FRONTEND_DIR / "favicon-32.png"), media_type="image/png")


app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")
