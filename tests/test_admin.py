"""Admin page: sign-in, uploading a brand's files, and the background import; plus the private-site password."""
import html
import io
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from conftest import make_pies, make_vehicles
from store.admin import classify

ITEMS = [
    {"pn": "AC-1", "name": "Acme 3in Lift Kit - Ram 1500", "part_type": "Suspension Lift Kit", "price": 500.00},
    {"pn": "AC-2", "name": "Acme Exhaust Tip", "part_type": "Exhaust Tail Pipe Tip", "price": 40.00},
]


@pytest.fixture
def app(conn, tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PHOTO_DIR", str(tmp_path / "data" / "photos"))
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin-pw")
    monkeypatch.delenv("SITE_PASSWORD", raising=False)
    from store.app import create_app
    return create_app(conn.info.dsn)


def acme_files(tmp_path):
    pies = make_pies(tmp_path / "acme.xml", ITEMS)
    text = open(pies).read().replace("<BrandAAIAID>TEST</BrandAAIAID>", "<BrandAAIAID>ACME</BrandAAIAID>")
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w") as z:
        z.writestr("SDC_ACME_PIES 7.0 XML.xml", text)
    veh = make_vehicles(tmp_path / "SDC_ACME_Admark Vehicle Assignment Template.xlsx",
                        [("Ram", "1500", y, "AC-1", "5.7") for y in (2020, 2021)])
    return zbuf.getvalue(), open(veh, "rb").read()


def test_admin_needs_password(app):
    with TestClient(app) as c:
        r = c.get("/admin", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/admin/login"
        assert "isn't right" in html.unescape(c.post("/admin/login", data={"password": "wrong"}).text)
        assert c.post("/admin/upload", files={"files": ("x.xml", b"<PIES/>")}, follow_redirects=False).headers["location"] == "/admin/login"


def test_admin_off_without_password(app, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "")
    with TestClient(app) as c:
        assert c.get("/admin").status_code == 404


def test_upload_imports_a_new_brand(app, tmp_path):
    pies_zip, sheet = acme_files(tmp_path)
    with TestClient(app) as c:
        c.post("/admin/login", data={"password": "admin-pw"})
        assert "Add or update a brand" in c.get("/admin").text
        r = c.post("/admin/upload", data={"brand_name": "Acme Parts"},
                   files=[("files", ("Acme_PIES_7.0_XML.zip", pies_zip, "application/zip")),
                          ("files", ("SDC_ACME_Admark Vehicle Assignment Template.xlsx", sheet, "application/octet-stream"))],
                   follow_redirects=False)
        assert r.status_code == 303 and "started=Acme" in r.headers["location"], r.headers["location"]
        app.state.shop.jobs.wait(120)
        page = c.get("/admin").text
        assert "Finished" in page and "Acme Parts" in page, page[-3000:]
        assert "AC-1" in c.get("/brand/acmeparts").text            # on the shop right away
        assert "Fits 2020-2021 Ram 1500" in c.get("/brand/acmeparts").text

        # same brand again: recognised by its brand code, no name needed
        c.post("/admin/upload", files=[("files", ("Acme_PIES_7.0_XML.zip", pies_zip, "application/zip"))])
        app.state.shop.jobs.wait(120)
        with app.state.shop.pool.connection() as conn:
            assert conn.execute("SELECT count(*) FROM brands WHERE aaia_id = 'ACME'").fetchone()[0] == 1


def test_upload_problems_are_explained(app, tmp_path):
    pies_zip, sheet = acme_files(tmp_path)
    with TestClient(app) as c:
        c.post("/admin/login", data={"password": "admin-pw"})
        r = c.post("/admin/upload", files=[("files", ("notes.txt", b"hello", "text/plain"))])
        assert "isn't a SEMA" in html.unescape(r.text)
        r = c.post("/admin/upload", files=[("files", ("SDC_ACME_Admark.xlsx", sheet, "application/octet-stream"))])
        assert "Include the brand's PIES file" in html.unescape(r.text)
        r = c.post("/admin/upload", files=[("files", ("a.zip", pies_zip, "application/zip")),
                                           ("files", ("SDC_BKMQ_Admark Vehicle Assignment Template.xlsx", sheet, "x"))])
        assert "is for brand code BKMQ" in html.unescape(r.text)


def test_classify(tmp_path):
    (tmp_path / "a.xml").write_text('<?xml version="1.0"?><ACES version="4.0"><Header/></ACES>')
    assert classify(tmp_path / "a.xml") == "aces"
    with zipfile.ZipFile(tmp_path / "assets.zip", "w") as z:
        z.writestr("img/1.jpg", b"x")
        z.writestr("img/2.png", b"x")
    assert classify(tmp_path / "assets.zip") == "assets"


def test_private_site_password(app, monkeypatch):
    monkeypatch.setenv("SITE_PASSWORD", "letmein")
    with TestClient(app) as c:
        r = c.get("/c/exhaust", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/login")
        assert c.get("/api/health").status_code == 200
        assert c.get("/api/search").status_code == 401
        assert "isn't right" in html.unescape(c.post("/login", data={"password": "nope"}).text)
        r = c.post("/login?next=/c/exhaust", data={"password": "letmein"}, follow_redirects=False)
        assert r.headers["location"] == "/c/exhaust"
        assert c.get("/c/exhaust").status_code == 200
