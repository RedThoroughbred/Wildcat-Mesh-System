# Sending to the mesh from an Apple Watch

*Spec, 2026-09-14. Two tiers: the fast win is an Apple Shortcut that POSTs to the
Den; the stretch is a watch-sized send page in the Wildcat app. Both ride the
existing operator TX path (`wildcat/tx` via meshd) and the Den's operator token.*

---

## 1. The endpoint contract

Everything below is what the dashboard's compose console already uses.

**`POST /v2/api/tx`** — queue one message on the mesh.

```json
{ "to": "^all",              // broadcast, or a node id like "!716c668c" for a DM
  "text": "Net at 8, ch0",   // 1–200 bytes UTF-8 = one LoRa packet (the server refuses longer)
  "channel": 0 }             // 0–7, optional (0 = primary)
```

Response `200`:

```json
{ "ok": true, "id": "obs-1789397680123", "to": null, "to_name": null, "broadcast": true,
  "channel": 0, "text": "Net at 8, ch0", "state": "queued", "ts": 1789397680.1, ... }
```

Errors: `400` bad text/to/channel · `401 {"error": "operator token required"}` · `503`
the bus is off (nothing owns the radio). Emoji count as 4 bytes each — keep watch
messages short.

**`GET /v2/api/tx/<id>`** — the send's state: `queued → sent → relayed → delivered | failed`
(`delivered` only for DMs the destination ACKed; broadcasts end at `sent`).

**`POST /v2/api/sos`** — the emergency broadcast (`{"text", "repeat", "interval", "channel"}`),
same auth. Don't bind this to a watch button you can hit by accident; if you do,
put a "Confirm SOS?" menu step in front of it.

**Reachability.** The Den listens on the LAN (`http://<den-ip>:5050`) and, with
Tailscale on the mini, on its Tailscale IP / MagicDNS name (`http://den.<tailnet>.ts.net:5050`
after `tailscale serve` is set up for HTTPS — see docs/OBSERVATORY_V2.md §6). Away
from home the phone must be on the tailnet.

## 2. Auth — the operator token

The read API is open on the LAN by design (the public view needs it). Writes are
gated by **one operator token** once `[observatory].operator_token` is set in
`config/wildcat.toml`:

```toml
[observatory]
operator_token = "a-long-random-string"     # `openssl rand -hex 24`
```

With it set, every `POST` / `PATCH` / `DELETE` under `/v2/api/` needs either

- header `Authorization: Bearer <token>` (or `X-Wildcat-Token: <token>`) — what the Shortcut sends, or
- the operator cookie the dashboard sets when you open **`/v2/?token=<token>`** once on a
  device (HttpOnly, a year). `/v2/public` never sets it.

Leave it blank and the API stays LAN-trust exactly as before (fine behind Tailscale
only; **required** the moment anything is exposed by Tailscale Funnel or a port
forward — a broadcast button on the open internet is a bad day).

## 3. Tier 1 — the Apple Shortcut ("Mesh Send")

Build it on the iPhone in the Shortcuts app; it syncs to the Watch. Two variants —
**canned** (a menu of messages, two taps) and **dictated** (speak it).

### 3a. Canned + dictated, one shortcut

1. **Choose from Menu** — prompt "Send to the mesh". Items: `Net at 8 tonight, ch0`,
   `On my way`, `Heading back to the Den`, `Say something…`, `Cancel`.
2. For each canned item: **Text** = the message → **Set Variable** `msg`.
3. For `Say something…`: **Dictate Text** (Stop Listening: *After pause*, language
   English) → **Set Variable** `msg`.
4. **Choose from Menu** — "To". Items: `Everyone (broadcast)` → **Text** `^all`;
   `GO` → **Text** `!716c668c`; `STAY` → **Text** `!8880xxxx` (fill in real ids
   from the Nodes page); → **Set Variable** `to`.
5. **Get Contents of URL**
   - URL: `http://<den-ip-or-tailscale-name>:5050/v2/api/tx`
   - Method: `POST`
   - Headers: `Authorization` = `Bearer <token>`, `Content-Type` = `application/json`
   - Request Body: **JSON** → `to` = variable `to`, `text` = variable `msg`, `channel` = `0`
     (add the three keys with the JSON type; do not paste raw JSON as text)
6. **Get Dictionary Value** `ok` from *Contents of URL* → **If** `ok` is `true`:
   **Show Notification** "Queued: {msg}" (or **Play Sound** / haptic via
   *Vibrate Device* on watchOS 10+); **Otherwise**: **Get Dictionary Value** `error`
   → **Show Result** "Mesh send failed: {error}".
7. Shortcut settings: name **Mesh Send**, icon 📡, **Show on Apple Watch** on,
   "Ask Before Running" off (the menu is the confirmation).

On the Watch: complication or the Shortcuts app → Mesh Send → two taps for a
canned line, one dictation for anything else. Under ~5 s end-to-end plus mesh
airtime.

### 3b. Cheap extras
- A second shortcut **Mesh SOS** that asks "Broadcast SOS?" then POSTs to
  `/v2/api/sos` with `{"text": <dictated>, "repeat": 6, "interval": 300}`. Keep it
  off the complication.
- **Mesh Last** — `GET /v2/api/messages?hours=1` and show the last three texts
  (Get Contents of URL → Get Dictionary Value `messages` → Repeat 3 → Show Result).
- Confirm delivery: after the POST, wait 8 s, `GET /v2/api/tx/{id}`, show `state`.

### 3c. Caveats to test on day one
- **Network from the watch.** Shortcuts on the Watch fetch through the paired iPhone
  when the watch has no Wi-Fi. Whether that path sees the iPhone's Tailscale VPN
  is the thing to verify: if `http://100.x.y.z:5050` fails from the wrist but works
  on the phone, use the LAN IP at home and, away, either run it from the phone
  (the watch tells the phone to run it: "Run on iPhone" isn't a toggle — trigger
  the shortcut from the phone instead) or expose the Den through **Tailscale
  Funnel** (public HTTPS, hence the token being mandatory).
- **Plain HTTP.** iOS allows `http://` in Shortcuts. If you switch to
  `tailscale serve` HTTPS, just change the URL.
- **Dictation quirks:** "channel zero" becomes "channel 0"; node names in
  dictation are unreliable — pick the recipient from the menu, dictate only the text.

## 4. Tier 2 (stretch) — a watch-sized send page in the app

A route the Den serves at **`/v2/send`**: no map, no feed, a single column of
big targets that works at 200 px wide and on the Watch's browser (watchOS
opens links in a reduced Safari; it's usable but not a first-class app — the
Shortcut stays the primary path; this page is for the phone in a pocket and
for the "I'm at the kitchen counter" case).

**Layout (top to bottom, all ≥ 56 px tall):**
1. Status line: Den reachable · radio connected · `N heard 1h` (from `/api/health`).
2. **Quick messages** — 4–6 buttons from a configurable list (`[observatory]
   quick_messages = ["Net at 8, ch0", "On my way", …]` or the BBS `messages.json`),
   one tap = send (with a 2-s "undo" toast rather than a confirm dialog).
3. **To** — a segmented control `All · GO · STAY · …` built from the roster's
   favourites (`admin_nodes` + the last 3 DM partners).
4. **Dictate / type** — one text field with the browser's mic (the iOS keyboard's
   dictation key); byte counter; **Send** button that greys out at 200 bytes.
5. Last 3 sends with their live state (`queued/sent/relayed/delivered`) via the
   existing `tx` socket event.

**Auth:** the operator cookie (open `/v2/?token=…` once on the phone). No token →
the page shows how to get one instead of a broken button.

**Build notes:** a second Jinja template `v2/send.html` + `send.css`, reusing
`app.js`'s `sharePublic`/`toast` bits is overkill — write a ~150-line standalone
script that talks to `/api/tx` and `/api/tx/<id>`. PWA manifest gets a second
`shortcuts` entry ("Send") so the home-screen icon opens it directly. Capacitor
later inherits it for free (docs/OBSERVATORY_V2.md §6).

**What it does not do:** run natively on the Watch. A true watchOS app means a
WatchKit target in the Capacitor shell — a later phase, and by then the Shortcut
has answered whether anyone actually sends from the wrist.

## 5. Checklist to ship Tier 1

- [ ] `openssl rand -hex 24` → `[observatory].operator_token` in wildcat.toml, restart the observatory
- [ ] open `http://<den>:5050/v2/?token=…` once on the phone → compose still works
- [ ] build **Mesh Send** as in §3a with the LAN IP; test a DM to GO from the phone
- [ ] run it from the Watch on home Wi-Fi; then with Wi-Fi off on the watch (phone relay)
- [ ] switch the URL to the Tailscale name; test from cellular
- [ ] add the complication; add **Mesh Last**; decide whether **Mesh SOS** belongs on the wrist
