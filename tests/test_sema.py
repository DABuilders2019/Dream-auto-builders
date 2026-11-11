"""SEMA Data connection against a fake SEMA server: sign-in, brand list matching, loading a brand
(products, fitment, logo link) through the normal loader, fitment caching, the allowance guard,
429 handling, and that the login never lands in the request log."""
import json
import urllib.parse

import httpx
import pytest

from semadata import client as sc
from semadata import sync as ss


def product(pn, name, price, fit=True):
    return {"PartNumber": pn, "BrandAAIAID": "TSTB",
            "ItemSegments": {"PartTerminologyID": 7564, "BrandLabel": "Test Shocks"},
            "Descriptions": [{"Code": "EXT", "Values": [name], "Language": "EN"}, {"Code": "DEF", "Values": ["Shock Absorber"]}],
            "Prices": [{"Code": "RET", "Value": price, "Currency": "USD", "UoM": "PE"}, {"Code": "RMP", "Value": price - 5}],
            "Attributes": [{"Code": "Series", "Value": "2.0 Performance", "IsPADBAttribute": False}],
            "DigitalAssets": [{"Code": "P04", "Value": f"https://img.example.com/{pn}.jpg", "FileName": f"{pn}.jpg", "FileType": "JPG"},
                              {"Code": "LGO", "Value": "https://img.example.com/logo.png", "FileName": "logo.png", "FileType": "PNG"}],
            "Packaging": {"Weight": "4.5", "WeightUOM": "PG", "Length": "20", "Width": "4", "Height": "4", "DIMUOM": "IN"}}


class FakeSema:
    def __init__(self):
        self.calls = []
        self.fail = {}
        self.api_count, self.api_max = 100, 100000
        self.batch_ok = False
        self.limit = 10
        self.max_query = 100000
        self.broken = set()
        self.parts = {"985-24-001": product("985-24-001", "Front Shock 0-2 in. Lift", 129.95),
                      "985-24-002": product("985-24-002", "Rear Shock 0-1 in. Lift", 119.95),
                      "UNI-1": product("UNI-1", "Shock Boot", 19.95)}

    def __call__(self, request):
        path = request.url.path.replace("/sdapi/v2", "")
        q = dict(request.url.params)
        form = dict(urllib.parse.parse_qsl(request.content.decode())) if request.method == "POST" else {}
        self.calls.append((request.method, path, q, form))
        if self.fail.get(path):
            code = self.fail[path].pop(0)
            return httpx.Response(code, headers={"Retry-After": "1"}, json={"Message": "slow down"})
        if path == "/token/get":
            if q.get("userName") == "me" and q.get("password") == "pw":
                return httpx.Response(200, json={"success": True, "message": "", "token": "tok1"})
            return httpx.Response(200, json={"success": False, "message": "Invalid credentials", "token": None})
        tok = q.get("token") or form.get("token")
        assert tok == "tok1"
        if path == "/receiver":
            return httpx.Response(200, json={"HasAPIAccess": True, "APICount": self.api_count, "APIMaxCount": self.api_max,
                                             "HasAPIUnitsLeft": True, "ExportCount": 3, "ExportMaxCount": 1000,
                                             "MembershipExpirationDate": "2027-04-01T00:00:00", "Success": True})
        self.api_count += 1
        if path == "/export/branddatasets":
            return httpx.Response(200, json={"success": True, "BrandDatasets": [
                {"AAIABrandId": "TSTB", "BrandName": "Test Shocks", "DatasetId": 1, "LastUpdated": "2026-11-01"},
                {"AAIABrandId": "BKQW", "BrandName": "BAK", "DatasetId": 2, "LastUpdated": "2026-10-01"},
                {"AAIABrandId": "RLTK", "BrandName": "RealTruck", "DatasetId": 3, "LastUpdated": "2026-10-01"}]})
        if path == "/products" and q.get("brandaaiaid") == "TSTB":
            return httpx.Response(200, json={"PartNumbers": list(self.parts), "Success": True})
        if path == "/products/bulk" and len(request.url.query) > self.max_query:
            return httpx.Response(404, text="<html><head><title>IIS 10.0 Detailed Error - 404.15 - Not Found</title></head></html>")
        if path == "/products/bulk":
            pns = request.url.params.get_list("partnumbers")
            assert len(pns) <= 100
            return httpx.Response(200, json={"Products": [self.parts[p] for p in pns if p in self.parts], "Success": True})
        if path == "/lookup/products":
            return httpx.Response(200, json={"success": True, "Products": [
                {"PartNumber": "985-24-001", "FitmentCount": 2}, {"PartNumber": "985-24-002", "FitmentCount": 1},
                {"PartNumber": "UNI-1", "FitmentCount": 0}] + [{"PartNumber": p, "FitmentCount": 1} for p in self.parts
                                                                if p.endswith("003") or p.startswith("BIG-")]})
        if path == "/lookup/vehiclesbyproduct":
            multi = urllib.parse.parse_qs(request.content.decode())
            pns = multi.get("partNumber") or multi.get("partNumbers") or []
            if any(p in self.broken for p in pns):          # SEMA's server fails on this part every time
                return httpx.Response(500, json={"Success": False, "Message": "An unexpected error occurred. Log Id: 1"})
            if not self.batch_ok or "partNumber" not in multi:
                pns = pns[:1]
            if len(pns) > self.limit:
                return httpx.Response(200, json={"success": False, "message": f"Part Numbers parameter is limited to {self.limit}."})
            table = {"985-24-001": [{"Year": 2021, "MakeName": "Ford", "ModelName": "F-150", "SubmodelName": "XLT"},
                                       {"Year": 2022, "MakeName": "Ford", "ModelName": "F-150", "SubmodelName": "Lariat", "FitmentNotes": "4WD"}],
                        "985-24-002": [{"Year": 2021, "MakeName": "Ford", "ModelName": "F-150", "SubmodelName": "XLT"}],
                        "985-24-003": [{"Year": 2023, "MakeName": "Ram", "ModelName": "2500", "SubmodelName": "Big Horn"}]}
            table.update({p: [{"Year": 2024, "MakeName": "Jeep", "ModelName": "Gladiator"}] for p in self.parts if p.startswith("BIG-")})
            return httpx.Response(200, json={"success": True, "Parts": [{"PartNumber": pn, "Vehicles": table.get(pn, [])} for pn in pns]})
        return httpx.Response(404, text="no route")


@pytest.fixture
def sema(conn, monkeypatch, tmp_path):
    conn.autocommit = True
    monkeypatch.setenv("SEMA_USERNAME", " me ")
    monkeypatch.setenv("SEMA_PASSWORD", "pw")
    monkeypatch.setattr(ss, "wishlist", lambda c=None: ["Test Shocks", "Real Truck Bak", "Nobody Brand"])
    fake = FakeSema()
    c = sc.Client(conn, task="t", transport=httpx.MockTransport(fake), sleep=lambda s: None, log=lambda *a: None)
    return fake, c, tmp_path


def test_connection_test_matches_our_list(conn, sema):
    fake, c, _ = sema
    stats = ss.run(conn, "test", log=lambda *a: None, client=c)
    assert stats["approved_brands"] == 3 and stats["on_our_list"] == 1
    matches = conn.execute("SELECT value FROM sema_state WHERE key = 'matches'").fetchone()[0]
    assert matches["Test Shocks"]["aaia"] == "TSTB"
    assert matches["Real Truck Bak"]["aaia"] is None             # ambiguous: BAK or RealTruck -> asks
    assert {a for a, n in matches["Real Truck Bak"]["choices"][:2]} == {"BKQW", "RLTK"}
    assert conn.execute("SELECT wanted FROM sema_brands WHERE aaia = 'TSTB'").fetchone()[0] is True
    receiver = conn.execute("SELECT value FROM sema_state WHERE key = 'receiver'").fetchone()[0]
    assert receiver["APIMaxCount"] == 100000
    # the login never lands in our request log
    logged = [r[0] for r in conn.execute("SELECT path FROM sema_requests").fetchall()]
    assert "/token/get" in logged and not any("pw" in p or "userName" in p for p in logged)


def test_bad_login_stops(conn, sema, monkeypatch):
    _, c, _ = sema
    monkeypatch.setenv("SEMA_PASSWORD", "wrong")
    with pytest.raises(sc.StopSync, match="refused the sign-in"):
        ss.run(conn, "test", log=lambda *a: None, client=c)


def test_load_brand_through_normal_loader(conn, sema):
    fake, c, tmp = sema
    ss.run(conn, "test", log=lambda *a: None, client=c)
    stats = ss.run(conn, "load", log=lambda *a: None, client=c, data_dir=tmp, photos=False)
    assert stats["loaded"] == 1 and stats["failed"] == 0
    rows = dict(conn.execute("SELECT part_number, price FROM products WHERE brand_id = 'testshocks'").fetchall())
    assert {k: float(v) for k, v in rows.items()} == {"985-24-001": 129.95, "985-24-002": 119.95, "UNI-1": 19.95}
    fit = conn.execute("""SELECT p.part_number, v.year, v.make, v.model, f.submodel FROM fitment f JOIN products p ON p.id = f.product_id
                          JOIN vehicles v ON v.id = f.vehicle_id ORDER BY 1, 2""").fetchall()
    assert fit[0] == ("985-24-001", 2021, "Ford", "F-150", "XLT") and len(fit) == 3
    assert conn.execute("SELECT logo_url FROM brands WHERE id = 'testshocks'").fetchone()[0] == "https://img.example.com/logo.png"
    photos = conn.execute("SELECT count(*) FROM product_images i JOIN products p ON p.id = i.product_id WHERE p.brand_id = 'testshocks'").fetchone()[0]
    assert photos == 3
    b = conn.execute("SELECT status, brand_id, parts, fitted_parts, loaded_updated FROM sema_brands WHERE aaia = 'TSTB'").fetchone()
    assert b == ("loaded", "testshocks", 3, 2, "2026-11-01")
    # parts with no fitment (FitmentCount 0) were never looked up
    assert not any(f.get("partNumber") == "UNI-1" for m, p, q, f in fake.calls)

    # Up to date: nothing to load, no product calls
    n = len(fake.calls)
    stats = ss.run(conn, "load", log=lambda *a: None, client=c, data_dir=tmp, photos=False)
    assert stats["brands"] == 0
    assert not [x for x in fake.calls[n:] if x[1].startswith("/products")]

    # SEMA updates the brand with a new part: only the new part's fitment is looked up again
    fake.parts["985-24-003"] = product("985-24-003", "Steering Stabilizer", 99.95)
    conn.execute("UPDATE sema_brands SET loaded_updated = 'old'")
    n = len(fake.calls)
    ss.run(conn, "load", log=lambda *a: None, client=c, data_dir=tmp, photos=False)
    looked_up = [f.get("partNumber") for m, p, q, f in fake.calls[n:] if p == "/lookup/vehiclesbyproduct"]
    assert looked_up == ["985-24-003"]
    assert conn.execute("SELECT count(*) FROM products WHERE brand_id = 'testshocks'").fetchone()[0] == 4
    assert conn.execute("SELECT count(*) FROM fitment").fetchone()[0] == 4


def test_allowance_guard_stops_before_loading(conn, sema):
    fake, c, tmp = sema
    ss.run(conn, "test", log=lambda *a: None, client=c)
    fake.api_count = fake.api_max - 2000          # under the 5,000 reserve
    with pytest.raises(sc.StopSync, match="allowance"):
        ss.run(conn, "load", log=lambda *a: None, client=c, data_dir=tmp, photos=False)
    assert conn.execute("SELECT count(*) FROM products").fetchone()[0] == 0


def test_429_waits_and_retries_once_then_stops(conn, sema):
    fake, c, _ = sema
    fake.fail["/export/branddatasets"] = [429]
    ss.run(conn, "test", log=lambda *a: None, client=c)
    fake.fail["/export/branddatasets"] = [429, 429]
    with pytest.raises(sc.StopSync):
        ss.run(conn, "test", log=lambda *a: None, client=c)
    fake.fail["/export/branddatasets"] = [500]      # SEMA's own server failed: reported, not retried here
    with pytest.raises(sc.ServerError, match="500"):
        ss.run(conn, "test", log=lambda *a: None, client=c)


def test_product_xml_parses_like_a_pies_file(tmp_path):
    from importer import pies
    p = tmp_path / "x.xml"
    p.write_text('<?xml version="1.0"?><PIES xmlns="http://www.aftermarket.org"><Header><SubmissionType>FULL</SubmissionType></Header><Items>'
                 + ss.product_xml(product("A&B-1", "Shock <2in>", 10.5), "TSTB") + "</Items></PIES>")
    item = next(pies.read_items(str(p)))
    assert item["part_number"] == "A&B-1" and item["descriptions"]["EXT"] == ["Shock <2in>"]
    assert item["prices"][0] == {"type": "RET", "amount": 10.5, "currency": "USD", "uom": "PE"}
    assert item["attributes"]["Series"] == "2.0 Performance"
    assert item["assets"][0]["uri"] == "https://img.example.com/A&B-1.jpg" and item["assets"][0]["asset_type"] == "P04"
    assert item["package"]["weight_lb"] == 4.5 and item["part_terminology_id"] == 7564


def test_matching_rules():
    names = ["FOX", "FOX Factory Bike", "BAK", "RealTruck", "Cali Raised LED", "CARR", "Air Lift", "Champion"]
    assert ss.candidates("Fox off-road shocks", names) == (["FOX"], True)
    assert ss.candidates("Airlift", names) == (["Air Lift"], True)
    assert ss.candidates("Carr", names) == (["CARR"], True)
    assert ss.candidates("Cali Offroad", names)[1] is False
    assert ss.candidates("Real Truck Bak", names)[1] is False


def test_admin_sema_page(conn, sema, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    fake, c, _ = sema
    ss.run(conn, "test", log=lambda *a: None, client=c)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin-pw")
    monkeypatch.delenv("SITE_PASSWORD", raising=False)
    from store.app import create_app
    app = create_app(conn.info.dsn)
    with TestClient(app) as tc:
        tc.post("/admin/login", data={"password": "admin-pw"})
        page = tc.get("/admin/sema").text
        assert "SEMA Data connection" in page and "Set in Render" in page and "Test Shocks" in page
        assert "100,000" in page and "Which one?" in page          # plan usage, and the ambiguous name asks
        assert "pw" not in page.replace("admin-pw", "")
        # pick BAK for "Real Truck Bak", skip "Nobody Brand"
        tc.post("/admin/sema/pick", data={"wish": "Real Truck Bak", "aaia": "BKQW"})
        tc.post("/admin/sema/pick", data={"wish": "Nobody Brand", "aaia": "-"})
        wanted = {r[0] for r in conn.execute("SELECT aaia FROM sema_brands WHERE wanted").fetchall()}
        assert wanted == {"TSTB", "BKQW"}
        page = tc.get("/admin/sema").text
        assert "Skipped" in page and "picked" in page
        # tick several brands and load just those
        assert "Load selected" in page and 'name="pick" value="TSTB"' in page
        from store import sema_schedule
        got = []
        monkeypatch.setattr(sema_schedule, "start", lambda st, codes, title="": got.append((list(codes), title)) or True)
        r = tc.post("/admin/sema/run/load", data={"pick": ["tstb", "BKQW", "TSTB", "bad code!"]})
        assert got == [(["TSTB", "BKQW"], "SEMA: load 2 selected brands")] and r.status_code == 200
        got.clear()
        tc.post("/admin/sema/run/load", data={"pick": ["!!"]})
        assert got == []                                    # nothing usable ticked: no job
        # editing the list rematches
        monkeypatch.setattr(ss, "wishlist", lambda c=None: ["RealTruck"])
        tc.post("/admin/sema/list", data={"names": "RealTruck"})
        assert {r[0] for r in conn.execute("SELECT aaia FROM sema_brands WHERE wanted").fetchall()} == {"RLTK"}


def test_fitment_in_batches_when_sema_allows(conn, sema):
    fake, c, tmp = sema
    fake.batch_ok = True
    ss.run(conn, "test", log=lambda *a: None, client=c)
    ss.run(conn, "load", log=lambda *a: None, client=c, data_dir=tmp, photos=False)
    fit_calls = [x for x in fake.calls if x[1] == "/lookup/vehiclesbyproduct"]
    assert len(fit_calls) == 2                       # one probe (2 parts), then one batch call
    assert conn.execute("SELECT value FROM sema_state WHERE key = 'fit_mode_v2'").fetchone()[0] == "repeat"
    assert conn.execute("SELECT count(*) FROM fitment").fetchone()[0] == 3


def test_fitment_answer_shapes():
    a = {"success": True, "Parts": [{"PartNumber": "X1", "Vehicles": [{"Year": 2020, "MakeName": "Jeep", "ModelName": "Wrangler"}]}]}
    b = {"Success": True, "parts": [{"partNumber": "X1", "vehicles": [{"year": "2020", "make": "Jeep", "model": "Wrangler", "subModel": "Rubicon"}]}]}
    c = {"success": True, "Vehicles": [{"YearFrom": 2019, "YearTo": 2021, "Make": "Ram", "Model": "1500", "Notes": "4WD"}]}
    assert ss.fitment_parts(a) == [("X1", [{"year": 2020, "make": "Jeep", "model": "Wrangler", "submodel": None, "notes": None, "engine": None}])]
    assert ss.fitment_parts(b)[0][1][0]["submodel"] == "Rubicon"
    rows = ss.fitment_parts(c)[0][1]
    assert [r["year"] for r in rows] == [2019, 2020, 2021] and rows[0]["notes"] == "4WD"


def test_fitment_batch_size_follows_semas_limit(conn, sema, monkeypatch):
    fake, c, tmp = sema
    fake.batch_ok, fake.limit = True, 10
    for i in range(23):
        fake.parts[f"BIG-{i}"] = product(f"BIG-{i}", f"Part {i}", 10.0)
    monkeypatch.setattr(ss, "FIT_BATCH", 50)            # start too big: SEMA's error message sets it to 10
    ss.run(conn, "test", log=lambda *a: None, client=c)
    ss.run(conn, "load", log=lambda *a: None, client=c, data_dir=tmp, photos=False)
    assert conn.execute("SELECT count(*) FROM fitment").fetchone()[0] == 3 + 23
    assert conn.execute("SELECT fitted_parts FROM sema_brands WHERE aaia = 'TSTB'").fetchone()[0] == 25


def test_long_part_numbers_are_sent_in_smaller_requests(conn, sema, monkeypatch):
    fake, c, tmp = sema
    for i in range(40):
        pn = f"VERY-LONG-PART-NUMBER-{i:03d}-ABCDEFGHIJKLMNOP"
        fake.parts[pn] = product(pn, f"Gauge {i}", 50.0)
    fake.max_query = 900                              # SEMA's server refuses longer addresses
    monkeypatch.setattr(ss, "MAX_QUERY", 5000)        # start too long: the 404.15 answer splits the request
    ss.run(conn, "test", log=lambda *a: None, client=c)
    stats = ss.run(conn, "load", log=lambda *a: None, client=c, data_dir=tmp, photos=False)
    assert stats["loaded"] == 1
    assert conn.execute("SELECT count(*) FROM products WHERE brand_id = 'testshocks'").fetchone()[0] == 43
    assert ss.url_batches(["A" * 50] * 10, 100, 200) == [["A" * 50] * 3] * 3 + [["A" * 50]]


def test_logo_upload_on_catalog_page(conn, sema, tmp_path, monkeypatch):
    import io
    from fastapi.testclient import TestClient
    from PIL import Image
    fake, c, tmp = sema
    ss.run(conn, "test", log=lambda *a: None, client=c)
    ss.run(conn, "load", log=lambda *a: None, client=c, data_dir=tmp, photos=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PHOTO_DIR", str(tmp_path / "data" / "photos"))
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin-pw")
    monkeypatch.delenv("SITE_PASSWORD", raising=False)
    from store.app import create_app
    img = Image.new("RGB", (300, 100), "white")
    img.paste((200, 0, 0), (20, 20, 280, 80))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    with TestClient(create_app(conn.info.dsn)) as tc:
        tc.post("/admin/login", data={"password": "admin-pw"})
        assert "Add logo" in tc.get("/admin").text
        r = tc.post("/admin/logo/testshocks", files={"file": ("bbk.png", buf.getvalue(), "image/png")})
        assert "logo saved" in r.text
        key, src = conn.execute("SELECT logo_key, logo_src FROM brands WHERE id = 'testshocks'").fetchone()
        assert key.startswith("logos/testshocks-") and src == "upload"
        r = tc.post("/admin/logo/testshocks", files={"file": ("x.png", b"not an image", "image/png")})
        assert "logo not saved" in r.text
    # a later photo run keeps the uploaded logo
    from importer import photos as ph
    assert ph.fetch_logo(conn, "testshocks", log=lambda *a: None) == key


def test_logo_rank_prefers_png():
    from importer.load import logo_rank_of
    assert logo_rank_of({"uri": "https://x/logo.eps", "file_type": "EPS"}) == 5
    assert logo_rank_of({"uri": "https://x/logo.png", "file_type": ""}) == 0
    assert logo_rank_of({"uri": "https://x/logo.jpg", "file_type": "JPG"}) == 1


def test_jobber_only_brand_priced_with_markup(conn, sema, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    fake, c, tmp = sema
    for p in fake.parts.values():                      # this brand only shares jobber prices (and a MAP on one part)
        p["Prices"] = [{"Code": "JBR", "Value": 10.0}]
    fake.parts["985-24-001"]["Prices"].append({"Code": "RMP", "Value": 15.0})
    ss.run(conn, "test", log=lambda *a: None, client=c)
    ss.run(conn, "load", log=lambda *a: None, client=c, data_dir=tmp, photos=False)
    assert conn.execute("SELECT count(*) FROM products WHERE brand_id = 'testshocks' AND status = 'hidden'").fetchone()[0] == 2   # the one with a MAP shows
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin-pw")
    monkeypatch.delenv("SITE_PASSWORD", raising=False)
    from store.app import create_app
    with TestClient(create_app(conn.info.dsn)) as tc:
        tc.post("/admin/login", data={"password": "admin-pw"})
        page = tc.get("/admin").text
        assert "Jobber</b> (3)" in page and "hidden (no price)" in page
        started = []
        tc.app.state.shop.jobs.start = lambda title, steps, brand_id=None: started.append(steps) or True
        r = tc.post("/admin/pricing/testshocks", data={"price_from": "JBR", "markup": "40"})
        assert "3 of 3 parts now have a price" in r.text
        pend = conn.execute("""SELECT count(DISTINCT i.product_id) FROM product_images i JOIN products p ON p.id = i.product_id
                               WHERE p.brand_id = 'testshocks' AND i.status = 'pending'""").fetchone()[0]
        assert pend      # parts that just appeared get their photos downloaded right away
        assert "Downloading photos" in r.text and started and started[0][0][1][:3] == ["photos", "--brand", "testshocks"]
    prices = dict(conn.execute("SELECT part_number, price FROM products WHERE brand_id = 'testshocks'").fetchall())
    assert float(prices["985-24-002"]) == 14.0 and float(prices["985-24-001"]) == 15.0     # never below MAP
    # a reload keeps the rule
    conn.execute("UPDATE sema_brands SET loaded_updated = 'old'")
    ss.run(conn, "load", log=lambda *a: None, client=c, data_dir=tmp, photos=False)
    assert float(conn.execute("SELECT price FROM products WHERE part_number = '985-24-002'").fetchone()[0]) == 14.0


def test_new_category_rules_apply_to_loaded_products(conn, sema):
    from importer import load as loader
    fake, c, tmp = sema
    fake.parts = {"TC-1": product("TC-1", "American Roll Cover", 699.0)}
    fake.parts["TC-1"]["Descriptions"][1]["Values"] = ["Tonneau Cover"]
    ss.run(conn, "test", log=lambda *a: None, client=c)
    ss.run(conn, "load", log=lambda *a: None, client=c, data_dir=tmp, photos=False)
    assert conn.execute("SELECT category_id FROM products WHERE part_number = 'TC-1'").fetchone()[0] == "tonneau"
    # pretend it was loaded under old rules; a rules change re-sorts it at startup
    conn.execute("UPDATE products SET category_id = 'uncategorized'")
    conn.execute("DELETE FROM app_state")
    assert loader.recategorize(conn, log=lambda *a: None) == 1
    assert conn.execute("SELECT category_id FROM products WHERE part_number = 'TC-1'").fetchone()[0] == "tonneau"
    assert loader.recategorize(conn, log=lambda *a: None) == 0          # same rules: nothing to do


def test_new_config_brands_join_the_saved_list_once(conn, monkeypatch):
    from psycopg.types.json import Jsonb
    conn.autocommit = True
    monkeypatch.setattr(ss, "_config_names", lambda: ["Air Lift", "Borla", "Edelbrock"])
    conn.execute("INSERT INTO sema_state (key, value) VALUES ('wishlist', %s)", (Jsonb(["Air Lift", "My Own Brand"]),))
    conn.execute("INSERT INTO sema_state (key, value) VALUES ('config_names_seen', %s)", (Jsonb(["Air Lift", "Borla"]),))
    assert ss.wishlist(conn) == ["Air Lift", "My Own Brand", "Edelbrock"]   # Borla was removed on the admin page: stays out
    assert ss.wishlist(conn) == ["Air Lift", "My Own Brand", "Edelbrock"]   # added once


def test_vehicle_groups_in_model_menu(conn):
    from store import vehicles as veh
    conn.autocommit = True
    from importer import load as loader
    loader.seed_reference(conn)
    conn.execute("INSERT INTO brands (id, name) VALUES ('vb', 'VB') ON CONFLICT DO NOTHING")
    pid = conn.execute("""INSERT INTO products (brand_id, part_number, slug, title, short_name, status, price, category_id)
                          VALUES ('vb', 'X1', 'vb-x1', 'VB X1', 'X1', 'active', 10, 'uncategorized') RETURNING id""").fetchone()[0]
    names = ["Ridgeline", "Accord", "SXS10S2R Talon 1000R", "TRX420FA5 FourTrax Rancher 4x4", "CBR1000RR"]
    for n in names:
        vid = conn.execute("INSERT INTO vehicles (year, make, model) VALUES (2022, 'Honda', %s) RETURNING id", (n,)).fetchone()[0]
        conn.execute("INSERT INTO fitment (product_id, vehicle_id) VALUES (%s, %s)", (pid, vid))
    models = veh.Picker(conn).models(2022, "Honda")
    assert [(m["group"], m["model"]) for m in models] == [
        ("car", "Accord"), ("car", "Ridgeline"),
        ("powersports", "FourTrax Rancher 4x4"), ("powersports", "Talon 1000R"),
        ("motorcycle", "CBR1000RR")]
    assert models[2]["group_label"] == "Powersports (UTV / ATV)"
    assert veh.get(conn, models[3]["id"])["label"] == "2022 Honda Talon 1000R"


def test_job_connection_reconnects_after_drop():
    import os
    import psycopg
    import pytest
    from semadata.conn import ReconnectingConnection
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set")
    rc = ReconnectingConnection(url, connect=lambda: psycopg.connect(url, autocommit=True))
    rc.raw.close()                                   # the database drops us mid-job
    assert rc.execute("SELECT 1").fetchone()[0] == 1 and rc.reconnects == 1
    with pytest.raises(psycopg.errors.UndefinedTable):    # a real SQL error is not retried
        rc.execute("SELECT * FROM no_such_table")
    assert rc.reconnects == 1
    with rc.cursor() as cur:                         # everything else goes to the live connection
        cur.execute("SELECT 2")
        assert cur.fetchone()[0] == 2


def test_load_redoes_brand_when_database_drops_mid_import(conn, sema, monkeypatch):
    """The database drops the connection during the big import: the job reconnects, imports the same files
    again (no extra SEMA calls) and marks the brand loaded."""
    import os
    import psycopg
    from importer import load as loader
    from semadata.conn import ReconnectingConnection
    fake, _, tmp = sema
    url = conn.info.dsn
    rc = ReconnectingConnection(url, connect=lambda: psycopg.connect(url, autocommit=True))
    c = sc.Client(rc, task="t", transport=httpx.MockTransport(fake), sleep=lambda s: None, log=lambda *a: None)
    ss.run(rc, "test", log=lambda *a: None, client=c)
    real, calls = loader.load_brand, []

    def flaky(cn, *a, **k):
        calls.append(1)
        if len(calls) == 1:
            cn.raw.close()                       # the drop
            raise psycopg.OperationalError("consuming input failed: SSL error: unexpected eof while reading")
        return real(cn, *a, **k)
    monkeypatch.setattr(loader, "load_brand", flaky)
    lines = []
    stats = ss.run(rc, "load", log=lines.append, client=c, data_dir=tmp, photos=False)
    assert stats["loaded"] == 1 and stats["failed"] == 0 and len(calls) == 2 and rc.reconnects == 1
    assert any("reconnected, importing again" in m for m in lines)
    assert rc.execute("SELECT status FROM sema_brands WHERE aaia = 'TSTB'").fetchone()[0] == "loaded"
    assert rc.execute("SELECT count(*) FROM products WHERE brand_id = 'testshocks'").fetchone()[0] == 3


def test_sema_server_error_on_fitment_is_retried(conn, sema):
    """SEMA answered 500 once: asked again, and the brand loads with all its fitment."""
    fake, c, tmp = sema
    ss.run(conn, "test", log=lambda *a: None, client=c)
    c.set_state(ss.FIT_MODE_KEY, "single")          # no probe call first: the 500 lands on a real lookup
    fake.fail["/lookup/vehiclesbyproduct"] = [500]
    logs = []
    ss.run(conn, "load", log=logs.append, client=c, data_dir=tmp, photos=False)
    assert conn.execute("SELECT status FROM sema_brands WHERE aaia = 'TSTB'").fetchone()[0] == "loaded"
    assert conn.execute("SELECT count(*) FROM fitment").fetchone()[0] == 3
    assert any("trying again" in m for m in logs)


def test_part_sema_keeps_failing_on_is_skipped(conn, sema):
    """SEMA's server fails on one part every time: that part loads without fitment, the rest of the brand loads,
    and the part is asked about again on the next load."""
    fake, c, tmp = sema
    fake.broken = {"985-24-002"}
    ss.run(conn, "test", log=lambda *a: None, client=c)
    logs = []
    ss.run(conn, "load", log=logs.append, client=c, data_dir=tmp, photos=False)
    assert conn.execute("SELECT status FROM sema_brands WHERE aaia = 'TSTB'").fetchone()[0] == "loaded"
    assert conn.execute("SELECT count(*) FROM products WHERE brand_id IS NOT NULL").fetchone()[0] >= 3
    assert conn.execute("""SELECT count(*) FROM fitment f JOIN products p ON p.id = f.product_id
                           WHERE p.part_number = '985-24-001'""").fetchone()[0] == 2
    assert any("failed on 1 part" in m and "985-24-002" in m for m in logs)
