"""Product photos: the plain white border is trimmed and the same small margin put back, so every part fills its box
about the same amount. Photos already on the site are trimmed once from their stored copies."""
import io

from PIL import Image, ImageDraw

from importer import photos as ph
from importer.storage import LocalStorage


def photo(size=(800, 800), part=(350, 350, 450, 420), bg=(255, 255, 255)):
    im = Image.new("RGB", size, bg)
    ImageDraw.Draw(im).rectangle(part, fill=(40, 40, 45))
    return im


def test_small_part_on_white_fills_the_frame():
    out = ph.trim_white(photo())
    w, h = out.size                       # part 101 x 71 plus 4% of 101 on each side
    assert (w, h) == (101 + 8, 71 + 8)
    assert out.getpixel((0, 0)) == (255, 255, 255) and out.getpixel((w // 2, h // 2)) == (40, 40, 45)


def test_left_alone_when_not_on_white_or_already_full():
    grey = photo(bg=(225, 225, 228))
    assert ph.trim_white(grey).size == grey.size
    full = photo(part=(2, 2, 797, 797))
    assert ph.trim_white(full).size == full.size
    speck = photo(part=(400, 400, 402, 402))
    assert ph.trim_white(speck).size == speck.size


def test_stray_specks_do_not_stop_trimming():
    im = photo()
    im.putpixel((3, 3), (0, 0, 0))
    assert ph.trim_white(im).size == (109, 79)


def test_downloaded_photos_are_trimmed():
    buf = io.BytesIO()
    photo(size=(1000, 600), part=(100, 250, 300, 350)).save(buf, "PNG")
    data, (w, h) = ph._process(buf.getvalue())
    assert (w, h) == (201 + 16, 101 + 16)


def test_trim_existing_from_stored_copies(conn, tmp_path):
    from importer import load
    load.seed_reference(conn)
    conn.execute("INSERT INTO brands (id, name) VALUES ('b', 'B') ON CONFLICT DO NOTHING")
    pid = conn.execute("""INSERT INTO products (brand_id, part_number, slug, status, title, short_name, category_id)
                          VALUES ('b', 'P1', 'b-p1', 'active', 'B Part', 'Part', 'uncategorized') RETURNING id""").fetchone()[0]
    store = LocalStorage(tmp_path)
    buf = io.BytesIO()
    photo().save(buf, "JPEG", quality=95)
    store.put("products/b/ab/abc.jpg", buf.getvalue())
    conn.execute("""INSERT INTO product_images (product_id, asset_type, file_name, source_url, sort, status, storage_key, kind)
                    VALUES (%s, 'P04', 'a.jpg', 'https://x/a.jpg', 0, 'ok', 'products/b/ab/abc.jpg', 'part')""", (pid,))
    conn.commit()
    assert ph.trim_existing(conn, log=lambda *a: None, storage=store) == 1
    key, w = conn.execute("SELECT storage_key, width FROM product_images").fetchone()
    assert key == "products/b/ab/abc-t.jpg" and w < 200 and store.exists(key)
    assert conn.execute("SELECT main_image FROM products").fetchone()[0] == key
    assert ph.trim_existing(conn, log=lambda *a: None, storage=store) == 0      # once is enough
