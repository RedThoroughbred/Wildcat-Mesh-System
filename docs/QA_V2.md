# Observatory v2 — QA pass (2026-09-13) and how to re-run it

## Findings (all fixed unless marked open)

| # | Issue | Severity | Root cause | Fix |
|---|---|---|---|---|
| 1 | After "Replay", every other page kept the replay running invisibly; back on the map it was still replaying ("Live map behaves oddly") | **High** | Replay was a body-level mode; opening a view never exited it, and clicking "Live map" while already on `#/` fires no `hashchange` so nothing ran | Opening any view exits replay; the "Live map" entry handles the click itself (exits replay, closes views) even when the hash doesn't change |
| 2 | Sidebar actions (`#/coverage`, `#/replay`, `#/cat`) left the URL on an action route while the map was showing; refresh/back re-fired them; re-clicking did nothing | **High** | Actions were modelled as routes | After the action runs, `history.replaceState` puts the URL back to `#/`; replay on a cold load waits for history to arrive instead of toasting "nothing recorded" |
| 3 | Rapid navigation could paint a stale view (URL says Channels, body shows Nodes) | Medium (latent) | Each view's `render()` awaits fetches then writes `viewBody`; an older render finishing late overwrote the newer one | Every render carries a sequence number; writes are dropped when superseded (`tests/test_router_invariants.py` enforces the guard on all 14 writes) |
| 4 | Compose sheet stayed open on top of every view | Medium | Only Home panels were hidden in the `viewing` state | Compose (and the follow flag) hide while a view is open, reappear on Home |
| 5 | Node detail and channel detail showed no active sidebar entry | Low | `setNav` matched the route name literally | Detail routes highlight their parent (Nodes / Channels) |
| 6 | A map card opened before navigating survived into the next Home visit | Low | Cards weren't part of view state | Opening a view closes the card |
| 7 | Brand wasn't a link home; nav entries lacked tooltips in rail mode | Low | — | Brand links to `#/`; every entry has a title |
| 8 | Unknown hash (typo) showed nothing and the URL stayed wrong | Low | No fallback | Toast + `#/` |

Verified good already (no change): refresh on `#/node/<id>` / `#/channels` / `#/channel/<n>` restores the view; deep links in a fresh tab; Esc and × from every view; map/list/feed all land on the same node view; drawer closes on tap; Back/Forward walk the history (hash routing).

## Re-running

- Static invariants: `pytest tests/test_router_invariants.py` (runs with the suite).
- Behavioural: open `/v2` in a browser, paste `tests/qa/router_qa.js` into the console (or run it through Playwright's `evaluate`). Every line must begin with `OK`.
- Then at phone width: ☰ → each entry → view opens, drawer closes; × returns to the map.
- `/v2/public`: no ✎ compose, no analyst, no send/save/restart controls, no Admin entry, no layers/timeline.
