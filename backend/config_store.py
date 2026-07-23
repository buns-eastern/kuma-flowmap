"""
Server configuration + credential storage for Kuma FlowMap.

Multiple Uptime Kuma servers can be configured from the web UI (no .env editing,
no container restart). Their settings live in a single JSON file on the writable
data volume (/app/data/servers.json). Credentials are the sensitive part, so the
password for each server is stored ENCRYPTED (never in plaintext, never returned
to the browser).

Encryption
----------
We use Fernet (AES-128-CBC + HMAC, from the `cryptography` library). The key comes
from, in order of preference:

  1. The KFM_SECRET_KEY environment variable. Any string works — we derive a proper
     32-byte Fernet key from it with SHA-256. Set this yourself (e.g. in .env) and a
     copied data volume is USELESS without it: this is the version that actually stops
     "the files grew legs and leaked my network." Keep the value secret and stable
     (change it and existing stored passwords can no longer be decrypted).

  2. If KFM_SECRET_KEY is not set, we generate a random key once and save it to
     /app/data/secret.key (0600). This keeps first-run setup painless and still beats
     plaintext — but because the key rides along in the same data volume, it does NOT
     protect a copied volume. For real at-rest protection, set KFM_SECRET_KEY.

Threat model note: this protects credentials AT REST (a stolen/copied volume, a
backup, a stray git commit). It does NOT protect against someone who compromises the
running container — at that point the app is already decrypting into memory. That is a
front-end-auth / network-trust problem, handled separately.
"""

import base64
import hashlib
import json
import os
import pathlib
import secrets
import threading

from cryptography.fernet import Fernet, InvalidToken

BASE_DIR = pathlib.Path(__file__).resolve().parent
DATA_DIR = pathlib.Path(os.getenv("DATA_DIR", BASE_DIR.parent / "data")).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)

SERVERS_FILE = DATA_DIR / "servers.json"
SECRET_FILE = DATA_DIR / "secret.key"

_lock = threading.RLock()

# Fields we accept from the client when creating/updating a server.
_STR_FIELDS = ("alias", "color", "url", "username")
_DEFAULTS = {
    "alias": "",
    "color": "",
    "url": "",
    "username": "",
    "enabled": True,
    "poll_interval": 2.0,
    "monitor_refresh": 30.0,
}


# --------------------------------------------------------------------------- #
# Encryption key handling
# --------------------------------------------------------------------------- #
def _derive_key_from_env(value: str) -> bytes:
    """Map any passphrase to a valid urlsafe-base64 32-byte Fernet key."""
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _load_or_create_file_key() -> bytes:
    if SECRET_FILE.exists():
        try:
            data = SECRET_FILE.read_bytes().strip()
            if data:
                return data
        except Exception:  # noqa: BLE001
            pass
    key = Fernet.generate_key()
    # write 0600 (owner only) — best effort on the mounted volume
    try:
        fd = os.open(str(SECRET_FILE), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, key)
        finally:
            os.close(fd)
    except Exception:  # noqa: BLE001 - fall back to a plain write if os.open is restricted
        SECRET_FILE.write_bytes(key)
        try:
            os.chmod(SECRET_FILE, 0o600)
        except Exception:  # noqa: BLE001
            pass
    return key


_fernet_cache = {"key": None, "f": None}


def _fernet() -> Fernet:
    env_val = os.getenv("KFM_SECRET_KEY", "").strip()
    key = _derive_key_from_env(env_val) if env_val else _load_or_create_file_key()
    if _fernet_cache["key"] != key:
        _fernet_cache["key"] = key
        _fernet_cache["f"] = Fernet(key)
    return _fernet_cache["f"]


def encrypt(plaintext: str) -> str:
    if plaintext is None:
        plaintext = ""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, Exception):  # noqa: BLE001 - wrong/rotated key or corrupt data
        return ""


# --------------------------------------------------------------------------- #
# Raw file IO
# --------------------------------------------------------------------------- #
def _empty():
    return {"version": 1, "servers": []}


def _read_raw() -> dict:
    if not SERVERS_FILE.exists():
        return _empty()
    try:
        data = json.loads(SERVERS_FILE.read_text())
        if not isinstance(data, dict) or not isinstance(data.get("servers"), list):
            return _empty()
        return data
    except Exception:  # noqa: BLE001
        return _empty()


def _write_raw(data: dict) -> None:
    tmp = SERVERS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    try:
        os.chmod(tmp, 0o600)
    except Exception:  # noqa: BLE001
        pass
    tmp.replace(SERVERS_FILE)


def _new_id() -> str:
    return "srv_" + secrets.token_hex(4)


def _coerce(record: dict, incoming: dict) -> dict:
    """Apply incoming user fields onto a record with type coercion + defaults."""
    for f in _STR_FIELDS:
        if f in incoming and incoming[f] is not None:
            record[f] = str(incoming[f]).strip()
    if "enabled" in incoming:
        record["enabled"] = bool(incoming["enabled"])
    for f in ("poll_interval", "monitor_refresh"):
        if f in incoming and incoming[f] is not None:
            try:
                record[f] = float(incoming[f])
            except (TypeError, ValueError):
                pass
    # sane floors
    record["poll_interval"] = max(1.0, float(record.get("poll_interval", 2.0)))
    record["monitor_refresh"] = max(10.0, float(record.get("monitor_refresh", 30.0)))
    return record


# --------------------------------------------------------------------------- #
# Public API (used by app.py)
# --------------------------------------------------------------------------- #
def _public(record: dict) -> dict:
    """A server record safe to send to the browser: NO password, ever."""
    out = {
        "id": record.get("id"),
        "alias": record.get("alias", ""),
        "color": record.get("color", ""),
        "url": record.get("url", ""),
        "username": record.get("username", ""),
        "enabled": bool(record.get("enabled", True)),
        "poll_interval": record.get("poll_interval", 2.0),
        "monitor_refresh": record.get("monitor_refresh", 30.0),
        "has_password": bool(record.get("password_enc")),
    }
    return out


def list_public() -> list:
    with _lock:
        return [_public(s) for s in _read_raw()["servers"]]


def list_decrypted() -> list:
    """Internal only: server records with plaintext password for building clients."""
    with _lock:
        out = []
        for s in _read_raw()["servers"]:
            rec = dict(s)
            rec["password"] = decrypt(s.get("password_enc", ""))
            out.append(rec)
        return out


def get_public(server_id: str):
    with _lock:
        for s in _read_raw()["servers"]:
            if s.get("id") == server_id:
                return _public(s)
    return None


def add_server(incoming: dict) -> dict:
    with _lock:
        data = _read_raw()
        record = dict(_DEFAULTS)
        record["id"] = _new_id()
        _coerce(record, incoming)
        pw = incoming.get("password")
        record["password_enc"] = encrypt(pw) if pw else ""
        if not record["alias"]:
            record["alias"] = record["url"] or record["id"]
        data["servers"].append(record)
        _write_raw(data)
        return _public(record)


def update_server(server_id: str, incoming: dict):
    with _lock:
        data = _read_raw()
        for s in data["servers"]:
            if s.get("id") == server_id:
                _coerce(s, incoming)
                # Only re-encrypt when a non-empty password is supplied; otherwise keep
                # the stored one. An explicit empty string does NOT wipe it (use the UI's
                # clear action for that, which sends password_clear=true).
                pw = incoming.get("password")
                if pw:
                    s["password_enc"] = encrypt(pw)
                elif incoming.get("password_clear"):
                    s["password_enc"] = ""
                if not s.get("alias"):
                    s["alias"] = s.get("url") or s["id"]
                _write_raw(data)
                return _public(s)
    return None


def delete_server(server_id: str) -> bool:
    with _lock:
        data = _read_raw()
        before = len(data["servers"])
        data["servers"] = [s for s in data["servers"] if s.get("id") != server_id]
        if len(data["servers"]) != before:
            _write_raw(data)
            return True
    return False


def seed_from_env_if_empty() -> bool:
    """First-run convenience: if there's no servers.json yet but the classic
    KUMA_URL/USERNAME/PASSWORD env vars are set, create the first server from them so
    existing single-server deployments keep working with zero clicks. Returns True if a
    server was seeded."""
    with _lock:
        if SERVERS_FILE.exists():
            return False
        url = os.getenv("KUMA_URL", "").strip()
        user = os.getenv("KUMA_USERNAME", "").strip()
        pw = os.getenv("KUMA_PASSWORD", "")
        if not (url and user and pw):
            return False
        record = dict(_DEFAULTS)
        record["id"] = _new_id()
        record["alias"] = "Main"
        record["url"] = url
        record["username"] = user
        record["password_enc"] = encrypt(pw)
        try:
            record["poll_interval"] = max(1.0, float(os.getenv("KUMA_POLL_INTERVAL", "2")))
            record["monitor_refresh"] = max(10.0, float(os.getenv("KUMA_MONITOR_REFRESH", "30")))
        except (TypeError, ValueError):
            pass
        _write_raw({"version": 1, "servers": [record]})
        return True
