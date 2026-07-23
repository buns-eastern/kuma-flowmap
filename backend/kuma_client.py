"""
Kuma data providers.

Two implementations expose the same interface:

    start()            -> begin background connection / data generation
    stop()             -> clean shutdown
    get_snapshot()     -> dict: {"mode", "connected", "error", "monitors": [ ... ]}

Each monitor dict:
    { "id": int|str, "name": str, "type": str, "parent": int|None,
      "target": str, "active": bool, "status": "up"|"down"|"pending"|"maintenance"|"unknown" }

LiveClient talks to a real Uptime Kuma instance over its Socket.IO API using the
`uptime-kuma-api` library (login with username/password). It keeps a live socket
open so heartbeats stream in; we read the in-memory cache on a short interval and
push changes to the browser -> effectively real-time on a LAN.

DemoClient fabricates monitors that randomly flip up/down, so the whole UI can be
demonstrated with no Kuma reachable (e.g. from a cloud sandbox).
"""

import os
import random
import threading
import time

# Uptime Kuma heartbeat status ints -> our labels
_STATUS_MAP = {0: "down", 1: "up", 2: "pending", 3: "maintenance"}


def status_label(value):
    return _STATUS_MAP.get(value, "unknown")


# --------------------------------------------------------------------------- #
# Demo client
# --------------------------------------------------------------------------- #
class DemoClient:
    mode = "demo"

    _NAMES = [
        ("Router / Gateway", "ping", "192.168.1.1"),
        ("Proxmox Host", "ping", "192.168.1.10"),
        ("NAS (TrueNAS)", "ping", "192.168.1.20"),
        ("Home Assistant", "http", "http://192.168.1.30:8123"),
        ("Plex", "http", "http://192.168.1.40:32400"),
        ("Pi-hole DNS", "dns", "192.168.1.53"),
        ("Docker Host", "ping", "192.168.1.225"),
        ("Nginx Proxy Mgr", "http", "http://192.168.1.225:81"),
        ("Grafana", "http", "http://192.168.1.225:3000"),
        ("Uptime Kuma", "http", "http://192.168.1.225:3001"),
        ("Postgres", "port", "192.168.1.60:5432"),
        ("Backup Job", "push", "cron:daily"),
    ]

    def __init__(self):
        self._lock = threading.Lock()
        self._monitors = []
        self._stop = threading.Event()
        self._thread = None
        for i, (name, typ, target) in enumerate(self._NAMES, start=1):
            self._monitors.append(
                {
                    "id": i,
                    "name": name,
                    "type": typ,
                    "parent": None,
                    "target": target,
                    "active": True,
                    # start mostly up
                    "status": "up" if random.random() > 0.15 else "down",
                }
            )

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            with self._lock:
                for m in self._monitors:
                    r = random.random()
                    if m["status"] == "up":
                        if r < 0.05:
                            m["status"] = "down"
                        elif r < 0.08:
                            m["status"] = "pending"
                    elif m["status"] == "down":
                        if r < 0.35:
                            m["status"] = "up"
                    else:  # pending / other
                        m["status"] = "up" if r < 0.7 else "down"
            self._stop.wait(4)

    def stop(self):
        self._stop.set()

    def get_snapshot(self):
        with self._lock:
            return {
                "mode": self.mode,
                "connected": True,
                "error": None,
                "monitors": [dict(m) for m in self._monitors],
            }


# --------------------------------------------------------------------------- #
# Live client (real Uptime Kuma)
# --------------------------------------------------------------------------- #
class LiveClient:
    mode = "live"

    def __init__(self, url, username, password, poll_interval=2.0, monitor_refresh=30.0):
        self.url = url.rstrip("/")
        self.username = username
        self.password = password
        self.poll_interval = poll_interval
        self.monitor_refresh = monitor_refresh   # how often to re-read the monitor LIST (new/removed sensors)

        self._api = None
        self._lock = threading.Lock()
        self._monitors = {}       # id -> config dict
        self._status = {}         # id -> label
        self._connected = False
        self._error = None
        self._stop = threading.Event()
        self._thread = None
        self._last_monitor_refresh = 0.0

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _connect(self):
        from uptime_kuma_api import UptimeKumaApi  # lazy import

        api = UptimeKumaApi(self.url, timeout=15)
        api.login(self.username, self.password)
        self._api = api
        self._connected = True
        self._error = None
        # the just-logged-in socket has the current list, so ingest it directly
        self._ingest_monitors(api.get_monitors())
        self._last_monitor_refresh = time.time()

    def _ingest_monitors(self, monitors):
        with self._lock:
            self._monitors = {}
            for m in monitors:
                mid = m.get("id")
                self._monitors[mid] = {
                    "id": mid,
                    "name": m.get("name", f"Monitor {mid}"),
                    "type": m.get("type", "unknown"),
                    "parent": m.get("parent"),
                    "target": m.get("url") or m.get("hostname") or m.get("host") or "",
                    "active": bool(m.get("active", True)),
                }

    def _refresh_monitors(self):
        # IMPORTANT: uptime-kuma-api's get_monitors() just returns the monitor list the
        # library cached from its *login* event — a long-lived socket keeps serving that
        # same list, so monitors you add/remove in Kuma never show up (only a full
        # reconnect, e.g. a container restart, picks them up). To see changes without a
        # restart we open a short-lived side connection, read the CURRENT list, and close
        # it — leaving the main status feed untouched. If it fails we keep the list we have.
        from uptime_kuma_api import UptimeKumaApi  # lazy import

        side = None
        try:
            side = UptimeKumaApi(self.url, timeout=15)
            side.login(self.username, self.password)
            monitors = side.get_monitors()
        except Exception:  # noqa: BLE001 - transient; keep current list and retry next cycle
            return
        finally:
            if side is not None:
                try:
                    side.disconnect()
                except Exception:  # noqa: BLE001
                    pass
        self._ingest_monitors(monitors)
        self._last_monitor_refresh = time.time()

    def _refresh_status(self):
        # get_heartbeats() reads the socket.io-populated in-memory cache
        heartbeats = self._api.get_heartbeats()
        new_status = {}
        for mid, hbs in (heartbeats or {}).items():
            try:
                key = int(mid)
            except (TypeError, ValueError):
                key = mid
            if hbs:
                last = hbs[-1]
                new_status[key] = status_label(last.get("status"))
        with self._lock:
            self._status = new_status

    def _run(self):
        backoff = 3
        while not self._stop.is_set():
            try:
                if self._api is None:
                    self._connect()
                    backoff = 3
                # re-read the monitor list so newly-added/removed sensors appear without a restart
                if time.time() - self._last_monitor_refresh > self.monitor_refresh:
                    self._refresh_monitors()
                self._refresh_status()
            except Exception as exc:  # noqa: BLE001
                self._connected = False
                self._error = str(exc)
                try:
                    if self._api:
                        self._api.disconnect()
                except Exception:  # noqa: BLE001
                    pass
                self._api = None
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 30)
                continue
            self._stop.wait(self.poll_interval)

    def stop(self):
        self._stop.set()
        try:
            if self._api:
                self._api.disconnect()
        except Exception:  # noqa: BLE001
            pass

    def get_snapshot(self):
        with self._lock:
            monitors = []
            for mid, cfg in self._monitors.items():
                m = dict(cfg)
                m["status"] = self._status.get(mid, "unknown")
                monitors.append(m)
            return {
                "mode": self.mode,
                "connected": self._connected,
                "error": self._error,
                "monitors": monitors,
            }


# --------------------------------------------------------------------------- #
# Connection tester (used by the settings "Test" button)
# --------------------------------------------------------------------------- #
def test_connection(url, username, password, timeout=15):
    """Open a short-lived login just to confirm creds/reachability, then close it.
    Returns {ok, error, monitor_count}. Never keeps a socket open."""
    api = None
    try:
        from uptime_kuma_api import UptimeKumaApi  # lazy import (inside try -> clean error)

        api = UptimeKumaApi((url or "").rstrip("/"), timeout=timeout)
        api.login(username, password)
        mons = api.get_monitors()
        return {"ok": True, "error": None, "monitor_count": len(mons or [])}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "monitor_count": 0}
    finally:
        if api is not None:
            try:
                api.disconnect()
            except Exception:  # noqa: BLE001
                pass


# --------------------------------------------------------------------------- #
# Multi-server client
# --------------------------------------------------------------------------- #
class MultiClient:
    """Runs one worker (LiveClient) per configured Uptime Kuma server and merges
    them into a single snapshot. Every monitor id is namespaced as
    ``<serverId>:<monitorId>`` so ids never collide across servers, and each monitor
    carries its origin (serverId / serverAlias / serverColor) so the UI can label it."""

    def __init__(self, demo=False):
        self.demo = demo
        self.mode = "demo" if demo else "live"
        self._lock = threading.Lock()
        self._workers = []  # list of {"meta": {...}, "client": <client|None>}

    def _build_workers(self):
        workers = []
        if self.demo:
            workers.append(
                {"meta": {"id": "demo", "alias": "Demo", "color": "#38bdf8"}, "client": DemoClient()}
            )
            return workers

        import config_store  # lazy; avoids importing crypto in demo-only runs

        for s in config_store.list_decrypted():
            meta = {
                "id": s.get("id"),
                "alias": s.get("alias") or s.get("url") or s.get("id"),
                "color": s.get("color", ""),
            }
            if not s.get("enabled", True):
                continue
            if not (s.get("url") and s.get("username") and s.get("password")):
                meta["config_error"] = "missing URL / username / password"
                workers.append({"meta": meta, "client": None})
                continue
            client = LiveClient(
                s["url"], s["username"], s["password"],
                poll_interval=s.get("poll_interval", 2.0),
                monitor_refresh=s.get("monitor_refresh", 30.0),
            )
            workers.append({"meta": meta, "client": client})
        return workers

    def start(self):
        with self._lock:
            self._workers = self._build_workers()
            for w in self._workers:
                if w["client"]:
                    w["client"].start()

    def stop(self):
        with self._lock:
            for w in self._workers:
                if w["client"]:
                    try:
                        w["client"].stop()
                    except Exception:  # noqa: BLE001
                        pass
            self._workers = []

    def reload(self):
        """Rebuild all workers from the current on-disk config (after a settings change),
        without restarting the container."""
        with self._lock:
            for w in self._workers:
                if w["client"]:
                    try:
                        w["client"].stop()
                    except Exception:  # noqa: BLE001
                        pass
            self._workers = self._build_workers()
            for w in self._workers:
                if w["client"]:
                    w["client"].start()

    def get_snapshot(self):
        with self._lock:
            workers = list(self._workers)
        monitors = []
        servers = []
        any_conn = False
        first_err = None
        for w in workers:
            meta = w["meta"]
            client = w["client"]
            sid = meta["id"]
            if client is None:
                err = meta.get("config_error", "not configured")
                servers.append({
                    "id": sid, "alias": meta["alias"], "color": meta.get("color", ""),
                    "connected": False, "error": err, "count": 0,
                })
                if first_err is None:
                    first_err = err
                continue
            snap = client.get_snapshot()
            conn = bool(snap.get("connected"))
            any_conn = any_conn or conn
            err = snap.get("error")
            if err and first_err is None:
                first_err = err
            count = 0
            for m in snap.get("monitors", []):
                m2 = dict(m)
                m2["id"] = f"{sid}:{m.get('id')}"
                p = m.get("parent")
                m2["parent"] = f"{sid}:{p}" if p is not None else None
                m2["serverId"] = sid
                m2["serverAlias"] = meta["alias"]
                m2["serverColor"] = meta.get("color", "")
                monitors.append(m2)
                count += 1
            servers.append({
                "id": sid, "alias": meta["alias"], "color": meta.get("color", ""),
                "connected": conn, "error": err, "count": count,
            })
        return {
            "mode": self.mode,
            "connected": any_conn,
            "error": first_err,
            "monitors": monitors,
            "servers": servers,
        }


# --------------------------------------------------------------------------- #
# Factory
# --------------------------------------------------------------------------- #
def build_client():
    demo = os.getenv("DEMO_MODE", "").lower() in ("1", "true", "yes")
    if not demo:
        try:
            import config_store
            config_store.seed_from_env_if_empty()
        except Exception:  # noqa: BLE001 - never let seeding block startup
            pass
    return MultiClient(demo=demo)
