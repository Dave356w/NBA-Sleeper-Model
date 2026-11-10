from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .common import iso, load_config
from .pipeline import capture, export_active, grade, refit, validate
from .storage import Ledger


def main(argv=None):
    parser = argparse.ArgumentParser(description="Sleeper-only NBA model")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)
    c = commands.add_parser("capture")
    c.add_argument("--dry-run", action="store_true")
    for name in ["grade", "refit", "report", "build-site", "validate", "run", "backtest"]:
        commands.add_parser(name)
    wake = commands.add_parser("next-wake")
    wake.add_argument("--tick-seconds", type=int, default=600)
    legacy = commands.add_parser("import-legacy")
    legacy.add_argument("path", type=Path)
    legacy.add_argument("--research-only", action="store_true", required=True)
    args = parser.parse_args(argv)
    root, ledger = args.root.resolve(), Ledger(args.root.resolve())
    config = load_config(root)
    try:
        if args.command == "capture" and args.dry_run:
            result = capture(root, config, dry_run=True)
        elif args.command == "validate":
            result = validate(root)
        elif args.command == "next-wake":
            # Read-only planning for the watcher loop; creates no records.
            from .common import utcnow
            from .pipeline import next_wake

            result = next_wake(ledger.records(), config, utcnow(), tick_seconds=args.tick_seconds)
        elif args.command == "backtest":
            from .research import walk_forward

            result = walk_forward(root, config)
        elif args.command == "import-legacy":
            # A research-only namespace cannot enter the native forecast ledger.
            data = json.loads(args.path.read_text())
            from .common import digest

            path = root / "research" / "output" / ("legacy-first-" + digest(data) + ".json")
            Ledger._atomic(
                path, json.dumps({"provenance": "legacy-first", "imported_at": iso(), "data": data}, indent=2)
            )
            result = {"research_only": True, "path": str(path)}
        else:
            with ledger.lock():
                if args.command == "capture":
                    result = capture(root, config)
                elif args.command == "grade":
                    result = grade(root, config)
                elif args.command == "refit":
                    result = refit(root, config)
                elif args.command in ["report", "build-site"]:
                    from .site import build_site

                    result = build_site(root)
                elif args.command == "run":
                    # Grade/refit prior results before generating new forecasts.
                    result = {
                        "grade": grade(root, config),
                        "refit": refit(root, config),
                        "capture": capture(root, config),
                        "validation": validate(root),
                    }
                    export_active(root)
                    from .site import build_site

                    result["site"] = build_site(root)
                else:
                    parser.error("Unknown command")
        print(json.dumps(result, indent=2))
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
