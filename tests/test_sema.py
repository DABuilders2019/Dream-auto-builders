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
        if path == "/products/bulk":
            pns = request.url.params.get_list("partnumbers")
            assert len(pns) <= 100
            return httpx.Response(200, json={"Products": [self.parts[p] for p in pns if p in self.parts], "Success": True})
        if path == "/lookup/products":
            return httpx.Response(200, json={"success": True, "Products": [
                {"PartNumber": "985-24-001", "FitmentCount": 2}, {"PartNumber": "985-24-002", "FitmentCount": 1},
                {"PartNumber": "UNI-1", "FitmentCount": 0}] + [{"PartNumber": p, "FitmentCount": 1} for p in self.parts if p.endswith("003")]})
        if path == "/lookup/vehiclesbyproduct":
            pn = form["partNumber"]
            vehicles = {"985-24-001": [{"Year": 2021, "MakeName": "Ford", "ModelName": "F-150", "SubmodelName": "XLT"},
                                       {"Year": 2022, "MakeName": "Ford", "ModelName": "F-150", "SubmodelName": "Lariat", "FitmentNotes": "4WD"}],
                        "985-24-002": [{"Year": 2021, "MakeName": "Ford", "ModelName": "F-150", "SubmodelName": "XLT"}],
                        "985-24-003": [{"Year": 2023, "MakeName": "Ram", "ModelName": "2500", "SubmodelName": "Big Horn"}]}[pn]
            return httpx.Response(200, json={"success": True, "Parts": [{"PartNumber": pn, "Vehicles": vehicles}]})
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
    looked_up = [f["partNumber"] for m, p, q, f in fake.calls[n:] if p == "/lookup/vehiclesbyproduct"]
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
    fake.fail["/export/branddatasets"] = [500]
    with pytest.raises(sc.StopSync, match="500"):
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
        # editing the list rematches
        monkeypatch.setattr(ss, "wishlist", lambda c=None: ["RealTruck"])
        tc.post("/admin/sema/list", data={"names": "RealTruck"})
        assert {r[0] for r in conn.execute("SELECT aaia FROM sema_brands WHERE wanted").fetchall()} == {"RLTK"}
