"""Search tests on a small made-up catalog that covers each search feature."""
import pytest

from conftest import make_pies, make_vehicles
from importer import load
from store.search import Searcher
from store.settings import search_config

ITEMS = [
    {"pn": "LK-100", "name": '4" Lift Kit - Ram 1500', "part_type": "Suspension Lift Kit", "price": 999.95},
    {"pn": "LV-200", "name": '2.5" Front Leveling Kit - Silverado 1500', "part_type": "Suspension Leveling Kit", "price": 249.95},
    {"pn": "LV-201", "name": '1.5" Front Leveling Kit - Tacoma', "part_type": "Suspension Leveling Kit", "price": 199.95},
    {"pn": "15001", "name": "2015-2023 Ford Mustang 2.3L Street Series Cat-Back Performance Exhaust System",
     "part_type": "Exhaust System Kit", "price": 1500.00},
    {"pn": "15002", "name": "2015-2023 Ford Mustang 5.0L Street Series Cat-Back Performance Exhaust System",
     "part_type": "Exhaust System Kit", "price": 1700.00},
    {"pn": "35116", "name": "4in. Round Polished Exhaust Tip", "part_type": "Exhaust Tail Pipe Tip", "price": 73.89},
    {"pn": "11214", "name": "5 x 8in. Oval Straight-Through Performance Exhaust Muffler", "part_type": "Exhaust Muffler", "price": 150.00},
    {"pn": "CAT-1", "name": "California Grade CARB Compliant Catalytic Converter", "part_type": "Catalytic Converter",
     "price": 500.00, "attrs": {"CARB Executive Order Number": "D-193-199"}},
    {"pn": "CAT-2", "name": "Federal Grade Catalytic Converter", "part_type": "Catalytic Converter", "price": 300.00,
     "attrs": {"CARB Executive Order Number": "Not Applicable"}},
    {"pn": "NOFIT-1", "name": "Track Bar Bracket", "part_type": "Suspension Track Bar Bracket", "price": 99.00},
    {"pn": "TIRE-1", "name": "Baja Boss A/T 35X12.50R20LT Tire", "part_type": "Tire", "price": 514.29,
     "attrs": {"Tire Size": "35X12.50R20LT", "Brand Line": "BAJA BOSS A/T"}},
]
FITMENT = [
    *[("Ram", "1500", y, "LK-100", "5.7") for y in (2019, 2020, 2021, 2022)],
    *[("Chevrolet", "Silverado 1500", y, "LV-200", "5.3") for y in (2019, 2020)],
    *[("Chevrolet", "Silverado 1500 LD", 2019, "LV-200", "5.3")],
    *[("Toyota", "Tacoma", y, "LV-201", "3.5") for y in (2016, 2017)],
    *[("Ford", "Mustang", y, "15001", "2.3") for y in (2015, 2018, 2023)],
    *[("Ford", "Mustang", y, "15002", "5.0") for y in (2018, 2023)],
    ("Ford", "F-150", 2018, "CAT-1", "5.0"), ("Ford", "F-150", 2018, "CAT-2", "5.0"),
    ("Dodge", "Ram 1500", 2008, "CAT-2", "5.7"),
]


@pytest.fixture
def shop(conn, tmp_path):
    pies = make_pies(tmp_path / "p.xml", ITEMS)
    veh = make_vehicles(tmp_path / "v.xlsx", FITMENT)
    load.load_brand(conn, "testbrand", pies, None, veh, log=lambda *a: None)
    conn.autocommit = True
    return conn, Searcher(conn, search_config())


def pns(result):
    return [it["part_number"] for it in result["items"]]


def test_part_number_any_format(shop):
    conn, s = shop
    for q in ("LK-100", "lk100", "LK 100", "testbrand lk-100"):
        r = s.search(conn, q)
        assert pns(r)[:1] == ["LK-100"] and r["part_number_match"], q


def test_typo_is_corrected(shop):
    conn, s = shop
    r = s.search(conn, "levling kit")
    assert r["corrections"] == {"levling": "leveling"}
    assert set(pns(r)) == {"LV-200", "LV-201"}


def test_vehicle_in_query_filters_to_parts_that_fit(shop):
    conn, s = shop
    r = s.search(conn, "2019 silverado 1500 leveling kit")
    assert r["vehicle"]["label"] == "2019 Chevrolet Silverado 1500" and r["vehicle"]["exact"]
    assert pns(r) == ["LV-200"]
    r = s.search(conn, "2018 mustang cat-back")
    assert sorted(pns(r)) == ["15001", "15002"]
    assert {tuple(it["fit_detail"]["engines"]) for it in r["items"]} == {("2.3",), ("5.0",)}


def test_vehicle_family_and_nicknames(shop):
    conn, s = shop
    r = s.search(conn, "chevy silverado leveling")
    assert "Silverado" in r["vehicle"]["label"] and pns(r) == ["LV-200"]
    r = s.search(conn, "2021 dodge ram 1500 lift kit")       # Ram became its own make in 2011
    assert pns(r) == ["LK-100"]


def test_synonym_and_hyphen(shop):
    conn, s = shop
    assert set(pns(s.search(conn, "catback"))) == {"15001", "15002"}
    assert "CAT-1" not in pns(s.search(conn, "cat-back"))      # "cat-back" must not match catalytic converters


def test_sizes_rank_but_are_not_required(shop):
    conn, s = shop
    assert pns(s.search(conn, "exhaust tip 4 inch"))[:1] == ["35116"]


def test_tire_size_search(shop):
    conn, s = shop
    for q in ("35x12.50r20", "35X12.5R20LT", "35x12.50R20 tire"):
        assert pns(s.search(conn, q)) == ["TIRE-1"], q


def test_garage_vehicle_filter_and_fit_labels(shop):
    conn, s = shop
    vid = conn.execute("SELECT id FROM vehicles WHERE year=2018 AND make='Ford' AND model='Mustang'").fetchone()[0]
    r = s.search(conn, "", vehicle_id=vid, per_page=50)
    fit = {it["part_number"]: it["fit"] for it in r["items"]}
    assert fit["15001"] == "fits" and fit["11214"] == "universal" and fit["TIRE-1"] == "size"
    assert "NOFIT-1" not in fit and "LK-100" not in fit                      # no fitment data / doesn't fit
    assert r["facets"]["fit"]["unknown"] == 1
    r = s.search(conn, "", vehicle_id=vid, fit_only=False, per_page=50)
    assert {"NOFIT-1", "LK-100"} <= set(pns(r))
    assert pns(r)[:2] in (["15001", "15002"], ["15002", "15001"])          # parts that fit come first


def test_filters_and_facets(shop):
    conn, s = shop
    r = s.search(conn, "", category="exhaust", carb_only=True)
    assert "CAT-2" not in pns(r) and "CAT-1" in pns(r)
    assert r["facets"]["emissions"] == {"emissions_parts": 2, "carb_legal": 1}
    sub = {c["id"]: c["count"] for c in r["facets"]["categories"][0]["subs"]}
    assert sub["catalytic-converters"] == 1 and sub["cat-back"] == 2
    r = s.search(conn, "", category="suspension", price_max=200)
    assert pns(r) == ["NOFIT-1", "LV-201"] or set(pns(r)) == {"NOFIT-1", "LV-201"}
    r = s.search(conn, "", sort="price_desc", per_page=2, page=1)
    assert pns(r) == ["15002", "15001"] and r["total"] == len(ITEMS) and r["pages"] == 6


def test_relaxed_search_when_no_product_has_every_word(shop):
    conn, s = shop
    r = s.search(conn, "leveling kit unicorn")
    assert r["relaxed"] and r["total"] > 0


def test_suggest(shop):
    conn, s = shop
    out = s.suggest(conn, "2019 silverado 1500 lev")
    assert out["vehicle"]["label"] == "2019 Chevrolet Silverado 1500" and out["vehicle"]["id"]
    assert out["categories"][0]["id"] == "leveling-kits"
    assert [p["part_number"] for p in out["products"]] == ["LV-200"]
    assert [p["part_number"] for p in s.suggest(conn, "lv-2")["part_numbers"]] == ["LV-200", "LV-201"]


def test_api(shop, monkeypatch):
    conn, _ = shop
    from fastapi.testclient import TestClient
    from store.app import create_app
    info = conn.info
    app = create_app(f"dbname={info.dbname} host={info.host} port={info.port} user={info.user}")
    c = TestClient(app)
    assert c.get("/api/health").json()["active_products"] == len(ITEMS)
    assert c.get("/api/vehicles/makes", params={"year": 2018}).json() == ["Ford"]
    models = c.get("/api/vehicles/models", params={"year": 2018, "make": "Ford"}).json()
    assert [m["model"] for m in models] == ["F-150", "Mustang"]
    r = c.get("/api/search", params={"q": "cat-back", "vehicle": models[1]["id"]}).json()
    assert r["vehicle"]["label"] == "2018 Ford Mustang" and r["total"] == 2
    assert c.get("/api/search", params={"sort": "nope"}).status_code == 400
    app.state.shop.pool.close()
