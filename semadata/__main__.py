"""python -m semadata test
   python -m semadata load [AAIA ...] [--refresh-fitment]   (no codes: brands on our list that are new or changed)"""
import json
import os
import sys

from .client import StopSync
from .sync import run


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if not argv or argv[0] not in ("test", "load"):
        print(__doc__)
        return 2
    from importer import db
    from .conn import ReconnectingConnection
    conn = ReconnectingConnection(os.environ["DATABASE_URL"])
    db.migrate(conn.raw)
    try:
        stats = run(conn, argv[0], argv[1:], log=lambda m: print(m, flush=True))
    except StopSync as e:
        print(f"STOPPED: {e}", flush=True)
        return 3
    print(json.dumps({k: v for k, v in stats.items() if k not in ("task",)}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
