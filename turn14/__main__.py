"""python -m turn14 <task>   (see turn14/sync.py for the tasks)"""
import json
import os
import sys

import psycopg

from .client import StopSync, environment
from .sync import run


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 1:
        print(__doc__)
        return 2
    task = argv[0]
    conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)
    log_conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)
    print(f"Turn 14 {task} on the {'testing' if environment() == 'test' else 'LIVE'} server", flush=True)
    try:
        stats = run(conn, task, log=lambda m: print(m, flush=True), log_conn=log_conn)
    except StopSync as e:
        print(f"STOPPED: {e}", flush=True)
        return 3
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
