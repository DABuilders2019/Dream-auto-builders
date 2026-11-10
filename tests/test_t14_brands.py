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
    assert "Add to SEMA list" in page and "Not at SEMA for you" in page
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
    assert "as NITTO TIRE" in page and "1 more is at SEMA" in page
