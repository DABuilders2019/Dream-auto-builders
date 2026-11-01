"""Importer tests. Run with: DATABASE_URL=... pytest tests/

Uses small hand-written PIES / ACES / vehicle files so they run in seconds. The database tests
create and drop their own scratch database next to the one in DATABASE_URL.
"""
import os
import uuid
from decimal import Decimal

import openpyxl
import psycopg
import pytest

from importer import catalog, db, load, pies, vehicles, aces

PIES_XML = """<?xml version="1.0" encoding="utf-8"?>
<PIES xmlns="http://www.autocare.org">
  <Header><PIESVersion>7.0</PIESVersion><SubmissionType>FULL</SubmissionType><BlanketEffectiveDate>2026-10-19</BlanketEffectiveDate></Header>
  <Items>
    <Item MaintenanceType="A">
      <ItemLevelGTIN GTINQualifier="UP">00804879206484</ItemLevelGTIN>
      <PartNumber>LK-100</PartNumber><BrandAAIAID>TEST</BrandAAIAID>
      <PartTerminologyID>18833</PartTerminologyID>
      <Descriptions>
        <Description DescriptionCode="EXT">TestBrand 4" Lift Kit - Ram 1500</Description>
        <Description DescriptionCode="DEF">Suspension Lift Kit</Description>
        <Description DescriptionCode="FAB">Bolt-on install</Description>
        <Description DescriptionCode="MKT">Lifts your truck.</Description>
      </Descriptions>
      <Prices>
        <Pricing PriceType="RMP"><CurrencyCode>USD</CurrencyCode><Price UOM="PE">899.0000</Price></Pricing>
        <Pricing PriceType="RET"><CurrencyCode>USD</CurrencyCode><Price UOM="PE">999.9500</Price></Pricing>
      </Prices>
      <ProductAttributes>
        <ProductAttribute AttributeID="California Proposition 65" PADBAttribute="N">WARNING: test</ProductAttribute>
        <ProductAttribute AttributeID="Lift Height" PADBAttribute="N">4 in.</ProductAttribute>
        <ProductAttribute AttributeID="17" PADBAttribute="Y">Black</ProductAttribute>
      </ProductAttributes>
      <Packages><Package><Dimensions UOM="IN"><Height>8</Height><Width>6</Width><Length>10</Length></Dimensions><Weights UOM="PG"><Weight>42.5</Weight></Weights></Package></Packages>
      <DigitalAssets>
        <DigitalFileInformation><FileName>lk-100-alt.jpg</FileName><AssetType>P01</AssetType><FileType>JPG</FileType><URI>https://example.com/lk-100-alt.jpg</URI></DigitalFileInformation>
        <DigitalFileInformation><FileName>lk-100.jpg</FileName><AssetType>P04</AssetType><FileType>JPG</FileType><URI>https://example.com/lk-100.jpg</URI></DigitalFileInformation>
        <DigitalFileInformation><FileName>lk-100-360-01.jpg</FileName><AssetType>360</AssetType><FileType>JPG</FileType><URI>https://example.com/360.jpg</URI></DigitalFileInformation>
        <DigitalFileInformation><FileName>lk-100.pdf</FileName><AssetType>INS</AssetType><FileType>PDF</FileType><URI>https://example.com/lk-100.pdf</URI></DigitalFileInformation>
      </DigitalAssets>
    </Item>
    <Item MaintenanceType="A">
      <PartNumber>CAT-1</PartNumber><BrandAAIAID>TEST</BrandAAIAID>
      <Descriptions><Description DescriptionCode="DES">Direct-Fit Catalytic Converter</Description><Description DescriptionCode="DEF">Catalytic Converter</Description></Descriptions>
      <Prices><Pricing PriceType="RET"><Price UOM="PE">500.00</Price></Pricing></Prices>
      <ProductAttributes><ProductAttribute AttributeID="CARB Executive Order Number" PADBAttribute="N">D-193-199</ProductAttribute></ProductAttributes>
    </Item>
    <Item MaintenanceType="A">
      <PartNumber>CAT-2</PartNumber><BrandAAIAID>TEST</BrandAAIAID>
      <Descriptions><Description DescriptionCode="DES">Federal Catalytic Converter</Description><Description DescriptionCode="DEF">Catalytic Converter</Description></Descriptions>
      <Prices><Pricing PriceType="RET"><Price UOM="PE">300.00</Price></Pricing></Prices>
      <ProductAttributes><ProductAttribute AttributeID="CARB Executive Order Number" PADBAttribute="N">Not Applicable</ProductAttribute></ProductAttributes>
    </Item>
    <Item MaintenanceType="A">
      <PartNumber>NOPRICE</PartNumber><BrandAAIAID>TEST</BrandAAIAID>
      <Descriptions><Description DescriptionCode="DES">Mystery Bracket</Description><Description DescriptionCode="DEF">Widget Bracket</Description></Descriptions>
    </Item>
  </Items>
</PIES>"""

ACES_XML = """<?xml version="1.0" encoding="utf-8"?>
<ACES version="4.0"><Header><SubmissionType>FULL</SubmissionType></Header>
  <App action="A" id="1"><Years from="2019" to="2022"/><Make id="1168"/><Model id="2"/><DriveType id="8"/>
    <Qual id="5442"><text>with Dual Rear Wheels (DRW)</text></Qual><Qty>1</Qty><PartType id="18833"/><Part>LK-100</Part></App>
  <App action="A" id="2"><BaseVehicle id="18424"/><EngineBase id="1113"/><Qty>1</Qty><PartType id="14724"/><Part>CAT-1</Part></App>
  <App action="A" id="3"><BaseVehicle id="1"/><Qty>1</Qty><PartType id="1"/><Part>NOT-IN-PIES</Part></App>
</ACES>"""

BRAND = {"id": "testbrand", "name": "TestBrand", "aaia_id": "TEST", "display_price": ["RET", "RMP"], "strip_prefix": ["TestBrand"]}


@pytest.fixture
def files(tmp_path):
    p = tmp_path / "pies.xml"
    p.write_text(PIES_XML)
    a = tmp_path / "aces.xml"
    a.write_text(ACES_XML)
    v = tmp_path / "vehicles.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Make", "Model", "Year", "Submodel", "Submodel two", "Submodel three", "Product Name", "SKU", "SKU Variation Name",
               "Notes", "Submodel", "Body", "Engine"])
    for y in (2019, 2020, 2021, 2022):
        ws.append(["Ram", "1500", str(y), "Big Horn", "", "", "", "LK-100", "", "w/4 in. Lift;", "", "", "5.7"])
    ws.append(["Lexus", "GX470", "2005", "", "", "", "", "CAT-1", "", "", "", "", "4.7"])
    ws.append(["Ford", "F-150", "2020", "", "", "", "", "NOT-IN-PIES", "", "", "", "", ""])
    wb.save(v)
    return str(p), str(a), str(v)


# ---------------------------------------------------------------- parsing (no database)

def test_pies_reader(files):
    items = {i["part_number"]: i for i in pies.read_items(files[0])}
    assert set(items) == {"LK-100", "CAT-1", "CAT-2", "NOPRICE"}
    lk = items["LK-100"]
    assert {p["type"]: p["amount"] for p in lk["prices"]} == {"RMP": 899.0, "RET": 999.95}
    assert lk["package"] == {"weight_lb": 42.5, "dims": "10 x 6 x 8 in."}
    assert lk["padb_attributes"] == {"17": "Black"}
    assert pies.read_header(files[0])["SubmissionType"] == "FULL"


def test_build_product_prices_names_and_emissions(files):
    items = {i["part_number"]: i for i in pies.read_items(files[0])}
    cat = catalog.Categorizer()
    lk = catalog.build_product(items["LK-100"], BRAND, cat)
    assert lk["short_name"] == '4" Lift Kit - Ram 1500'          # brand prefix stripped
    assert lk["title"] == 'TestBrand 4" Lift Kit - Ram 1500'
    assert (lk["price"], lk["price_type"], lk["map_price"]) == (999.95, "RET", 899.0)
    assert lk["category_id"] == "lift-kits"
    assert lk["prop65_warning"] == "WARNING: test"
    assert lk["upc"] == "804879206484"
    assert lk["specs"]["Lift Height"] == "4 in."
    c1 = catalog.build_product(items["CAT-1"], BRAND, cat)
    c2 = catalog.build_product(items["CAT-2"], BRAND, cat)
    assert (c1["emissions"], c1["carb_eo"]) == ("carb_legal", "D-193-199")
    assert (c2["emissions"], c2["carb_eo"]) == ("federal_only", None)
    nop = catalog.build_product(items["NOPRICE"], BRAND, cat)
    assert nop["status"] == "hidden" and nop["category_id"] == "uncategorized"


def test_photo_assets_order_and_filtering(files):
    lk = next(i for i in pies.read_items(files[0]) if i["part_number"] == "LK-100")
    assert [a["file_name"] for a in catalog.photo_assets(lk)] == ["lk-100.jpg", "lk-100-alt.jpg"]   # primary first, no 360/PDF
    assert catalog.install_docs(lk) == ["https://example.com/lk-100.pdf"]


def test_categorizer_rules():
    c = catalog.Categorizer()
    assert c.place("Exhaust System Kit", "Street Series Cat-Back Performance Exhaust") == "cat-back"
    assert c.place("Exhaust System Kit", "Race Series Axle-Back") == "axle-back"
    assert c.place("Exhaust System Kit", "Competition Series Header-Back") == "exhaust-kits"
    assert c.place("Catalytic Converter with Integrated Exhaust Manifold", "") == "catalytic-converters"
    assert c.place("Suspension Control Arm", "") == "suspension-components"
    assert c.place("Exhaust Tail Pipe Tip", "") == "exhaust-tips"


def test_aces_and_vehicle_readers(files):
    apps = list(aces.read_apps(files[1]))
    assert apps[0]["vcdb"] == {"DriveType": 8} and apps[0]["qualifiers"] == ["with Dual Rear Wheels (DRW)"]
    assert apps[1]["base_vehicle_id"] == 18424 and apps[1]["vcdb"] == {"EngineBase": 1113}
    rows = list(vehicles.read_rows(files[2]))
    assert rows[0]["year"] == 2019 and rows[0]["engine"] == "5.7" and rows[0]["notes"] == "w/4 in. Lift;"


# ---------------------------------------------------------------- full load (database)

@pytest.fixture
def conn(monkeypatch):
    base = os.environ.get("DATABASE_URL")
    if not base:
        pytest.skip("DATABASE_URL not set")
    name = "dab_test_" + uuid.uuid4().hex[:8]
    with psycopg.connect(base, autocommit=True) as admin:
        admin.execute(f"CREATE DATABASE {name}")
    info = psycopg.conninfo.conninfo_to_dict(base)
    info["dbname"] = name
    c = psycopg.connect(**info)
    db.migrate(c)
    monkeypatch.setattr(load.config, "brands", lambda: {"testbrand": BRAND})
    monkeypatch.setattr(load.config, "image_reviews", lambda: [])
    monkeypatch.setattr(load.config, "extra_images", lambda: [])
    yield c
    c.close()
    with psycopg.connect(base, autocommit=True) as admin:
        admin.execute(f"DROP DATABASE {name}")


def test_load_brand_end_to_end(conn, files):
    stats = load.load_brand(conn, "testbrand", *files, log=lambda *a: None)
    assert stats["products_in_file"] == 4 and stats["products_active"] == 3 and stats["products_hidden"] == 1
    assert stats["aces_applications"] == 2 and stats["aces_parts_not_in_pies"] == 1
    assert stats["fitment_rows"] == 5 and stats["fitment_parts_not_in_pies"] == 1
    q = lambda sql, *a: conn.execute(sql, a).fetchall()
    # what the storefront asks: which active parts fit a 2021 Ram 1500?
    fits = q("""SELECT p.part_number FROM products p JOIN fitment f ON f.product_id = p.id JOIN vehicles v ON v.id = f.vehicle_id
                WHERE p.status = 'active' AND v.year = 2021 AND v.make = 'Ram' AND v.model = '1500'""")
    assert fits == [("LK-100",)]
    assert q("SELECT fitment_status FROM products WHERE part_number = 'CAT-2'") == [("missing",)]
    assert q("SELECT fitment_status FROM products WHERE part_number = 'LK-100'") == [("vehicle_specific",)]
    assert q("SELECT count(*) FROM product_images") == [(2,)]
    assert q("SELECT count(*) FROM product_documents") == [(1,)]
    assert q("SELECT p.price_type, p.amount FROM product_prices p JOIN products x ON x.id = p.product_id WHERE x.part_number='LK-100' ORDER BY 1") == [("RET", Decimal("999.95")), ("RMP", Decimal("899.00"))]


def test_reload_is_idempotent_and_discontinues_missing_parts(conn, files, tmp_path):
    load.load_brand(conn, "testbrand", *files, log=lambda *a: None)
    load.load_brand(conn, "testbrand", *files, log=lambda *a: None)
    assert conn.execute("SELECT count(*) FROM products").fetchone() == (4,)
    assert conn.execute("SELECT count(*) FROM fitment").fetchone() == (5,)
    # next month's FULL file no longer lists CAT-2
    smaller = tmp_path / "pies2.xml"
    start = PIES_XML.index("<Item", PIES_XML.index("<PartNumber>CAT-2") - 300)
    end = PIES_XML.index("</Item>", start) + len("</Item>")
    smaller.write_text(PIES_XML[:start] + PIES_XML[end:])
    stats = load.load_brand(conn, "testbrand", str(smaller), files[1], files[2], log=lambda *a: None)
    assert stats["products_discontinued_since_last_file"] == 1
    assert conn.execute("SELECT status FROM products WHERE part_number='CAT-2'").fetchone() == ("discontinued",)


def test_failed_load_leaves_previous_data(conn, files, tmp_path):
    load.load_brand(conn, "testbrand", *files, log=lambda *a: None)
    broken = tmp_path / "broken.xml"
    broken.write_text(PIES_XML[: len(PIES_XML) // 2])      # truncated download
    with pytest.raises(Exception):
        load.load_brand(conn, "testbrand", str(broken), files[1], files[2], log=lambda *a: None)
    assert conn.execute("SELECT count(*) FROM products WHERE status='active'").fetchone() == (3,)
    assert conn.execute("SELECT status FROM import_runs ORDER BY id DESC LIMIT 1").fetchone() == ("failed",)
