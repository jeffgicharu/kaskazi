"""Release commands, run by the activation script as the app's own user.

python -I -m kaskazi.release version            imports everything compiled, prints the sha
python -I -m kaskazi.release migrate            applies the database migrations
python -I -m kaskazi.release load --release S   loads this release's documents and prompts
python -I -m kaskazi.release check ...          the release checks against the idle slot
python -m kaskazi.release gate                   CI: a passing golden-set report covers this AI
python -m kaskazi.release fingerprint            the AI fingerprint of this tree, as JSON
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import os
import pkgutil
import sys
import sysconfig
from pathlib import Path

import kaskazi
from kaskazi.release import release_id


def version() -> int:
    """Imports every compiled module in the runtime and every module of the app.

    On the server this is the CPU check: a wheel built for newer instructions than the server
    has fails here, before the release starts.
    """

    failures: list[str] = []
    site = Path(sysconfig.get_paths()["purelib"])
    for so in sorted(site.rglob("*.so")):
        parts = list(so.relative_to(site).with_suffix("").parts)
        parts[-1] = parts[-1].split(".")[0]
        if parts[0].endswith(".libs"):
            continue  # vendored shared libraries, loaded by the modules that need them
        name = ".".join(parts)
        try:
            importlib.import_module(name)
        except Exception as exc:
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
    for module in pkgutil.walk_packages(kaskazi.__path__, prefix="kaskazi."):
        if module.name.endswith("__main__"):
            continue
        try:
            importlib.import_module(module.name)
        except Exception as exc:
            failures.append(f"{module.name}: {type(exc).__name__}: {exc}")
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print(release_id())
    return 0


async def migrate() -> int:
    from kaskazi.db.migrate import migrate as run

    applied = await run(os.environ["KASKAZI_OWNER_DATABASE_URL"])
    print("applied: " + (", ".join(applied) or "nothing new"))
    return 0


async def load(release: str) -> int:
    from kaskazi.release.load import load_release

    summary = await load_release(os.environ["KASKAZI_OWNER_DATABASE_URL"], release)
    print(summary)
    return 0


async def check(args: argparse.Namespace) -> int:
    from kaskazi.release.check import run_checks

    ok = await run_checks(
        api=args.api, gateway=args.gateway, release=args.release, stand_in=args.stand_in
    )
    return 0 if ok else 1


def evaluation_gate() -> int:
    from kaskazi.release.evalgate import check_release

    ok, why = check_release()
    print(why, file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(prog="kaskazi.release")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("version")
    sub.add_parser("migrate")
    sub.add_parser("gate")
    sub.add_parser("fingerprint")
    p = sub.add_parser("load")
    p.add_argument("--release", required=True)
    p = sub.add_parser("check")
    p.add_argument("--api", required=True)
    p.add_argument("--gateway", required=True)
    p.add_argument("--release", required=True)
    p.add_argument(
        "--stand-in-providers",
        dest="stand_in",
        action="store_true",
        help="CI only: the gateway runs stand-in providers",
    )
    args = parser.parse_args()
    if args.command == "version":
        return version()
    if args.command == "migrate":
        return asyncio.run(migrate())
    if args.command == "load":
        return asyncio.run(load(args.release))
    if args.command == "gate":
        return evaluation_gate()
    if args.command == "fingerprint":
        import json

        from kaskazi.release.fingerprint import fingerprint

        print(json.dumps(fingerprint(), indent=2))
        return 0
    return asyncio.run(check(args))


if __name__ == "__main__":
    sys.exit(main())
