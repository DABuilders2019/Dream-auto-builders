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
  PHOTO_STORAGE / PHOTO_DIR / S3_*   where product photos are (see importer/storage.py)
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

from . import vehicles as veh
from . import web
from .search import SORTS, Searcher
from .settings import search_config

REFRESH_SECONDS = 300   # pick up new imports (vehicles, categories) at most this often


class State:
    def __init__(self, dsn):
        self.pool = ConnectionPool(dsn, min_size=1, max_size=int(os.environ.get("DB_POOL_SIZE", "10")),
                                   kwargs={"autocommit": True}, open=True)
        self.lock = threading.Lock()
        self.searcher, self.loaded_at, self.run_id = None, 0, None

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

    @contextlib.asynccontextmanager
    async def lifespan(app):
        yield
        state.pool.close()

    app = FastAPI(title="Dream Auto Builders", docs_url="/api/docs", redoc_url=None, lifespan=lifespan)
    app.state.shop = state

    secret = os.environ.get("SECRET_KEY")
    if not secret:
        logging.getLogger("store").warning("SECRET_KEY is not set: using a temporary key, so carts reset when the server restarts")
        secret = secrets.token_urlsafe(32)
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
        with state.pool.connection() as conn:
            return veh.years(conn)

    @app.get("/api/vehicles/makes")
    def makes(year: int):
        with state.pool.connection() as conn:
            return veh.makes(conn, year)

    @app.get("/api/vehicles/models")
    def models(year: int, make: str):
        with state.pool.connection() as conn:
            return veh.models(conn, year, make)

    @app.get("/api/vehicles/{vehicle_id}")
    def vehicle(vehicle_id: int):
        with state.pool.connection() as conn:
            v = veh.get(conn, vehicle_id)
        if not v:
            raise HTTPException(404, "vehicle not found")
        return v

    web.install(app)
    return app

