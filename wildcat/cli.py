"""``wildcat`` — the operator CLI for The Den.

    wildcat config path        # which file would be loaded, and why
    wildcat config validate    # parse + validate; exit 0/2; prints every problem
    wildcat config show        # the fully-resolved config as TOML (defaults filled in)
    wildcat config migrate     # legacy bbs/config.ini → TOML (add --write to save it)
    wildcat doctor             # (Phase 1 step 3) preflight: config, deps, radio, db, mqtt

Exit codes: 0 ok · 1 runtime failure · 2 config problem.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import List, Optional

from . import __version__, paths
from .config import ConfigError, load, load_file, to_toml

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_CONFIG = 2


def _add_config_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument("-c", "--config", help=f"config file (default: ${paths.ENV_CONFIG} or {paths.default_config_path()})")


def cmd_config_path(args: argparse.Namespace) -> int:
    print("Search order:")
    for reason, path in paths.config_search_paths(args.config):
        mark = "FOUND" if path.is_file() else "  -  "
        print(f"  [{mark}] {path}   ({reason})")
    try:
        path, kind = paths.find_config(args.config)
    except FileNotFoundError as e:
        print(f"\n{e}", file=sys.stderr)
        return EXIT_CONFIG
    print(f"\nWould load: {path}  [{kind}]")
    return EXIT_OK


def cmd_config_validate(args: argparse.Namespace) -> int:
    try:
        cfg = load(args.config)
    except ConfigError as e:
        print(f"CONFIG ERROR\n{e}", file=sys.stderr)
        return EXIT_CONFIG
    print(f"OK  {cfg.source}  [{cfg.source_kind}]")
    for w in cfg.warnings:
        print(f"WARN {w}")
    return EXIT_OK


def cmd_config_show(args: argparse.Namespace) -> int:
    try:
        cfg = load(args.config)
    except ConfigError as e:
        print(f"CONFIG ERROR\n{e}", file=sys.stderr)
        return EXIT_CONFIG
    print(f"# resolved from {cfg.source} [{cfg.source_kind}]")
    print(to_toml(cfg, redact=not args.no_redact))
    return EXIT_OK


def cmd_config_migrate(args: argparse.Namespace) -> int:
    ini = Path(args.ini).expanduser().resolve() if args.ini else paths.legacy_ini_path()
    if not ini.is_file():
        print(f"no legacy INI at {ini} (nothing to migrate)", file=sys.stderr)
        return EXIT_CONFIG
    try:
        cfg = load_file(ini)
    except ConfigError as e:
        print(f"CONFIG ERROR in {ini}\n{e}", file=sys.stderr)
        return EXIT_CONFIG
    text = to_toml(cfg)
    header = (
        f"# config/wildcat.toml — generated from {ini} by `wildcat config migrate`\n"
        f"# Every key is explicit here; delete any line to fall back to the default.\n"
        f"# See config/wildcat.example.toml for the annotated reference.\n\n"
    )
    out = Path(args.output).expanduser().resolve() if args.output else paths.default_config_path()
    if not args.write:
        print(header + text)
        print(f"\n# (dry run) add --write to save this to {out}", file=sys.stderr)
        return EXIT_OK
    if out.exists() and not args.force:
        print(f"refusing to overwrite existing {out} (use --force)", file=sys.stderr)
        return EXIT_FAIL
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(header + text, encoding="utf-8")
    # Prove the round-trip before declaring victory.
    try:
        load_file(out)
    except ConfigError as e:
        print(f"wrote {out} but it failed to validate:\n{e}", file=sys.stderr)
        return EXIT_FAIL
    print(f"wrote {out}\nThe legacy INI at {ini} is no longer read (wildcat.toml takes priority). "
          f"Keep it or delete it.")
    for w in cfg.warnings:
        if "LEGACY INI" not in w:
            print(f"WARN {w}")
    return EXIT_OK


def cmd_doctor(args: argparse.Namespace) -> int:
    try:
        from . import doctor  # Phase 1 step 3
    except ImportError:
        print("`wildcat doctor` is not built yet (Phase 1 step 3). Use `wildcat config validate` for now.",
              file=sys.stderr)
        return EXIT_FAIL
    return doctor.run(args)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="wildcat", description="Wildcat Mesh — The Den operator CLI")
    p.add_argument("--version", action="version", version=f"wildcat {__version__}")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = p.add_subparsers(dest="cmd", required=True)

    pc = sub.add_parser("config", help="inspect / validate / migrate the config")
    csub = pc.add_subparsers(dest="config_cmd", required=True)

    s = csub.add_parser("path", help="show the search order and which file wins")
    _add_config_arg(s)
    s.set_defaults(func=cmd_config_path)

    s = csub.add_parser("validate", help="load + validate; exit 2 on any problem")
    _add_config_arg(s)
    s.set_defaults(func=cmd_config_validate)

    s = csub.add_parser("show", help="print the fully-resolved config as TOML")
    _add_config_arg(s)
    s.add_argument("--no-redact", action="store_true", help="show secrets in clear")
    s.set_defaults(func=cmd_config_show)

    s = csub.add_parser("migrate", help="convert legacy bbs/config.ini to config/wildcat.toml")
    s.add_argument("--ini", help=f"legacy INI to read (default: {paths.legacy_ini_path()})")
    s.add_argument("-o", "--output", help=f"where to write (default: {paths.default_config_path()})")
    s.add_argument("--write", action="store_true", help="actually write the file (default: print only)")
    s.add_argument("--force", action="store_true", help="overwrite an existing output file")
    s.set_defaults(func=cmd_config_migrate)

    d = sub.add_parser("doctor", help="preflight checks: config, deps, database, radio, mqtt")
    _add_config_arg(d)
    d.add_argument("--no-network", action="store_true", help="skip radio / broker reachability probes")
    d.set_defaults(func=cmd_doctor)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    return int(args.func(args))


if __name__ == "__main__":  # python -m wildcat.cli
    sys.exit(main())
