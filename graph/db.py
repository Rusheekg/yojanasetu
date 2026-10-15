"""FalkorDB connection shared by the loader, the syntax check and the queries."""

import os
import sys

from falkordb import FalkorDB

GRAPH_NAME = "yojanasetu"


def connect():
    """Build a client from environment variables only.

    WHY: AGENTS.md forbids hardcoded hosts or passwords; failing early with a
    clear message is friendlier than a socket error later. Keeping this in one
    place means every script connects the same way.
    """
    missing = [v for v in ("FALKORDB_HOST", "FALKORDB_PORT") if not os.environ.get(v)]
    if missing:
        sys.exit("Missing environment variables: " + ", ".join(missing))
    return FalkorDB(
        host=os.environ["FALKORDB_HOST"],
        port=int(os.environ["FALKORDB_PORT"]),
        username=os.environ.get("FALKORDB_USERNAME") or None,
        password=os.environ.get("FALKORDB_PASSWORD") or None,
    )
