"""Shared test fixtures: a scratch database per test, and helpers to write small SEMA-style files."""
import os
import sys
import uuid
from xml.sax.saxutils import escape

import openpyxl
import psycopg
import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from importer import db, load  # noqa: E402

BRAND = {"id": "testbrand", "name": "TestBrand", "aaia_id": "TEST", "display_price": ["RET", "RMP"], "strip_prefix": ["TestBrand"]}


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
        admin.execute(f"DROP DATABASE {name} WITH (FORCE)")


def make_pies(path, items):
    """items: dicts with pn, name, part_type, and optional price, attrs, photo."""
    out = ['<?xml version="1.0" encoding="utf-8"?><PIES xmlns="http://www.autocare.org">',
           "<Header><PIESVersion>7.0</PIESVersion><SubmissionType>FULL</SubmissionType></Header><Items>"]
    for it in items:
        attrs = "".join(f'<ProductAttribute AttributeID="{escape(k)}" PADBAttribute="N">{escape(v)}</ProductAttribute>'
                        for k, v in it.get("attrs", {}).items())
        price = f'<Prices><Pricing PriceType="RET"><Price UOM="PE">{it["price"]}</Price></Pricing></Prices>' if it.get("price") else ""
        out.append(f'''<Item MaintenanceType="A"><PartNumber>{escape(it["pn"])}</PartNumber><BrandAAIAID>TEST</BrandAAIAID>
            <Descriptions><Description DescriptionCode="EXT">{escape(it["name"])}</Description>
            <Description DescriptionCode="DEF">{escape(it["part_type"])}</Description></Descriptions>{price}
            <ProductAttributes>{attrs}</ProductAttributes></Item>''')
    out.append("</Items></PIES>")
    with open(path, "w") as f:
        f.write("\n".join(out))
    return str(path)


def make_vehicles(path, rows):
    """rows: (make, model, year, sku, engine)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Make", "Model", "Year", "Submodel", "Submodel two", "Submodel three", "Product Name", "SKU", "SKU Variation Name",
               "Notes", "Submodel", "Body", "Engine"])
    for make, model, year, sku, engine in rows:
        ws.append([make, model, str(year), "", "", "", "", sku, "", "", "", "", engine])
    wb.save(path)
    return str(path)
