"""meshd — the single process that owns the radio (Phase 1 step 5).

* :mod:`.packets`  — pure: meshtastic packet dict → bus envelope (JSON-safe, symmetric)
* :mod:`.txqueue`  — pure: prioritized TX queue, chunking, airtime pacing
* :mod:`.daemon`   — the service: radio ↔ bus, node snapshots, reconnect
"""
