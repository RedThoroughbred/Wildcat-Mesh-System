"""Bobcat — the Den's AI.

Part A (operator analyst, ``analyst.py``): Seth chats with the local ``claude`` CLI about
his mesh; the model may propose read-only SQL which is hard-gated (``sqlgate.py``) and
executed against a read-only connection. Part B (mesh-facing responder, ``responder.py``):
answers ``?``-prefixed DMs over LoRa with rate limits, an airtime brake and a hard
off-by-default switch. Both share ``cli.py`` (CLI resolution + streaming) and
``context.py`` (the mesh brief).
"""
