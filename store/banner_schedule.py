"""Background pass that gives every brand with photos a page banner (new brands, or ones whose banner part is gone)."""
import logging
import threading

from importer import banners

log = logging.getLogger("store.banners")
EVERY = 20 * 60


def launch(state):
    stop = threading.Event()

    def loop():
        if stop.wait(90):           # let the site finish starting first
            return
        while True:
            try:
                if not state.jobs.running() and not state.sema.running():
                    made = 0
                    with state.pool.connection() as conn:
                        todo = banners.needs_banner(conn)
                    for brand in todo:                  # one brand at a time, with a pause, so pages stay quick
                        if stop.is_set() or state.jobs.running() or state.sema.running():
                            break
                        with state.pool.connection() as conn:
                            made += banners.ensure(conn, brand, log=log.info)
                        stop.wait(2)
                    if made:
                        state.reset()
            except Exception:       # noqa: BLE001
                log.warning("banner pass failed", exc_info=True)
            if stop.wait(EVERY):
                return
    threading.Thread(target=loop, daemon=True, name="banners").start()
    return stop
