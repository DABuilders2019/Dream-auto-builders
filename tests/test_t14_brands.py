"""Turn 14 page: Turn 14's own brand list (approved for us, on our site or not), and adding one to the SEMA list."""
import html

from test_checkout import shop  # noqa: F401


def test_turn14_brand_list(shop):
    c, conn = shop.c, shop.conn
    ours = conn.execute("SELECT id, name FROM brands WHERE EXISTS (SELECT 1 FROM products p WHERE p.brand_id = brands.id) LIMIT 1").fetchone()
    conn.execute("""INSERT INTO t14_brands (id, name, aaia, approved, dropship, brand_id) VALUES
        (1, 'Our Brand At T14', '{AAAA}', true, true, %s),
        (2, 'Zippy Parts', '{BBBB}', true, true, NULL),
        (3, 'Obscure Co', '{CCCC}', true, false, NULL),
        (4, 'Not Approved Inc', '{DDDD}', false, NULL, NULL)""", (ours[0],))
    conn.execute("INSERT INTO sema_brands (aaia, name, approved, wanted) VALUES ('BBBB', 'Zippy Parts', true, false) ON CONFLICT DO NOTHING")
    conn.commit()
    c.post("/admin/login", data={"password": "pw"})
    page = html.unescape(c.get("/admin/turn14").text)
    assert "Turn 14's brands" in page and "Approved for you (3)" in page and "All Turn 14 brands (4)" in page
    assert "Not Approved Inc" not in page                       # hidden until "All"
    assert "Add to SEMA list" in page and "Add from Turn 14" in page
    assert f'href="/brand/{ours[0]}"' in page
    assert "Not Approved Inc" in c.get("/admin/turn14?t14=all").text
    r = c.post("/admin/turn14/add-to-sema", data={"name": "Zippy Parts"}, follow_redirects=True)
    page = html.unescape(r.text)
    assert "Zippy Parts is on your SEMA brand list" in page and "On your SEMA list" in page
    from semadata import sync as ss
    assert "Zippy Parts" in ss.wishlist(conn)


def test_turn14_brand_matches_sema_by_name(shop):
    c, conn = shop.c, shop.conn
    conn.execute("INSERT INTO t14_brands (id, name, aaia, approved) VALUES (7, 'Nitto Tire', '{T14X}', true)")
    conn.execute("INSERT INTO sema_brands (aaia, name, approved, wanted) VALUES ('NTTO', 'NITTO TIRE', true, false)")
    conn.commit()
    c.post("/admin/login", data={"password": "pw"})
    page = html.unescape(c.get("/admin/turn14").text)
    assert "as NITTO TIRE" in page and "<b>1</b> more is at SEMA" in page


def test_turn14_only_brand_offers_add_from_turn14(shop, monkeypatch):
    c, conn = shop.c, shop.conn
    conn.execute("INSERT INTO t14_brands (id, name, aaia, approved) VALUES (9, 'K&N', '{BBBK}', true)")
    conn.commit()
    c.post("/admin/login", data={"password": "pw"})
    page = html.unescape(c.get("/admin/turn14").text)
    assert "Add from Turn 14" in page and "only approved at Turn 14" in page
    started = []
    from store import turn14_schedule
    monkeypatch.setenv("TURN14_CLIENT_ID", "cid")
    monkeypatch.setenv("TURN14_CLIENT_SECRET", "secret")
    monkeypatch.setattr(turn14_schedule, "start", lambda st, task, label=None: started.append((task, label)) or True)
    r = c.post("/admin/turn14/add-from-turn14", data={"t14_id": "9"}, follow_redirects=False)
    assert r.status_code == 303 and started == [("catalog:9", "Add K&N from Turn 14")]


def test_sema_page_turn14_column_and_sync_button(shop, monkeypatch):
    c, conn = shop.c, shop.conn
    conn.execute("INSERT INTO sema_brands (aaia, name, approved, wanted, status, brand_id) VALUES ('TEST', 'TestBrand', true, true, 'loaded', 'testbrand')")
    conn.execute("INSERT INTO t14_brands (id, name, aaia, approved, brand_id) VALUES (1, 'TestBrand', '{TEST}', true, 'testbrand')")
    pid = conn.execute("SELECT id FROM products WHERE brand_id = 'testbrand' LIMIT 1").fetchone()[0]
    conn.execute("INSERT INTO t14_items (item_id, t14_brand_id, product_id) VALUES ('1G', 1, %s)", (pid,))
    conn.execute("INSERT INTO t14_inventory (item_id, total) VALUES ('1G', 4)")
    conn.execute("INSERT INTO sema_state (key, value) VALUES ('wishlist', '[\"TestBrand\"]'), ('matches', '{\"TestBrand\": {\"aaia\": \"TEST\", \"choices\": []}}') ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value")
    conn.commit()
    monkeypatch.setenv("TURN14_CLIENT_ID", "cid")
    monkeypatch.setenv("TURN14_CLIENT_SECRET", "secret")
    c.post("/admin/login", data={"password": "pw"})
    page = html.unescape(c.get("/admin/sema").text)
    assert "1 matched" in page and "1 in stock" in page and "Sync with Turn 14" in page
    started = []
    from store import turn14_schedule
    monkeypatch.setattr(turn14_schedule, "start", lambda st, task, label=None: started.append((task, label)) or True)
    r = c.post("/admin/sema/turn14/testbrand", follow_redirects=True)
    assert started == [("brand:testbrand", "Sync TestBrand with Turn 14")] and "progress on the Turn 14 page" in r.text


def test_brand_added_from_turn14_has_reload(shop, monkeypatch):
    c, conn = shop.c, shop.conn
    conn.execute("INSERT INTO brands (id, name) VALUES ('michelin', 'Michelin')")
    conn.execute("INSERT INTO t14_brands (id, name, aaia, approved, brand_id, load_catalog) VALUES (428, 'Michelin', '{}', true, 'michelin', true)")
    conn.commit()
    c.post("/admin/login", data={"password": "pw"})
    page = html.unescape(c.get("/admin/turn14").text)
    assert "From Turn 14 (no fitment)" in page and "Reload from Turn 14" in page
    started = []
    from store import turn14_schedule
    monkeypatch.setenv("TURN14_CLIENT_ID", "cid")
    monkeypatch.setenv("TURN14_CLIENT_SECRET", "secret")
    monkeypatch.setattr(turn14_schedule, "start", lambda st, task, label=None: started.append((task, label)) or True)
    c.post("/admin/turn14/add-from-turn14", data={"t14_id": "428"})
    assert started == [("catalog:428", "Reload Michelin from Turn 14")]


def test_catalog_page_offers_trimming_once(shop, monkeypatch):
    c, conn = shop.c, shop.conn
    pid = conn.execute("SELECT id FROM products LIMIT 1").fetchone()[0]
    conn.execute("""INSERT INTO product_images (product_id, asset_type, file_name, source_url, sort, status, storage_key, kind)
                    VALUES (%s, 'P04', 'a.jpg', 'https://x/a.jpg', 9, 'ok', 'products/x/ab/abc.jpg', 'part')""", (pid,))
    conn.commit()
    c.post("/admin/login", data={"password": "pw"})
    assert "Even out photo margins" in c.get("/admin").text
    started = []
    c.app.state.shop.jobs.start = lambda title, steps, brand_id=None: started.append(steps) or True
    c.post("/admin/photos-trim")
    assert started == [[("Trim photos already on the site", ["trim-photos"])]]
    conn.execute("INSERT INTO app_state (key, value) VALUES ('photos_trimmed', 'true')")
    conn.commit()
    assert "Even out photo margins" not in c.get("/admin").text
