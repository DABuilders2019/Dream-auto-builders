"""Turn 14 sync against a fake Turn 14 server: token reuse, paging, the 429 rule, stopping on errors,
the usage cap, brand approval, part matching, pricing and stock, and stock shown in the shop."""
import json

import httpx
import psycopg
import pytest

from conftest import make_pies, make_vehicles
from importer import load
from test_search import FITMENT, ITEMS
from turn14 import client as t14client
from turn14.client import Client, StopSync
from turn14.sync import run


def item(iid, mfr, brand=1, **kw):
    return {"id": iid, "type": "Item", "attributes": {"product_name": f"Item {mfr}", "part_number": "tst" + mfr,
            "mfr_part_number": mfr, "brand_id": brand, "brand": "TestBrand", "active": True, "regular_stock": True, **kw}}


class FakeTurn14:
    def __init__(self):
        self.calls = []
        self.fail = {}          # path -> list of status codes to return first
        self.items_pages = [
            [item("100", "LK-100"), item("100G", "LK-100"), item("200", "XX-0", barcode="0012345678905")],
            [item("300", "LV 201"), item("999", "NOT-OURS")],
        ]

    def __call__(self, request):
        path = request.url.path
        page = int(request.url.params.get("page", 1))
        self.calls.append((request.method, path, dict(request.url.params)))
        if self.fail.get(path):
            code = self.fail[path].pop(0)
            return httpx.Response(code, json={"errors": [{"detail": f"fake {code}"}]})
        if path == "/v1/token":
            body = json.loads(request.content)
            assert body["grant_type"] == "client_credentials" and body["client_id"] == "cid"
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600, "token_type": "Bearer"})
        assert request.headers["Authorization"] == "Bearer tok"
        if path == "/v1/brands":
            return httpx.Response(200, json={"data": [
                {"id": "1", "type": "Brand", "attributes": {"name": "Test Brand Inc", "AAIA": ["TEST"],
                 "pricegroups": [{"pricegroup_id": 5, "purchase_restrictions": [{"program": "authorized", "your_status": "approved"}]}]}},
                {"id": "2", "type": "Brand", "attributes": {"name": "Someone Else", "AAIA": ["ZZZZ"], "pricegroups": []}}]})
        if path == "/v1/locations":
            return httpx.Response(200, json={"data": [{"id": "01", "type": "Location", "attributes": {"Name": "Turn 14 East", "City": "Hatfield", "State": "PA"}},
                                                      {"id": "02", "type": "Location", "attributes": {"Name": "Turn 14 Texas", "City": "Flower Mound", "State": "TX"}}]})
        if path == "/v1/items/brand/1":
            return httpx.Response(200, json={"meta": {"total_pages": 2}, "data": self.items_pages[page - 1]})
        if path == "/v1/items/data/brand/1":      # photos for our parts that have none
            return httpx.Response(200, json={"meta": {"total_pages": 1}, "data": [
                {"id": "100G", "type": "ProductData", "files": [{"type": "Image", "media_content": "Photo - Primary",
                 "links": [{"url": "https://img.example/100g.jpg", "width": 800, "height": 800}]}]}]})
        if path == "/v1/pricing/brand/1":
            return httpx.Response(200, json={"meta": {"total_pages": 1}, "data": [
                {"id": "100G", "type": "PricingItem", "attributes": {"purchase_cost": 700.0, "has_map": True, "can_purchase": True,
                 "pricelists": [{"name": "MAP", "price": 949.95}, {"name": "Retail", "price": 999.95}]}},
                {"id": "300", "type": "PricingItem", "attributes": {"purchase_cost": 150.0, "has_map": False, "can_purchase": True, "pricelists": []}}]})
        if path == "/v1/inventory/brand/1":
            return httpx.Response(200, json={"meta": {"total_pages": 1}, "data": [
                {"id": "100G", "type": "InventoryItem", "attributes": {"inventory": {"01": 2, "02": 1}, "manufacturer": {"stock": 5, "esd": "2026-11-10"}}},
                {"id": "300", "type": "InventoryItem", "attributes": {"inventory": {"01": 0, "02": 0}, "manufacturer": {"stock": 7}}}]})
        if path == "/v1/inventory/updates":
            assert request.url.params["minutes"] in {"15", "30", "60", "240", "480", "1440"}
            return httpx.Response(200, json={"meta": {"total_pages": 1}, "data": [
                {"id": "300", "type": "InventoryItem", "attributes": {"inventory": {"01": 4}, "manufacturer": {"stock": 0}}},
                {"id": "555555", "type": "InventoryItem", "attributes": {"inventory": {"01": 9}}}]})
        if path == "/v1/items/updates":
            assert 1 <= int(request.url.params["days"]) <= 15
            return httpx.Response(200, json={"meta": {"total_pages": 1}, "data": [item("400", "CAT-1"), item("888", "X", brand=2)]})
        return httpx.Response(404, json={"errors": [{"detail": "no route"}]})


@pytest.fixture
def t14(conn, tmp_path, monkeypatch):
    load.load_brand(conn, "testbrand", make_pies(tmp_path / "p.xml", ITEMS), None,
                    make_vehicles(tmp_path / "v.xlsx", FITMENT), log=lambda *a: None)
    conn.autocommit = True
    conn.execute("UPDATE products SET upc = '012345678905' WHERE part_number = 'LV-200'")
    monkeypatch.setenv("TURN14_CLIENT_ID", "cid")
    monkeypatch.setenv("TURN14_CLIENT_SECRET", "secret")
    monkeypatch.setenv("T14_SKIP_PHOTOS", "1")         # photo links are added, not downloaded
    fake = FakeTurn14()
    slept = []

    def make(task="test"):
        return Client(conn, task=task, transport=httpx.MockTransport(fake), sleep=slept.append, log=lambda *a: None)

    return conn, fake, make, slept


def go(conn, make, task):
    return run(conn, task, log=lambda *a: None, client=make(task))


def test_setup_matches_and_loads(t14):
    conn, fake, make, slept = t14
    stats = go(conn, make, "setup")
    assert stats["our_brands_at_turn14"] == 1 and stats["items"] == 5
    rows = dict(conn.execute("""SELECT p.part_number, i.item_id || ':' || i.match_method FROM t14_items i
                                JOIN products p ON p.id = i.product_id""").fetchall())
    assert rows == {"LK-100": "100G:part_number", "LV-200": "200:barcode", "LV-201": "300:part_number"}
    assert conn.execute("SELECT brand_id FROM t14_brands WHERE id = 1").fetchone()[0] == "testbrand"
    assert conn.execute("SELECT brand_id FROM t14_brands WHERE id = 2").fetchone()[0] is None
    assert float(conn.execute("SELECT map_price FROM t14_pricing WHERE item_id = '100G'").fetchone()[0]) == 949.95
    assert conn.execute("SELECT total FROM t14_inventory WHERE item_id = '100G'").fetchone()[0] == 3
    # every list read with paging, never item by item; one token for the whole run
    assert [c for c in fake.calls if c[1] == "/v1/token"].__len__() == 1
    assert ("GET", "/v1/items/brand/1", {"page": "2"}) in fake.calls
    assert all("page" in c[2] for c in fake.calls if c[1].startswith(("/v1/items", "/v1/pricing", "/v1/inventory")))


def test_token_is_reused_between_runs(t14):
    conn, fake, make, _ = t14
    go(conn, make, "test")
    go(conn, make, "test")
    assert len([c for c in fake.calls if c[1] == "/v1/token"]) == 1


def test_429_stops_the_sync(t14):
    conn, fake, make, slept = t14
    fake.fail["/v1/brands"] = [429]
    with pytest.raises(StopSync, match="429"):
        go(conn, make, "test")
    assert 60 not in slept and len([c for c in fake.calls if c[1] == "/v1/brands"]) == 1     # no wait, no retry


@pytest.mark.parametrize("code", [400, 401, 403, 404, 500, 503])
def test_other_errors_stop_the_sync(t14, code):
    conn, fake, make, _ = t14
    fake.fail["/v1/locations"] = [code]
    with pytest.raises(StopSync, match=str(code)):
        go(conn, make, "test")
    last = conn.execute("SELECT value FROM t14_state WHERE key = 'last_test'").fetchone()[0]
    assert last["status"] == "stopped" and str(code) in last["error"]


def test_usage_cap_stops_before_sending(t14):
    conn, fake, make, _ = t14
    conn.execute("INSERT INTO t14_requests (method, path, status) SELECT 'GET', '/x', 200 FROM generate_series(1, %s)",
                 (t14client.HOURLY_CAP,))
    with pytest.raises(StopSync, match="hourly limit"):
        go(conn, make, "test")
    assert not [c for c in fake.calls if c[1] != "/v1/token"]


def test_daily_and_changes(t14):
    conn, fake, make, _ = t14
    go(conn, make, "setup")
    go(conn, make, "daily")
    assert conn.execute("SELECT count(*) FROM t14_items WHERE item_id = '888'").fetchone()[0] == 0   # not our brand
    assert conn.execute("SELECT product_id IS NOT NULL FROM t14_items WHERE item_id = '400'").fetchone()[0]
    go(conn, make, "changes")
    assert conn.execute("SELECT total FROM t14_inventory WHERE item_id = '300'").fetchone()[0] == 4
    assert conn.execute("SELECT count(*) FROM t14_inventory WHERE item_id = '555555'").fetchone()[0] == 0


def test_items_no_longer_listed_are_inactive(t14):
    conn, fake, make, _ = t14
    go(conn, make, "setup")
    fake.items_pages = [[item("100G", "LK-100")], []]
    go(conn, make, "full-items")
    assert conn.execute("SELECT active FROM t14_items WHERE item_id = '300'").fetchone()[0] is False


def test_stock_shows_in_the_shop_and_admin(t14, tmp_path, monkeypatch):
    conn, fake, make, _ = t14
    go(conn, make, "setup")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PHOTO_DIR", str(tmp_path / "photos"))
    monkeypatch.setenv("SECRET_KEY", "s")
    monkeypatch.setenv("ADMIN_PASSWORD", "pw")
    monkeypatch.delenv("SITE_PASSWORD", raising=False)
    from fastapi.testclient import TestClient
    from store.app import create_app
    with TestClient(create_app(conn.info.dsn)) as c:
        slug = lambda pn: next(it["slug"] for it in c.get("/api/search", params={"q": pn}).json()["items"] if it["part_number"] == pn)  # noqa: E731
        page = c.get("/p/" + slug("LK-100")).text
        assert "In stock" in page and "PA warehouse" in page and "TX warehouse" in page
        assert "Ships from the manufacturer" in c.get("/p/" + slug("LV-201")).text
        assert "Not listed" in c.get("/p/" + slug("NOFIT-1")).text
        assert "In stock" in c.get("/c/suspension").text
        c.post("/admin/login", data={"password": "pw"})
        admin = c.get("/admin/turn14").text
        assert "Testing (apitest.turn14.com)" in admin and "Test Brand Inc" in admin and "First full load" in admin
        csv = c.get("/admin/turn14/unmatched/testbrand.csv").text
        assert "NOT-OURS" in csv and "TIRE-1" in csv


def test_schedule(t14):
    import datetime as dt
    from store import turn14_schedule as sch
    conn, fake, make, _ = t14
    et = sch.ET
    assert sch.due(conn) is None                      # nothing until the first full load is done
    go(conn, make, "setup")
    at = lambda h, m, day=3: dt.datetime(2026, 11, day, h, m, tzinfo=et).astimezone(dt.timezone.utc)   # noqa: E731
    conn.execute("UPDATE t14_state SET value = jsonb_set(value, '{finished}', to_jsonb(%s::text)) WHERE key = 'last_setup'",
                 (at(0, 10).isoformat(),))
    assert sch.due(conn, at(0, 50)) is None           # less than an hour since the last run
    assert sch.due(conn, at(1, 20)) == "changes"      # hourly stock changes
    assert sch.due(conn, at(1, 35)) == "daily"        # after 1:30 AM Eastern


def test_sync_one_brand_now(t14):
    conn, fake, make, slept = t14
    conn.execute("INSERT INTO brands (id, name) VALUES ('nobody', 'Nobody Carries This')")
    stats = go(conn, make, "brand:testbrand,nobody")
    assert stats["brands_synced"] == 1 and stats["brands_not_at_turn14"] == 1
    assert conn.execute("SELECT task, status FROM t14_runs ORDER BY id DESC LIMIT 1").fetchone() == ("brand:testbrand,nobody", "ok")
    assert conn.execute("SELECT count(*) FROM t14_items WHERE product_id IS NOT NULL").fetchone()[0] == 3
    assert conn.execute("SELECT total FROM t14_inventory WHERE item_id = '100G'").fetchone()[0] == 3
    assert not any(c[1] == "/v1/items/brand/2" for c in fake.calls)    # only the brands asked for


def test_sema_load_syncs_turn14_right_after(conn, monkeypatch):
    from semadata import sync as ss
    from turn14 import client as t14c
    import turn14.sync as t14s
    ran, logs = [], []
    monkeypatch.setattr(t14c, "configured", lambda: True)
    monkeypatch.setattr(t14s, "run", lambda c, task, log=print: ran.append(task) or {})
    ss.turn14_after_load(conn, ["kn", "bilstein"], logs.append)
    assert ran == ["brand:kn,bilstein"]

    def boom(c, task, log=print):
        raise t14c.StopSync("Turn 14 answered 500")
    monkeypatch.setattr(t14s, "run", boom)
    ss.turn14_after_load(conn, ["kn"], logs.append)                    # logged, never fails the SEMA load
    assert "didn't finish" in logs[-1]
    monkeypatch.setattr(t14c, "configured", lambda: False)
    ran.clear()
    ss.turn14_after_load(conn, ["kn"], logs.append)
    assert ran == []
