"""Price match: the request from the product page (link or photo), checks on what's sent, the admin review and emails,
the claim link putting the part in the cart at the matched price for 24 hours, checkout charging that price, and the
match being used up once the order is paid."""
import html
import io
import re

from PIL import Image

from store.mailer import _deliver as REAL_DELIVER
from test_checkout import paid_event, pid, post_webhook, shop  # noqa: F401


def slug_price(conn, p):
    slug, price = conn.execute("SELECT slug, price FROM products WHERE id = %s", (p,)).fetchone()
    return slug, float(price)


def send(c, p, **kw):
    data = {"product_id": str(p), "url": "https://www.other-store.com/part/123", "their_price": "10.00", "qty": "1",
            "email": "buyer@example.com"}
    data.update(kw.pop("data", {}))
    r = c.post("/price-match", data=data, follow_redirects=True, **kw)
    r.page = html.unescape(r.text)
    return r


def png():
    b = io.BytesIO()
    Image.new("RGB", (40, 30), (200, 10, 10)).save(b, "PNG")
    return b.getvalue()


def test_product_page_has_closed_card(shop):
    c = shop.c
    slug, _ = slug_price(shop.conn, pid(c, "LK-100"))
    page = c.get(f"/p/{slug}").text
    assert 'id="pricematch"' in page and "Found it for less? We'll price match." in page
    assert '<details class="pmB" id="pricematch">' in page          # closed until clicked
    assert 'enctype="multipart/form-data"' in page


def test_request_checks(shop):
    c, conn = shop.c, shop.conn
    p = pid(c, "LK-100")
    slug, price = slug_price(conn, p)
    assert "Add a link" in send(c, p, data={"url": ""}).page
    assert "doesn't look right" in send(c, p, data={"url": "javascript:alert(1)"}).page
    assert "isn't lower than ours" in send(c, p, data={"their_price": f"{price + 5:.2f}"}).page
    assert "Enter your email" in send(c, p, data={"email": "nope"}).page
    assert "Quantity should be" in send(c, p, data={"qty": "50"}).page
    assert "our own site" in send(c, p, data={"url": "https://shop.test/p/x"}).page
    r = send(c, p, data={"url": "", "their_price": "9.99"}, files={"photo": ("x.txt", b"not an image", "text/plain")})
    assert "isn't a photo" in r.page
    # what was typed comes back after an error
    assert 'value="9.99"' in r.text
    assert conn.execute("SELECT count(*) FROM price_matches").fetchone()[0] == 0
    # a bot filling the hidden field gets a thank-you but nothing is saved
    send(c, p, data={"website": "spam.example"})
    assert conn.execute("SELECT count(*) FROM price_matches").fetchone()[0] == 0


def test_full_flow_link_and_photo(shop):
    c, conn = shop.c, shop.conn
    p = pid(c, "LK-100")
    slug, price = slug_price(conn, p)
    theirs = round(price - 3, 2)
    r = send(c, p, data={"their_price": f"{theirs}", "qty": "2"}, files={"photo": ("shot.png", png(), "image/png")})
    assert "we got your request" in r.page and "<details class=\"pmB\" id=\"pricematch\" open>" in r.text
    row = conn.execute("SELECT id, status, qty, url, photo_key, our_price FROM price_matches").fetchone()
    pm_id, status, qty, url, photo_key, ours = row
    assert status == "pending" and qty == 2 and url.startswith("https://www.other-store.com") and re.fullmatch(r"[0-9a-f]{24}\.jpg", photo_key)
    assert ("buyer@example.com", f"We got your price match request (#{pm_id})") in shop.emails
    assert any(to == "owner@example.com" and subj.startswith(f"Price match request #{pm_id}") for to, subj in shop.emails)

    # admin: needs sign-in; sees the request, the link and the private photo
    assert c.get("/admin/price-match/1/photo", follow_redirects=False).status_code in (303, 404)
    c.post("/admin/login", data={"password": "pw"})
    page = c.get("/admin/price-match").text
    assert "Waiting (1)" in page and "other-store.com" in page and f"/admin/price-match/{pm_id}/photo" in page
    ph = c.get(f"/admin/price-match/{pm_id}/photo")
    assert ph.status_code == 200 and ph.headers["content-type"] == "image/jpeg"

    # approve: emailed, token made, held 24 hours
    c.post(f"/admin/price-match/{pm_id}/approve", data={"matched_price": f"{theirs}", "message": "Thanks for shopping with us"})
    assert ("buyer@example.com", "Your price match is approved") in shop.emails
    token, hours = conn.execute("SELECT token, extract(epoch FROM expires_at - decided_at) / 3600 FROM price_matches").fetchone()
    assert token and round(float(hours)) == 24
    assert "Approved (1)" in c.get("/admin/price-match?show=approved").text
    # approving twice does nothing
    c.post(f"/admin/price-match/{pm_id}/approve", data={"matched_price": "1.00"})
    assert float(conn.execute("SELECT matched_price FROM price_matches").fetchone()[0]) == theirs

    # the claim link fills the cart at the matched price for the approved quantity
    r = c.get(f"/price-match/claim/{token}")
    assert "Your price match is in your cart" in r.text and "Price matched for up to 2" in r.text
    assert f"${theirs:,.2f}" in r.text and f"${price:,.2f}" in r.text            # matched, and the regular price crossed out
    c.post("/cart/update", data={"product_id": p, "qty": 5})
    assert "Price matched for up to 2" in c.get("/cart").text                   # held to the approved quantity

    # checkout charges the matched price and records the match
    c.post("/checkout")
    params = shop.created[-1]
    li = params["line_items"][0]
    assert li["quantity"] == 2 and li["price_data"]["unit_amount"] == round(theirs * 100)
    assert params["metadata"]["price_matches"] == str(pm_id)
    order_id = int(params["metadata"]["order_id"])
    events = conn.execute("SELECT events::text FROM orders WHERE id = %s", (order_id,)).fetchone()[0]
    assert f"Price match #{pm_id}" in events

    # paid: the match is used up and the cart goes back to the regular price
    ev = paid_event("cs_test_1", order_id)
    ev["data"]["object"]["metadata"] = params["metadata"]
    post_webhook(c, ev)
    assert conn.execute("SELECT used_order_id FROM price_matches").fetchone()[0] == order_id
    assert "already used" in html.unescape(c.get(f"/price-match/claim/{token}").text)


def test_decline_and_expiry(shop):
    c, conn = shop.c, shop.conn
    p = pid(c, "LK-100")
    slug, price = slug_price(conn, p)
    send(c, p, data={"their_price": f"{price - 1:.2f}"})
    send(c, p, data={"their_price": f"{price - 2:.2f}"})
    a, b = [r[0] for r in conn.execute("SELECT id FROM price_matches ORDER BY id").fetchall()]
    c.post("/admin/login", data={"password": "pw"})
    c.post(f"/admin/price-match/{a}/decline", data={"reason": "That seller isn't authorized"})
    assert ("buyer@example.com", "About your price match request") in shop.emails
    assert conn.execute("SELECT status FROM price_matches WHERE id = %s", (a,)).fetchone()[0] == "declined"

    c.post(f"/admin/price-match/{b}/approve", data={"matched_price": f"{price - 2:.2f}"})
    token = conn.execute("SELECT token FROM price_matches WHERE id = %s", (b,)).fetchone()[0]
    assert "Price matched" in html.unescape(c.get(f"/price-match/claim/{token}").text)
    # 24 hours pass: the cart goes back to the regular price and the link says it expired
    conn.execute("UPDATE price_matches SET expires_at = now() - interval '1 minute' WHERE id = %s", (b,))
    conn.commit()
    page = c.get("/cart").text
    assert "Price matched" not in page and f"${price:,.2f}" in page
    assert "expired" in html.unescape(c.get(f"/price-match/claim/{token}").text)
    assert "isn't valid" in html.unescape(c.get("/price-match/claim/nope").text)


def test_free_shipping_line_mentions_oversized(shop):
    c, conn = shop.c, shop.conn
    c.post("/admin/login", data={"password": "pw"})
    c.post("/admin/ups/free", data={"on": "1", "over": "149", "max_lb": "50", "service": "03", "lower48": "1", "oversize": "1"})
    slug, _ = slug_price(conn, pid(c, "LK-100"))
    assert "excludes oversized items" in c.get(f"/p/{slug}").text


def test_admin_email_shows_recent_sends(shop, monkeypatch):
    from store import mailer
    import smtplib
    c = shop.c
    monkeypatch.setenv("SMTP_HOST", " smtp.example.com ")      # stray spaces from pasting are ignored
    calls = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            calls.append(host)
            self.fail = host != "smtp.example.com"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            pass

        def login(self, u, p):
            pass

        def send_message(self, msg):
            if msg["To"] == "blocked@example.com":
                raise smtplib.SMTPResponseException(450, b"You can only send testing emails to your own email address")
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(mailer, "_deliver", REAL_DELIVER)
    mailer.recent.clear()
    mailer.send("ok@example.com", "Hello", "x", wait=True)
    mailer.send("blocked@example.com", "Hello", "x", wait=True)
    assert calls == ["smtp.example.com", "smtp.example.com"]
    c.post("/admin/login", data={"password": "pw"})
    page = c.get("/admin/email").text
    assert "Recent emails" in page and "ok@example.com" in page and "only send testing emails" in page
