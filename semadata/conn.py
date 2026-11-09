"""A database connection for long SEMA jobs that reconnects once if the database drops it.

A brand load can run for hours. If the database closes the connection (a restart, maintenance, or a network
hiccup: "SSL error: unexpected eof"), the next statement opens a new connection and runs again, so the job can
still record that the brand loaded (or failed) instead of crashing. Only statements run through execute() are
retried, and only when the connection is actually gone; a statement the database rejected is never repeated.
"""
import logging

import psycopg

log = logging.getLogger("semadata.conn")

# TCP keepalives so an idle connection isn't silently dropped along the way
KEEPALIVE = {"keepalives": 1, "keepalives_idle": 30, "keepalives_interval": 10, "keepalives_count": 5}


class ReconnectingConnection:
    def __init__(self, url, connect=None):
        self.url = url
        self._connect = connect or (lambda: psycopg.connect(url, autocommit=True, **KEEPALIVE))
        self.reconnects = 0
        self._c = self._connect()

    @property
    def raw(self):
        return self._c

    def reconnect(self):
        try:
            self._c.close()
        except Exception:        # noqa: BLE001
            pass
        self._c = self._connect()
        self.reconnects += 1
        log.warning("database connection was closed; reconnected")
        return self._c

    def execute(self, query, params=None, **kw):
        try:
            return self._c.execute(query, params, **kw)
        except psycopg.OperationalError:
            if not self._c.closed and not self._c.broken:
                raise                    # the database answered with an error: don't retry
            self.reconnect()
            return self._c.execute(query, params, **kw)

    def __getattr__(self, name):         # cursor(), transaction(), commit(), closed, ... go to the live connection
        return getattr(self._c, name)
