"""What release this process is running."""

from __future__ import annotations

import sys
from functools import cache
from pathlib import Path


@cache
def release_id() -> str:
    """The commit sha of this release, from the REVISION file at the release's root.

    A release is laid out as <sha>/python (the runtime, with this package installed in it), with
    REVISION beside it. Outside a release (development, tests) this is "dev".
    """

    revision = Path(sys.prefix).parent / "REVISION"
    try:
        return revision.read_text().strip() or "dev"
    except OSError:
        return "dev"
