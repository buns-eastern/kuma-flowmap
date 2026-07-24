# Kuma FlowMap — Quick Start

A drag-and-drop map of your network that shows what's **up** and what's **down**, live.
This guide gets you from clone to running in about five minutes. No coding required.

---

## 1. What you need

- **Docker** (with the Docker Compose plugin). That's the only requirement — Python, etc. all run
  *inside* the container, so you don't install them.
  - **Mac / Windows:** install **Docker Desktop** → https://www.docker.com/products/docker-desktop/
  - **Linux:** install Docker Engine + the Compose plugin (`docker` and `docker compose`).
- A free port on your machine (the app uses **8848** by default — easy to change).
- **Optional:** your own **Uptime Kuma** instance, if you want to see *your* real sensors. You don't need
  one to try it — it ships with a **demo mode** that makes up fake sensors.

Check Docker is ready:

```bash
docker --version
docker compose version
```

If both print a version, you're good.

---

## 2. Run it

From inside the project folder:

```bash
docker compose up -d --build
```

The first build takes a minute or two. When it finishes, open:

**http://localhost:8848**  (or `http://<server-ip>:8848` from another machine)

The first thing you'll see is a **"Create your admin login"** screen — pick a username and a password
(8+ characters). There's no default password; you're setting it now. That login stays on your server.

To stop it: `docker compose down`. To watch logs: `docker compose logs -f`.

> You don't need to create a `.env` file — the app runs with sensible defaults and everything is
> configured in the web UI.

---

## 3. Add your Uptime Kuma server(s)

Once you're logged in, click the **gear (⚙)** top-right → **Manage Kuma servers** → **Add a server**:

1. **Alias** — a short name shown on sensors (e.g. `Rack A`, `Home`, `Site-2`).
2. **Kuma URL** — e.g. `http://192.168.1.50:3001`.
3. **Username / Password** — your Kuma login.
4. Click **Test connection** (you should get a green ✓ with a sensor count), then **Save**.

Sensors appear within a few seconds. Repeat **Add a server** for as many Kuma instances as you like —
each server's sensors group under its alias in the left panel.

Notes:
- Passwords are stored **encrypted**. Set `KFM_SECRET_KEY` in a `.env` file to a long random string so a
  copied data volume can't be read (see the README's *Security* section).
- If your Kuma account has **2FA**, socket login won't work — use a Kuma account without 2FA for this tool.
- New sensors you add in Kuma show up on the map **on their own within ~30 seconds** — no rebuild.
- **Just exploring?** Put `DEMO_MODE=true` in a `.env` file and restart to see a dozen fake sensors
  instead of connecting to Kuma.

---

## 4. Using it

Top-right of the toolbar there are two modes: **Edit** (build the map) and **View** (a clean, live status
board). **The page opens in View** — click **Edit** when you want to change the map.

**Build your map (Edit mode)**
- **Drag** a sensor from the left panel onto the canvas. Click its icon to change it; double-click its
  name to rename.
- **Add Box** makes an unmonitored *pass-through* node (same size as a sensor, with its own icon) — handy
  for routing wires through something that isn't monitored, like an unmonitored switch.
- **Frame** makes a labeled, transparent group box to draw *around* things — double-click to rename, drag
  the corner handle to resize. Frames snap to each other when you move *and* resize them.
- **Text** drops a transparent rich-text note (change size/weight/colour per character).
- **Icon** drops a standalone graphic from the built-in icon set — drag a corner to resize (it keeps its
  aspect ratio and stays crisp), recolour it (or leave it on **theme** so it auto-flips light/dark), and
  double-click to swap the glyph.
- **Wire things together:** drag from a node's **right-side dot** (output) to another node's **left-side
  dot** (input).

**Arrange precisely**
- Drag to move — alignment and equal-spacing guides appear to help you line things up.
- **Nudge** a selection with the **arrow keys** (1px; hold `Shift` for 10px).
- **Group** a selection so it moves as one (`Ctrl/Cmd+G`), or right-click → **Lock in place** so it can't
  be moved, resized, or deleted until you unlock it.
- **Copy/paste** any box, frame, text note or icon to reuse it: `Ctrl/Cmd+C` then `Ctrl/Cmd+V`, or
  `Ctrl/Cmd+D` to duplicate it in place (also on the right-click menu) — its style and size come along.
- **Layer order:** right-click a node → *Bring to Front / Send to Back*, or `Ctrl+]` / `Ctrl+[`.

**Delete a wire:** click the line — a red **✕ Delete link** button pops up. Click it, press `Delete`, or
double-click the line.

**Zoom & pan:** mouse wheel to zoom, drag the empty background to pan, and the **⤢ fit** button
(bottom-right) frames the whole map.

**Settings (⚙):** four themes (two dark, two light); toggles for the sensor's 2nd line, the type badge,
the **source-server** label, a down-alert sound, desktop notifications, and the minimap; plus **Manage
Kuma servers** and your **account** (change password / log out).

**Saving is automatic** — a couple of seconds after any change, with a **Saved / Saving…** indicator by
the Save button. `Ctrl/Cmd+S` or the Save button save instantly.

**Handy shortcuts**

| Action | Shortcut |
|---|---|
| Save | `Ctrl/Cmd + S` |
| Undo / Redo | `Ctrl/Cmd + Z` / `Ctrl/Cmd + Shift + Z` |
| Nudge selection | Arrow keys (`Shift` = 10px) |
| Group / Ungroup | `Ctrl/Cmd + G` / `Ctrl/Cmd + Shift + G` |
| Delete selected node / wire | `Delete` (or `Cmd + Backspace` on Mac) |
| Search sensors & jump to one | `/` |
| Layer forward / backward | `Ctrl + ]` / `Ctrl + [` |
| Layer to front / to back | `Ctrl + Shift + ]` / `Ctrl + Shift + [` |
| Pan | drag background, or hold `Space` and drag |

**Present it:** stay in **View** for a live board — editing hides, wires flow, and sensors show 🟢 up ·
🔴 down · 🟠 pending · 🔵 maintenance. On a **phone or tablet** it's view-only with smooth pinch/zoom, and
you can **install it** as an app (Add to Home Screen).

---

## 5. Good to know

- **Log in required.** The app has its own admin login (you created it on first run). Change the password
  or log out anytime from **Settings ▸ Account**.
- **Still use a trusted network / reverse proxy.** The login works over plain `http://` on a LAN, but if
  you expose this beyond your LAN, front it with a reverse proxy (e.g. Nginx Proxy Manager) with HTTPS.
- **Desktop notifications need HTTPS.** Over plain `http://` the browser blocks them — the down *sound*
  still works. Notifications light up once you serve it over HTTPS.
- **It only talks to your Kuma.** No outbound connections anywhere else.

Have fun — break it, and tell me what's rough.
