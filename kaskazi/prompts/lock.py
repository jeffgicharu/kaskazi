"""Adds new prompt versions to prompts/prompts.lock. Never rewrites a version already there.

uv run python -m kaskazi.prompts.lock
"""

from __future__ import annotations

import sys

from kaskazi.prompts.registry import lock_problems, write_lock


def main() -> int:
    write_lock()
    problems = lock_problems()
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
