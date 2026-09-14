# What the Den could become — an honest, ambitious shortlist

*2026-09-14. Creative fuel for Seth, not a commitment. Each item: what it is, why a
local mesh community would care, and a rough effort (S = a day, M = a week of
evenings, L = a month, XL = a season). Ordered by how much I believe in them, not
by size. Things already built are noted so we don't re-imagine them.*

---

## The ten I'd bet on

1. **The community portal** — `/v2/public` becomes the front door of the Northern
   Kentucky mesh: a landing page with the live map, "how to join" (which radio,
   which channel, what to name yourself), the daily digest, the bulletin boards
   read-only, coverage over time, and a "who runs this" page. One URL to give
   anyone who asks "what is that antenna on your roof?". **M.** *(the welcome card
   and read-only view exist; this is the page around them)*

2. **Coverage that grows and ages** — keep every measured RX point forever, render
   a coverage *history*: this month vs last, "new ground covered this week",
   dead zones that stayed dead, the effect of the new mega node before/after. A
   scheduled monthly coverage PNG posted to the boards. Turns antenna work into a
   visible score. **M.** *(rx_points + hex bins exist; the time axis doesn't)*

3. **Bridges: SMS / email / Discord ↔ the mesh** — a Den service that relays a
   Discord channel (or a Twilio number, or an inbox) to a mesh channel and back,
   with the same airtime brake and rate limits Bobcat has, and every relayed
   message tagged so nobody mistakes it for RF. The mesh keeps working when the
   internet dies; until then the community lives where it already lives. **M per
   bridge; Discord first (cheapest, most used).**

4. **Store-and-forward mailbox at the Den** — messages for a node that's off the
   air are queued and delivered when it's heard again (with a "you have 3 waiting"
   nudge). The BBS mail already stores; the missing half is *delivery on
   reappearance* + a dashboard queue view. The single most useful thing for
   part-time nodes. **M.**

5. **Neighborhood broadcast tiers** — SOS is built; the next rungs are
   "Announcement" (scheduled, once, to everyone) and "Watch" (recurring safety
   posts: boil-water notice, road closed, lost dog), each with its own style in
   the feed and a public archive. Plus an opt-in **email/SMS mirror** of anything
   tagged urgent for members who aren't on a radio yet. **S–M.** *(SOS + scheduled
   posts exist; this is the vocabulary and the archive)*

6. **Historical playback that tells a story** — the 6-hour replay exists; extend
   it to "any day, any speed" with a narration line from the digest ("18:40 — GO
   went out of range on Route 8") and shareable links to a moment
   (`#/replay/2026-09-13T18:40`). Field days and outages become something you
   can show people. **M.**

7. **A public, documented API + webhooks** — the `/v2/api` reads are already open;
   publish them properly (OpenAPI, a token for writes, rate limits), add
   **webhooks** ("POST to my URL when node X is heard / when an SOS arrives / when
   coverage gains a new hex"). This is what lets other people build on the Den
   without touching it: a Home Assistant card, a Grafana board, a ham club's page.
   **S for the docs, M for webhooks.**

8. **Node passports** — a per-node public page (`/v2/n/<id>`) with its coverage
   footprint, uptime, best-ever distance, battery history, and a QR code the owner
   can stick on the box. Owners *want* to see their node's report card; it
   quietly recruits maintainers. **S–M.** *(the node page has most of it; the
   public URL and the passport framing don't exist)*

9. **Mesh weather & environment layer** — many nodes carry BME280/680 sensors:
   temperature, humidity, pressure arrive as telemetry today and are stored but
   never shown. A map layer + a "local weather from the mesh" panel + pressure
   trend alerts turns the mesh into an instrument network the whole county can use,
   no internet required. **S–M.**

10. **MeshCore alongside Meshtastic** — the envelope is protocol-neutral by design
    (`proto`, opaque ids); a MeshCore adapter on the bus would put both meshes on
    one map, one feed, one digest, and let the BBS/Bobcat answer on whichever
    network the question came from. Research first (docs/OBSERVATORY_V2.md §7):
    what MeshCore's companion protocol exposes, how ids and channels map, whether
    a Den can be a node on both without two radios. **L, after a week of research.**

## Worth doing, smaller

- **Field-day mode** — a full-screen kiosk view for a tent or a club meeting: big
  map, big feed, no controls, auto-cycling panels. **S.**
- **"Who can hear whom" matrix** — a node × node table of direct-hear counts and
  SNR from neighbour reports; the topology page as a spreadsheet. **S.**
- **Link budget helper** — pick two nodes, get distance, bearing, Fresnel-zone
  ground clearance from an elevation API, and "you'd need N m of height". **M.**
- **Airtime accounting per node** — who's spending the channel (packets × bytes ×
  hops), with a polite public leaderboard. Half the airtime arguments end with a
  chart. **S.**
- **Traceroute on demand** — Meshtastic supports it; a button on a node page that
  runs one through meshd and draws the hops. **S–M.**
- **Voice notes to text** — dictate on the watch/phone, the Den transcribes locally
  (whisper.cpp) and sends the text; the mesh never sees audio. **M.**
- **Bobcat "office hours"** — the responder is on for two scheduled hours a day
  (a schedule row), off otherwise; cost-capped by construction. **S.**
- **Digest by mesh** — a 2-packet nightly summary DM'd to nodes that opted in
  (`?digest on`). The full digest stays on the board; the radio gets the
  headline. **S.**
- **Offline tile pack** — the Den serves its own OSM tiles for the county so the
  map works with no internet (the tile URL shape was chosen for this). **M** and
  ~2 GB of disk.
- **A second Den** — the whole stack on a second Pi at another site, sharing the
  bus over Tailscale; one map, two radios, twice the ears. The bus was designed
  for it. **M.**

## Bigger bets

- **Emergency operations mode** — one switch that turns the Den into an EOC board:
  check-in roll call (every node DMs "OK"), a resource board (who has a generator,
  a chainsaw, a medic), an assignments list, and the SOS/broadcast tiers above,
  all readable on any phone on the LAN with no internet. This is what the mesh is
  *for*; everything else is practice. **L.**
- **Multi-community federation** — Dens exchange digests and node passports over
  the internet when it's up; each stays sovereign when it isn't. Cincinnati
  and NKY meshes see each other's coverage without merging. **XL.**
- **A native app** — the Capacitor path in the vision doc; push notifications for
  SOS, a real watch app for sends, offline-first. Only after the web version has
  users who ask for it. **L.**

## What I would NOT build

- Chat features that make the mesh look like Discord. LoRa airtime is the scarce
  resource; every feature should respect it or spend it deliberately.
- Anything that needs the Den to be reachable from the open internet without
  the operator token and Tailscale. Resilience tools should not add attack surface.
- A second map framework, a build step, or a dependency on a paid tile provider.
  The current stack runs on a Pi with no accounts and that is a feature.
