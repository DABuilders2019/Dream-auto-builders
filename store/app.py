"""Storefront web app: the shop pages (store/web.py) plus this JSON API, which the pages' scripts also use.

  GET /api/search    q, vehicle, category, brand (repeatable), price_min, price_max, carb, fit_only, sort, page, per_page
  GET /api/suggest   q, vehicle                      (search-as-you-type)
  GET /api/vehicles/years
  GET /api/vehicles/makes?year=
  GET /api/vehicles/models?year=&make=             (returns vehicle ids)
  GET /api/vehicles/{id}
  GET /api/health

Run locally:  DATABASE_URL=... uvicorn --factory store.app:create_app --reload

Settings (environment variables):
  DATABASE_URL    Postgres connection string (required)
  SECRET_KEY      signs the shopper's cookie (garage + cart). Set a long random value in production.
  COOKIE_SECURE   1 to send that cookie over HTTPS only (production)
  SITE_PREVIEW    1 (default) shows the "test site" ribbon and tells search engines not to index; 0 at launch
  SITE_PASSWORD   if set, visitors must enter it before seeing the shop (private test site)
  ADMIN_PASSWORD  turns on the admin page at /admin (catalog uploads, imports, photo downloads)
  DATA_DIR        folder for uploaded SEMA files and import logs (default: data)
  STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET, SITE_URL      checkout (see store/checkout.py)
  SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, MAIL_FROM, ORDER_NOTIFY_EMAIL   order emails (store/mailer.py)
  TURN14_CLIENT_ID, TURN14_CLIENT_SECRET, TURN14_ENV (test|production), TURN14_AUTO (1 = run on schedule)
                  Turn 14 stock and pricing feed (docs/TURN14.md)
  GOOGLE_MAPS_API_KEY nearby repair shops on order pages and emails (docs/INSTALLERS.md)
  SEMA_USERNAME, SEMA_PASSWORD, SEMA_AUTO (1 = check for brand updates nightly)
                  SEMA Data API catalog feed (docs/SEMA.md)
  PHOTO_STORAGE / PHOTO_DIR / S3_*   where product photos are (see importer/storage.py)

On start the database tables are created or upgraded, and the store categories and brands
from config/ are written to it.
"""
import contextlib
import logging
import os
import pathlib
import secrets
import threading
import time
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from psycopg_pool import ConnectionPool
from starlette.middleware.sessions import SessionMiddleware

from importer import db as importer_db
from importer import load as importer_load
from . import accounts, admin, banner_schedule, builds, checkout, freight, installers, launchcheck, pricematch, sema_schedule, tracking, turn14_schedule
from . import vehicles as veh
from . import web
from .jobs import Jobs
from .search import SORTS, Searcher
from .settings import search_config

REFRESH_SECONDS = 300   # pick up new imports (vehicles, categories) at most this often


class State:
    def __init__(self, dsn):
        self.dsn = dsn
        self.pool = ConnectionPool(dsn, min_size=1, max_size=int(os.environ.get("DB_POOL_SIZE", "10")),
                                   kwargs={"autocommit": True}, open=True)
        self.lock = threading.Lock()
        self.searcher, self.loaded_at, self.run_id = None, 0, None
        self.picker, self.picker_at, self.picker_busy = None, 0, False
        self.data_dir = pathlib.Path(os.environ.get("DATA_DIR", "data"))
        self.uploads_dir = self.data_dir / "uploads"
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.jobs = Jobs(self.data_dir, dsn, on_finish=self.reset)
        self.t14 = Jobs(self.data_dir, dsn, on_finish=self.reset, subdir="turn14", module="turn14")
        self.sema = Jobs(self.data_dir, dsn, on_finish=self.reset, subdir="sema", module="semadata")

    def reset(self):
        """Forget cached catalog data (after an import) so pages show it right away."""
        with self.lock:
            self.searcher, self.loaded_at = None, 0
            self.picker_at = 0          # rebuilt on next use; the old choices are served meanwhile
            self.__dict__.pop("page_cache", None)

    def get_picker(self):
        """The vehicle bar's choices. Rebuilt in the background when old, so no shopper waits for it."""
        now = time.time()
        if self.picker is not None and now - self.picker_at < REFRESH_SECONDS:
            return self.picker
        if self.picker is None:
            with self.pool.connection() as conn:
                self.picker, self.picker_at = veh.Picker(conn), now
            return self.picker
        with self.lock:
            if self.picker_busy:
                return self.picker
            self.picker_busy = True

        def rebuild():
            try:
                with self.pool.connection() as conn:
                    self.picker, self.picker_at = veh.Picker(conn), time.time()
            except Exception:      # noqa: BLE001 - keep serving the old choices
                logging.getLogger("store").exception("vehicle picker rebuild")
            finally:
                self.picker_busy = False
        threading.Thread(target=rebuild, daemon=True).start()
        return self.picker

    def prepare_database(self):
        with self.pool.connection() as conn:
            conn.autocommit = False
            importer_db.migrate(conn)
            importer_load.seed_reference(conn)
            importer_load.recategorize(conn, log=lambda m: logging.getLogger("store").info(m))
            conn.commit()
            conn.autocommit = True

    def get_searcher(self, conn):
        now = time.time()
        if self.searcher and now - self.loaded_at < REFRESH_SECONDS:
            return self.searcher
        with self.lock:
            run_id = conn.execute("SELECT max(id) FROM import_runs WHERE status = 'ok'").fetchone()[0]
            if self.searcher is None or run_id != self.run_id:
                self.searcher, self.run_id = Searcher(conn, search_config()), run_id
            self.loaded_at = now
        return self.searcher


def create_app(dsn=None):
    state = State(dsn or os.environ["DATABASE_URL"])

    state.prepare_database()

    @contextlib.asynccontextmanager
    async def lifespan(app):
        stop_t14 = turn14_schedule.launch(state)
        stop_sema = sema_schedule.launch(state)
        stop_surveys = installers.launch(state)
        stop_freight = freight.launch(state)
        stop_tracking = tracking.cleanup_loop(state)
        stop_banners = banner_schedule.launch(state)
        yield
        stop_t14.set()
        stop_sema.set()
        stop_surveys.set()
        stop_freight.set()
        stop_tracking.set()
        stop_banners.set()
        state.pool.close()

    app = FastAPI(title="Dream Auto Builders", docs_url="/api/docs", redoc_url=None, lifespan=lifespan)
    app.state.shop = state

    secret = os.environ.get("SECRET_KEY")
    if not secret:
        logging.getLogger("store").warning("SECRET_KEY is not set: using a temporary key, so carts reset when the server restarts")
        secret = secrets.token_urlsafe(32)
    accounts.install(app)            # signed-in shoppers' garage and builds kept on their account
    tracking.install(app)            # page views (inside the session middleware, for the visitor id)
    web.install_site_gate(app)       # (inside the session middleware below, so it can read the cookie)
    app.add_middleware(SessionMiddleware, secret_key=secret, session_cookie="dab", max_age=90 * 24 * 3600,
                       same_site="lax", https_only=os.environ.get("COOKIE_SECURE") == "1")
    app.mount("/static", StaticFiles(directory=str(pathlib.Path(__file__).resolve().parent / "static")), name="static")

    if os.environ.get("PHOTO_STORAGE", "local") == "local":
        photo_dir = os.environ.get("PHOTO_DIR", "data/photos")
        os.makedirs(photo_dir, exist_ok=True)
        app.mount("/photos", StaticFiles(directory=photo_dir), name="photos")

    @app.get("/api/health")
    def health():
        with state.pool.connection() as conn:
            n = conn.execute("SELECT count(*) FROM products WHERE status = 'active'").fetchone()[0]
        return {"ok": True, "active_products": n}

    @app.get("/api/search")
    def search(q: str = "", vehicle: Optional[int] = None, category: Optional[str] = None,
               brand: List[str] = Query(default=[]), price_min: Optional[float] = None, price_max: Optional[float] = None,
               carb: bool = False, fit_only: bool = True, sort: str = "relevance",
               page: int = Query(1, ge=1), per_page: int = Query(24, ge=1, le=96)):
        if sort not in SORTS:
            raise HTTPException(400, f"sort must be one of {sorted(SORTS)}")
        with state.pool.connection() as conn:
            s = state.get_searcher(conn)
            return s.search(conn, q, vehicle_id=vehicle, category=category, brands=brand, price_min=price_min,
                            price_max=price_max, carb_only=carb, fit_only=fit_only, sort=sort, page=page, per_page=per_page)

    @app.get("/api/suggest")
    def suggest(q: str = "", vehicle: Optional[int] = None):
        with state.pool.connection() as conn:
            return state.get_searcher(conn).suggest(conn, q, vehicle_id=vehicle)

    @app.get("/api/vehicles/years")
    def years():
        return state.get_picker().years()

    @app.get("/api/vehicles/makes")
    def makes(year: int):
        return state.get_picker().makes(year)

    @app.get("/api/vehicles/models")
    def models(year: int, make: str):
        return state.get_picker().models(year, make)

    @app.get("/api/vehicles/{vehicle_id}")
    def vehicle(vehicle_id: int):
        with state.pool.connection() as conn:
            v = veh.get(conn, vehicle_id)
        if not v:
            raise HTTPException(404, "vehicle not found")
        return v

    app.include_router(admin.router)
    app.include_router(checkout.router)
    app.include_router(installers.router)
    app.include_router(freight.router)
    app.include_router(tracking.router)
    app.include_router(builds.router)
    app.include_router(accounts.router)
    app.include_router(pricematch.router)
    app.include_router(launchcheck.router)
    web.install(app)
    return app

