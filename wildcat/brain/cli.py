"""The local ``claude`` CLI (Claude Code) as a model provider.

Resolution (same lesson Repot learned): ``$CLAUDE_BIN`` → ``shutil.which`` → the
usual Homebrew/npm/user paths. **launchd and systemd run services with a bare
PATH**, so ``which`` alone misses ``/opt/homebrew/bin``; the subprocess also gets
an environment with those dirs prepended.

Invocation is non-interactive, **tool-less** (``--tools ""`` + strict MCP: the
model can't touch files or a shell — it only ever sees the prompt we build),
streamed (``stream-json`` with partial messages), budget-capped, and fed the
prompt on **stdin**, never argv.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import time
from typing import Any, Dict, Iterator, List, Optional

log = logging.getLogger("wildcat.brain.cli")

_HOME = os.path.expanduser("~")
CANDIDATES = [
    os.path.join(_HOME, ".claude", "local", "claude"),
    "/opt/homebrew/bin/claude",
    "/usr/local/bin/claude",
    os.path.join(_HOME, ".npm-global", "bin", "claude"),
    os.path.join(_HOME, ".local", "bin", "claude"),
    "/usr/bin/claude",
]
_BIN_DIRS = ["/opt/homebrew/bin", "/usr/local/bin", os.path.join(_HOME, ".local", "bin"),
             os.path.join(_HOME, ".npm-global", "bin"), "/usr/bin", "/bin"]


def resolve(explicit: Optional[str] = None) -> Optional[str]:
    """Absolute path to the CLI, or None (with the reason logged)."""
    override = explicit or os.environ.get("CLAUDE_BIN")
    if override:
        return override if os.path.exists(override) else None
    found = shutil.which("claude")
    if found:
        return found
    for c in CANDIDATES:
        if os.path.exists(c):
            return c
    return None


def env() -> Dict[str, str]:
    e = os.environ.copy()
    dirs = _BIN_DIRS + e.get("PATH", "").split(":")
    e["PATH"] = ":".join(d for d in dict.fromkeys(dirs) if d)
    return e


_version_cache: Dict[str, str] = {}


def version(binary: str) -> str:
    if binary in _version_cache:
        return _version_cache[binary]
    try:
        out = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=15, env=env()).stdout.strip()
    except (OSError, subprocess.SubprocessError) as e:
        out = f"unknown ({e})"
    _version_cache[binary] = out.splitlines()[0] if out else "unknown"
    return _version_cache[binary]


def status(explicit: Optional[str] = None) -> Dict[str, Any]:
    b = resolve(explicit)
    return {"available": bool(b), "path": b, "version": version(b) if b else None,
            "hint": None if b else "install Claude Code (npm i -g @anthropic-ai/claude-code) or set CLAUDE_BIN"}


def stream(prompt: str, system: str, model: str, timeout: int = 120, max_budget_usd: float = 0.50,
           binary: Optional[str] = None) -> Iterator[Dict[str, Any]]:
    """Yield ``{"type": "delta", "text"}`` as the answer streams, then one
    ``{"type": "done", "text", "cost_usd", "ms", "model", "is_error"}``; on failure a single
    ``{"type": "error", "text"}``. Never raises."""
    b = binary or resolve()
    if not b:
        yield {"type": "error", "text": "claude CLI not found — set CLAUDE_BIN or install Claude Code"}
        return
    args = [b, "--print", "--output-format", "stream-json", "--verbose", "--include-partial-messages",
            "--tools", "", "--strict-mcp-config", "--no-session-persistence",
            "--model", model, "--max-budget-usd", f"{max_budget_usd:.2f}", "--system-prompt", system]
    t0 = time.time()
    try:
        proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, env=env(), bufsize=1)
    except OSError as e:
        yield {"type": "error", "text": f"could not start claude: {e}"}
        return
    assert proc.stdin and proc.stdout
    try:
        proc.stdin.write(prompt); proc.stdin.close()
    except (OSError, ValueError) as e:
        proc.kill(); yield {"type": "error", "text": f"could not send the prompt: {e}"}; return

    killer = threading.Timer(timeout, lambda: proc.kill())
    killer.start()
    got_delta = False; final_text: Optional[str] = None; cost = None; is_error = False; assistant_text: List[str] = []
    try:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                j = json.loads(line)
            except ValueError:
                continue
            t = j.get("type")
            if t == "stream_event":
                ev = j.get("event") or {}
                if ev.get("type") == "content_block_delta" and (ev.get("delta") or {}).get("type") == "text_delta":
                    got_delta = True
                    yield {"type": "delta", "text": ev["delta"]["text"]}
            elif t == "assistant":
                for blk in ((j.get("message") or {}).get("content") or []):
                    if blk.get("type") == "text" and blk.get("text"):
                        assistant_text.append(blk["text"])
            elif t == "result":
                final_text = j.get("result"); cost = j.get("total_cost_usd"); is_error = bool(j.get("is_error"))
    finally:
        killer.cancel()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    ms = int((time.time() - t0) * 1000)
    if proc.returncode not in (0, None) and final_text is None:
        err = (proc.stderr.read() if proc.stderr else "")[-400:].strip()
        yield {"type": "error", "text": err or f"claude exited {proc.returncode}" + (" (timed out)" if ms >= timeout * 1000 else "")}
        return
    text = final_text if final_text is not None else "".join(assistant_text)
    if not got_delta and text:
        yield {"type": "delta", "text": text}
    yield {"type": "done", "text": text or "", "cost_usd": cost, "ms": ms, "model": model, "is_error": is_error}
