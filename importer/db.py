"""Database connection and migrations."""
import os
import pathlib

import psycopg

MIGRATIONS = pathlib.Path(__file__).parent / "migrations"


def connect():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is not set. Example: postgresql://user:pass@host:5432/dbname")
    return psycopg.connect(url)


def migrate(conn):
    """Apply any migrations in importer/migrations that haven't run yet. Returns the versions applied."""
    conn.commit()   # start from a clean transaction so each migration commits on its own
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
        cur.execute("SELECT version FROM schema_migrations")
        done = {r[0] for r in cur.fetchall()}
    conn.commit()
    applied = []
    for path in sorted(MIGRATIONS.glob("*.sql")):
        version = path.stem
        if version in done:
            continue
        sql = path.read_text().replace("CREATE TABLE schema_migrations", "CREATE TABLE IF NOT EXISTS schema_migrations")
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(sql)
            cur.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))
        applied.append(version)
    conn.commit()
    return applied
