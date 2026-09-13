"""Absolute-path resolution for The Den.

Rule #1 of v2: **nothing depends on the current working directory.** Every
path here is derived from where this package lives on disk (or from an
explicit override), so a service started by systemd, a shell in ``/``, or a
``cd bbs && python server.py`` all resolve the same files.

Pure stdlib, no third-party imports — ``wildcat doctor`` must be able to
import this even when the venv is broken.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional, Tuple

ENV_CONFIG = "WILDCAT_CONFIG"
CONFIG_FILENAME = "wildcat.toml"
LEGACY_INI_NAME = "config.ini"

_PACKAGE_DIR = Path(__file__).resolve().parent


def repo_root() -> Path:
    """The checkout that contains this package.

    ``wildcat/`` lives directly under the repo root, so the root is our parent.
    If the package was installed non-editable into site-packages we can't know
    the checkout; callers fall back to the config file's location in that case
    (see :func:`project_root_for`).
    """
    return _PACKAGE_DIR.parent


def is_checkout(root: Path) -> bool:
    """True when ``root`` looks like a Wildcat-Mesh-System checkout."""
    return (root / "bbs").is_dir() and (root / "wildcat").is_dir()


def project_root_for(config_path: Optional[Path]) -> Path:
    """Where relative data paths (db, bbs content) resolve from.

    Prefer the checkout containing this package; if that isn't a checkout
    (site-packages install), use the directory *above* the config file, so
    ``/opt/wildcat/config/wildcat.toml`` → ``/opt/wildcat``.
    """
    root = repo_root()
    if is_checkout(root):
        return root
    if config_path is not None:
        return config_path.resolve().parent.parent
    return root


def default_config_path() -> Path:
    return repo_root() / "config" / CONFIG_FILENAME


def legacy_ini_path() -> Path:
    return repo_root() / "bbs" / LEGACY_INI_NAME


def config_search_paths(explicit: Optional[str] = None) -> List[Tuple[str, Path]]:
    """Every location we consider, in priority order, as ``(reason, path)``.

    Returned in full (not just the first hit) so error messages can list
    exactly what was tried — the #1 complaint about configparser's silent
    empty read.
    """
    candidates: List[Tuple[str, Path]] = []
    if explicit:
        candidates.append(("--config", Path(explicit).expanduser().resolve()))
    env = os.environ.get(ENV_CONFIG)
    if env:
        candidates.append((f"${ENV_CONFIG}", Path(env).expanduser().resolve()))
    candidates.append(("repo default", default_config_path()))
    candidates.append(("system", Path("/etc/wildcat") / CONFIG_FILENAME))
    candidates.append(("legacy bbs/config.ini", legacy_ini_path()))
    return candidates


def find_config(explicit: Optional[str] = None) -> Tuple[Path, str]:
    """Return ``(path, kind)`` for the first config that exists.

    ``kind`` is ``"toml"`` or ``"legacy-ini"``. Raises ``FileNotFoundError``
    listing every path tried when nothing exists. An explicit ``--config`` or
    ``$WILDCAT_CONFIG`` that does not exist is an error on its own — we never
    silently fall through past a path the operator asked for.
    """
    tried = config_search_paths(explicit)
    for reason, path in tried:
        if path.is_file():
            kind = "legacy-ini" if path.suffix.lower() == ".ini" else "toml"
            return path, kind
        if reason in ("--config", f"${ENV_CONFIG}"):
            raise FileNotFoundError(
                f"config file given by {reason} does not exist: {path}"
            )
    listing = "\n".join(f"  - {path}  ({reason})" for reason, path in tried)
    raise FileNotFoundError(
        "no Wildcat config found. Looked for:\n" + listing +
        f"\nCreate one: copy config/{CONFIG_FILENAME.replace('.toml', '.example.toml')} "
        f"to config/{CONFIG_FILENAME}, or run `wildcat config migrate --write`."
    )
