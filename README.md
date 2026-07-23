# Kuma FlowMap

A dark, drag-and-drop **network topology map** for [Uptime Kuma](https://github.com/louislam/uptime-kuma).
Pull in all your monitors, arrange them into a live block diagram — boxes, frames, labels, wired
together — then flip to **View mode** for a clean, real-time "what's up / what's down" board.
Self-hosted, runs in Docker, works fully offline on your LAN.

---

## Highlights

- **Live status board** — every sensor's up / down / pending / maintenance state streams in over a
  WebSocket. Wires gently animate, and a link feeding a *down* sensor turns red and stops flowing.
- **Build it your way** — drag sensors onto a canvas, group them inside **boxes** and **frames**, drop
  in rich-text **notes**, and wire everything together. Edge/center **snapping**, equal-spacing guides,
  **grouping**, **locking**, and arrow-key **nudging** make precise layout easy.
- **Multiple Kuma servers** — add as many as you like from the web UI. Sensors group by server in the
  palette, with an optional small "source" label so you never mix them up.
- **In-app login** — on first run you create your own admin account (there is **no default password**).
  Cookie sessions, change-password, and logout are built in.
- **Encrypted credentials** — each server's password is stored **encrypted** on the data volume and is
  never sent back to the browser.
- **Autosave** — changes save on their own a couple of seconds after you make them (with a manual Save
  and `Ctrl/Cmd+S` too).
- **Installable PWA** — add it to a phone's home screen; mobile is a smooth, view-only board with
  pinch/zoom.
- **Hardened container** — runs non-root, all Linux capabilities dropped, read-only root filesystem.

---

## Quick start

You only need **Docker** with the **Docker Compose** plugin — Python and everything else run *inside*
the container.

```bash
git clone https://github.com/buns-eastern/kuma-flowmap.git
cd kuma-flowmap
docker compose up -d --build
```

The first build takes a minute or two. Then open **`http://<server-ip>:8848`** and:

1. **Create your admin login** — pick a username and a password (8+ characters). This is stored, hashed,
   on your server; there's no shipped default to leak.
2. Open **⚙ Settings ▸ Manage Kuma servers ▸ Add a server**, enter an **alias** (a short name like
   `Rack A`), your **Kuma URL** (e.g. `http://192.168.1.50:3001`), and your Kuma **username / password**.
   Hit **Test connection** for a green ✓, then **Save**.

Your sensors appear within a few seconds. Add more servers the same way, anytime. **No `.env` editing is
required** for any of this.

> **Just want to kick the tyres?** It ships with a demo mode. Set `DEMO_MODE=true` (in a `.env` file, or
> as an environment variable) and you'll get a dozen fake, randomly-flipping sensors — enough to try
> every feature with no Kuma at all.

To stop it: `docker compose down`. To watch logs: `docker compose logs -f`.

---

## Configuration (optional `.env`)

Everything is configured in the web UI, so a `.env` file is **optional**. Copy `.env.example` to `.env`
only if you want to override a default:

| Variable | Default | What it does |
|---|---|---|
| `KFM_SECRET_KEY` | auto-generated | Encrypts stored server passwords. Set a long random string to protect a copied data volume (see **Security**). |
| `DEMO_MODE` | `false` | `true` shows fake, randomly-flipping sensors instead of connecting to Kuma. |
| `HOST_PORT` | `8848` | Host port the app is published on. |
| `HOST_BIND` | `0.0.0.0` | Interface to bind. Use `127.0.0.1` to only expose it via a reverse proxy on the same box. |
| `KUMA_URL` / `KUMA_USERNAME` / `KUMA_PASSWORD` | — | **Optional first-run seed.** If set *and* you have no servers configured yet, your first server is created from them automatically. After that, manage servers in the UI. |
| `KUMA_POLL_INTERVAL` | `2` | Seconds between live-status reads from a server. |

New sensors you add in Uptime Kuma appear on the map on their own within ~30 seconds — no restart.

> **2FA note:** Uptime Kuma's socket login doesn't support two-factor auth. Use a Kuma account without
> 2FA for this tool (or disable it for that user).

---

## Using it

Two modes, top-right of the toolbar: **Edit** (build the map) and **View** (a clean live board). The
page opens in **View** by default — you load it to check status, not to edit.

**Node types**
- **Sensor** — drag one from the left palette onto the canvas. Click its icon to change it; double-click
  its name to rename.
- **Box** — an unmonitored *pass-through* node (same size as a sensor, changeable icon, no status). Use
  it to route wires through something that isn't monitored — an unmonitored switch, a patch panel, etc.
- **Frame** — a labeled, transparent group box to draw *around* things. Double-click to rename; drag the
  corner handle to resize (frames snap to each other on both move and resize).
- **Text** — a transparent rich-text note (per-character size, weight, and colour).

**Arrange precisely**
- Drag to move; edge/center **alignment guides** and **equal-spacing guides** appear as you go.
- **Nudge** a selection 1px with the arrow keys (10px with `Shift`).
- **Group** a selection so it moves as one (`Ctrl/Cmd+G`); **Lock** it in place from the right-click menu
  (locked items can't be moved, resized, or deleted until unlocked).
- **Layer order:** right-click → *Bring to Front / Send to Back*, or `Ctrl+]` / `Ctrl+[`.
- **Wire things:** drag from a node's **right-side dot** (output) to another node's **left-side dot** (input).
- **Delete a wire:** click it and hit the red **✕ Delete link** button, press `Delete`, or double-click it.

**Settings (⚙)** — four themes (two dark, two light); toggles for the sensor's 2nd line, the type badge,
the **source-server** label, a down-alert sound, desktop notifications, and the minimap; plus **Manage
Kuma servers** and your **account** (change password / log out).

**Saving** is automatic (a couple of seconds after a change), with a **Saved / Saving…** indicator by the
Save button. `Ctrl/Cmd+S` or the button save instantly.

**Shortcuts**

| Action | Shortcut |
|---|---|
| Save | `Ctrl/Cmd + S` |
| Undo / Redo | `Ctrl/Cmd + Z` / `Ctrl/Cmd + Shift + Z` |
| Nudge selection | Arrow keys (`Shift` = 10px) |
| Group / Ungroup | `Ctrl/Cmd + G` / `Ctrl/Cmd + Shift + G` |
| Delete selected node / wire | `Delete` (or `Cmd + Backspace`) |
| Search sensors & jump to one | `/` |
| Layer forward / backward | `Ctrl + ]` / `Ctrl + [` |
| Layer to front / to back | `Ctrl + Shift + ]` / `Ctrl + Shift + [` |
| Pan | drag the background, or hold `Space` and drag |

---

## Security

- **App login.** On first run you create an admin account (username + password). The password is stored
  **hashed** (PBKDF2-HMAC-SHA256), never in plaintext, and there is **no default password**. Sessions are
  HttpOnly cookies; changing the password immediately invalidates old sessions.
- **Encrypted credentials at rest.** Each Kuma password is encrypted (Fernet — AES-128 + HMAC) in
  `data/servers.json` and is never returned by the API — the browser only learns *whether* a password is
  set. The key comes from `KFM_SECRET_KEY`: set your own long random value and a copied/stolen data volume
  is useless without it. Leave it blank and a key is auto-generated into `data/secret.key` (zero-config,
  but that key rides along in the volume — set your own for real at-rest protection).
- **Hardened container.** Runs as a non-root user (uid `10001`), drops **all** Linux capabilities, sets
  `no-new-privileges`, and uses a **read-only root filesystem**. The only writable path is the named data
  volume where your login, servers, and saved map live.
- **Use HTTPS on the internet.** The login and cookies work fine over plain `http://` on a trusted LAN,
  but if you expose this beyond your LAN, put it behind a reverse proxy (e.g. Nginx Proxy Manager) with
  TLS. Desktop notifications also require a secure context — over `http://` the browser blocks them (the
  down *sound* still works).
- **Minimal footprint.** The only outbound connections are to the Kuma server(s) you configure.

---

## Updating

Pull the latest code and rebuild:

```bash
git pull
docker compose up -d --build     # or: ./update.sh
```

`index.html` and the backend are baked into the image at build time, so `--build` is what picks up
changes (a plain restart won't). Your **data volume — login, servers, and saved map — is untouched** by
rebuilds.

---

## Project layout

```
kuma-flowmap/
├─ docker-compose.yml     # hardened service definition
├─ Dockerfile             # python:3.11-slim, runs non-root
├─ update.sh              # git pull + rebuild, one command
├─ .env.example           # optional overrides (copy to .env)
├─ backend/
│  ├─ app.py              # FastAPI: auth, REST, WebSocket, static serving
│  ├─ auth_store.py       # admin login (PBKDF2 hashing, signed-cookie sessions)
│  ├─ config_store.py     # servers.json + encrypted-credential storage
│  ├─ kuma_client.py      # multi-server live (Socket.IO) + demo data providers
│  └─ requirements.txt
├─ frontend/
│  ├─ index.html          # the entire UI (self-contained)
│  └─ vendor/             # Drawflow + icons, vendored for offline use
└─ data/                  # runtime volume: login, servers.json, secret.key, layout.json
```

Nothing under `data/` and no `.env` is committed — see `.gitignore`.

---

## Built with

[FastAPI](https://fastapi.tiangolo.com/) + [uvicorn](https://www.uvicorn.org/) on the backend, vanilla JS
with [Drawflow](https://github.com/jerosoler/Drawflow) on the frontend (vendored, no CDN, no build step),
[uptime-kuma-api](https://github.com/lucasheld/uptime-kuma-api) to talk to Kuma, and
[cryptography](https://cryptography.io/) for credential encryption.

---

## License

Released under the **[MIT License](LICENSE)** — use it, change it, self-host it, even sell it; just
keep the copyright notice. No warranty. See the [`LICENSE`](LICENSE) file for the full text.
