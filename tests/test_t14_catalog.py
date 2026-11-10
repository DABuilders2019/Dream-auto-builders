"""Add from Turn 14: a brand approved at Turn 14 but not at SEMA, built from Turn 14's item data; later upgraded
in place (same products, same web addresses) when SEMA's data for the brand is loaded."""
import httpx
import pytest

from conftest import make_pies
import pathlib

from importer import brands as brand_lookup
from importer import load
from turn14.client import Client, StopSync
from turn14 import catalog
from turn14.sync import run

KN = 77


def kn_item(iid, mfr, **kw):
    return {"id": iid, "type": "Item", "attributes": {
        "product_name": f"K&N {kw.pop('name', 'Replacement Air Filter')}", "part_number": "kn" + mfr, "mfr_part_number": mfr,
        "brand_id": KN, "active": True, "regular_stock": True, "part_description": "K&N Air Filter",
        "category": "Air Intake", "subcategory": "Air Filters - Drop In", "barcode": "024844012345",
        "dimensions": [{"box_number": 1, "length": 12, "width": 9, "height": 3, "weight": 1.5}],
        "thumbnail": "https://img.example/thumb.jpg", "prop_65": "Y", **kw}}


class FakeKN:
    def __init__(self):
        self.calls = []
        self.retail = 64.99

    def __call__(self, request):
        path = request.url.path
        self.calls.append(path)
        if path == "/v1/token":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        if path == "/v1/brands":
            return httpx.Response(200, json={"data": [
                {"id": str(KN), "attributes": {"name": "K&N", "AAIA": ["BBBK"], "pricegroups": []}},
                {"id": "78", "attributes": {"name": "Nope", "AAIA": ["NOPE"],
                 "pricegroups": [{"purchase_restrictions": [{"your_status": "pending"}]}]}}]})
        if path == "/v1/locations":
            return httpx.Response(200, json={"data": []})
        if path == "/v1/items/updates":
            return httpx.Response(200, json={"meta": {"total_pages": 1}, "data": []})
        if path == f"/v1/items/brand/{KN}":
            return httpx.Response(200, json={"meta": {"total_pages": 1}, "data": [
                kn_item("5001", "33-2304"), kn_item("5001G", "33-2304"), kn_item("5002", "E-0665", name="Oil Filter"),
                kn_item("5003", "99-5000", active=False)]})
        if path == f"/v1/items/data/brand/{KN}":
            return httpx.Response(200, json={"meta": {"total_pages": 1}, "data": [
                {"id": "5001G", "type": "ProductData",
                 "descriptions": [{"type": "Market Description", "description": "<p>Washable &amp; reusable.</p>"},
                                  {"type": "Features and Benefits", "description": "<li>Lasts 10 years</li><li>More airflow</li>"}],
                 "files": [{"type": "Image", "file_extension": "JPG", "media_content": "Photo - Mounted",
                            "links": [{"url": "https://img.example/mounted-s.jpg", "width": "100", "height": "100"},
                                      {"url": "https://img.example/mounted-l.jpg", "width": "1000", "height": "1000"},
                                      {"url": "https://img.example/mounted-x.jpg", "width": None, "height": "abc"}]},
                           {"type": "Image", "file_extension": "JPG", "media_content": "Photo - Primary",
                            "links": [{"url": "https://img.example/primary.jpg", "width": 800, "height": 800}]},
                           {"type": "Other", "file_extension": "PDF", "media_content": "Installation Instructions",
                            "links": [{"url": "https://img.example/install.pdf"}]}]}]})
        if path == f"/v1/pricing/brand/{KN}":
            return httpx.Response(200, json={"meta": {"total_pages": 1}, "data": [
                {"id": "5001G", "attributes": {"purchase_cost": 40, "has_map": True, "can_purchase": True,
                 "pricelists": [{"name": "MAP", "price": 59.99}, {"name": "Retail", "price": self.retail}, {"name": "Jobber", "price": 55}]}},
                {"id": "5002", "attributes": {"purchase_cost": 8, "has_map": False, "can_purchase": True, "pricelists": []}}]})
        if path == f"/v1/inventory/brand/{KN}":
            return httpx.Response(200, json={"meta": {"total_pages": 1}, "data": [
                {"id": "5001G", "attributes": {"inventory": {"01": 6}}}]})
        return httpx.Response(404, json={"errors": [{"detail": "no route"}]})


@pytest.fixture
def kn(conn, monkeypatch):
    load.seed_reference(conn)
    conn.commit()
    conn.autocommit = True
    monkeypatch.setenv("TURN14_CLIENT_ID", "cid")
    monkeypatch.setenv("TURN14_CLIENT_SECRET", "secret")
    monkeypatch.setenv("T14_SKIP_PHOTOS", "1")
    fake = FakeKN()

    def go(task):
        c = Client(conn, task=task, transport=httpx.MockTransport(fake), sleep=lambda s: None, log=lambda *a: None)
        return run(conn, task, log=lambda *a: None, client=c)
    return conn, fake, go


def test_add_from_turn14_builds_products(kn):
    conn, fake, go = kn
    go("test")
    stats = go(f"catalog:{KN}")
    assert stats["catalog_products"] == 3 and stats["catalog_active"] == 1
    assert stats["no_retail_price_cost_only"] == 1               # the oil filter: Turn 14 gives only our cost
    b = conn.execute("SELECT id, name, aaia_id FROM brands WHERE aaia_id = 'BBBK'").fetchone()
    assert b == ("kn", "K&N", "BBBK")
    assert conn.execute(f"SELECT brand_id, load_catalog FROM t14_brands WHERE id = {KN}").fetchone() == ("kn", True)
    p = dict(zip(["slug", "status", "title", "price", "map_price", "fitment_status", "marketing_copy", "features", "specs", "upc", "prop65"],
                 conn.execute("""SELECT slug, status, title, price, map_price, fitment_status, marketing_copy, features, specs, upc,
                                        prop65_warning FROM products WHERE part_number = '33-2304'""").fetchone()))
    assert p["slug"] == "kn-33-2304" and p["status"] == "active" and p["title"] == "K&N Replacement Air Filter"
    assert float(p["price"]) == 64.99 and float(p["map_price"]) == 59.99
    assert p["fitment_status"] == "missing"                      # no vehicle data: "Fitment not listed"
    assert p["marketing_copy"] == "Washable & reusable." and p["features"] == ["Lasts 10 years", "More airflow"]
    assert p["specs"]["Shipping Weight"] == "1.5 lb" and p["specs"]["Package Dimensions"] == "12 x 9 x 3 in."
    assert p["upc"] == "24844012345" and p["prop65"]
    # oil filter has no price at Turn 14: kept off the storefront; the inactive item is discontinued
    assert conn.execute("SELECT status FROM products WHERE part_number = 'E-0665'").fetchone()[0] == "hidden"
    assert conn.execute("SELECT status FROM products WHERE part_number = '99-5000'").fetchone()[0] == "discontinued"
    imgs = conn.execute("""SELECT i.asset_type, i.source_url FROM product_images i JOIN products p ON p.id = i.product_id
                           WHERE p.part_number = '33-2304' ORDER BY i.sort""").fetchall()
    assert imgs[0] == ("P04", "https://img.example/primary.jpg") and ("P01", "https://img.example/mounted-l.jpg") in imgs
    assert conn.execute("""SELECT url FROM product_documents d JOIN products p ON p.id = d.product_id
                           WHERE p.part_number = '33-2304'""").fetchone()[0] == "https://img.example/install.pdf"
    # the kit item (G) is the one matched, so stock and ordering use it
    assert conn.execute("""SELECT i.item_id FROM t14_items i JOIN products p ON p.id = i.product_id
                           WHERE p.part_number = '33-2304'""").fetchone()[0] == "5001G"
    assert f"/v1/items/data/brand/{KN}" in fake.calls


def test_not_approved_or_sema_brand_refused(kn):
    conn, fake, go = kn
    go("test")
    with pytest.raises(StopSync, match="isn't approved"):
        go("catalog:78")
    conn.execute("INSERT INTO brands (id, name, aaia_id) VALUES ('kn', 'K&N', 'BBBK')")
    conn.execute(f"UPDATE t14_brands SET brand_id = 'kn' WHERE id = {KN}")
    with pytest.raises(StopSync, match="already on the site from SEMA"):
        go(f"catalog:{KN}")


def test_daily_rebuilds_then_sema_takes_over(kn, tmp_path):
    conn, fake, go = kn
    go("test")
    go(f"catalog:{KN}")
    pid = conn.execute("SELECT id FROM products WHERE part_number = '33-2304'").fetchone()[0]
    fake.retail = 69.99                                         # Turn 14's price changes; the daily sync passes it on
    go("daily")
    assert float(conn.execute("SELECT price FROM products WHERE id = %s", (pid,)).fetchone()[0]) == 69.99
    # SEMA approves K&N under a different code: same brand, same product (and web address), now with SEMA's data
    brand = brand_lookup.resolve(conn, "KNFL", "K&N", "K&N")
    assert brand["id"] == "kn" and brand["aaia_id"] == "KNFL"
    pies = make_pies(tmp_path / "kn.xml", [{"pn": "33-2304", "name": "Air Filter from SEMA", "part_type": "Air Filter", "price": "70.00"}])
    pth = pathlib.Path(pies)
    pth.write_text(pth.read_text().replace("<BrandAAIAID>TEST</BrandAAIAID>", "<BrandAAIAID>KNFL</BrandAAIAID>"))
    load.load_brand(conn, brand, str(pth), None, None, log=lambda *a: None)
    conn.execute("INSERT INTO sema_brands (aaia, name, approved, wanted, status, brand_id) VALUES ('KNFL', 'K&N', true, true, 'loaded', 'kn')")
    conn.execute("UPDATE t14_brands SET load_catalog = false WHERE brand_id = 'kn'")
    row = conn.execute("SELECT id, slug, title FROM products WHERE part_number = '33-2304'").fetchone()
    assert row == (pid, "kn-33-2304", "K&N Air Filter from SEMA")
    assert conn.execute("SELECT count(*) FROM products WHERE brand_id = 'kn'").fetchone()[0] == 3
    assert catalog.catalog_brands(conn) == []                    # Turn 14 no longer rebuilds it



def test_turn14_prices_and_tires():
    row = {"pricelists": [{"name": "Suggested Retail", "price": "120.00"}, {"name": "MAP", "price": 99},
                          {"name": "Jobber", "price": 90}], "purchase_cost": "70", "has_map": True}
    assert catalog.price_types(row) == {"RET": 120.0, "RMP": 99.0, "JBR": 90.0, "CST": 70.0}
    assert "RMP" not in catalog.price_types({**row, "has_map": False})
    assert catalog.part_type_of({"category": "Wheels and Tires", "subcategory": "Automotive/UTV Tires - On Road"}) == "Tire"
    assert catalog.part_type_of({"category": "Wheels and Tires", "subcategory": "Tire Pressure Monitoring"}) == "Tire Pressure Monitoring"
    gaps = catalog.price_gaps([{"active": True, "pricelists": [], "purchase_cost": 5},
                               {"active": True, "pricelists": [{"name": "Jobber", "price": 9}]},
                               {"active": True, "pricelists": [], "can_purchase": False},
                               {"active": True, "pricelists": []}, {"active": False, "pricelists": []}])
    assert gaps == {"cost_only": 1, "jobber": 1, "cant_buy": 1, "nothing": 1}


def test_cost_plus_markup_never_below_map():
    from importer.catalog import choose_price
    assert choose_price({"CST": 70, "RMP": 99}, ["RET", "RMP", "LST"], None) == (99, "RMP")
    assert choose_price({"CST": 70}, ["RET", "RMP", "LST", "CST"], 30) == (91.0, "CST")


def test_turn14_photos_for_sema_parts_without_any(kn):
    """A brand from SEMA with no photos (like Brembo): its parts matched at Turn 14 get Turn 14's photos, and a later
    SEMA reload keeps them."""
    conn, fake, go = kn
    conn.execute("INSERT INTO brands (id, name, aaia_id) VALUES ('semakn', 'K&N', 'BBBK')")
    conn.execute("""INSERT INTO products (brand_id, part_number, slug, status, title, short_name, category_id, price)
                    VALUES ('semakn', '33-2304', 'semakn-33-2304', 'active', 'K&N Filter', 'Filter', 'uncategorized', 60),
                           ('semakn', 'E-0665', 'semakn-e-0665', 'active', 'K&N Oil Filter', 'Oil Filter', 'uncategorized', 9)""")
    go("test")
    stats = go("brand:semakn")
    assert stats["parts_given_turn14_photos"] == 2
    rows = conn.execute("""SELECT p.part_number, i.asset_type, i.source_url, i.sort FROM product_images i
                           JOIN products p ON p.id = i.product_id ORDER BY p.part_number, i.sort""").fetchall()
    assert rows[0] == ("33-2304", "T14", "https://img.example/primary.jpg", 100)          # primary photo first
    assert ("E-0665", "T14", "https://img.example/thumb.jpg", 100) in rows                 # no photo data: thumbnail
    data_calls = fake.calls.count(f"/v1/items/data/brand/{KN}")
    go("brand:semakn")                                     # nothing new to do: photos waiting, no extra requests
    assert fake.calls.count(f"/v1/items/data/brand/{KN}") == data_calls
    # a SEMA reload of the brand (its data has no photos) keeps the Turn 14 photos
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        pies = make_pies(pathlib.Path(tmp) / "p.xml", [{"pn": "33-2304", "name": "Filter", "part_type": "Air Filter", "price": "60"},
                                                      {"pn": "E-0665", "name": "Oil Filter", "part_type": "Oil Filter", "price": "9"}])
        load.load_brand(conn, {"id": "semakn", "name": "K&N", "aaia_id": None}, str(pies), None, None, log=lambda *a: None)
    assert conn.execute("SELECT count(*) FROM product_images WHERE asset_type = 'T14'").fetchone()[0] == len(rows)
