"""Visit and click tracking, the Analytics page, and abandoned Stripe checkout recovery."""
import html
import time

from test_checkout import WHSEC, order_row, paid_event, pid, post_webhook, shop  # noqa: F401


def kinds(conn):
    return [r[0] for r in conn.execute("SELECT kind FROM site_events ORDER BY id").fetchall()]


def test_page_views_clicks_cart_and_purchase(shop):  # noqa: F811
    c, conn = shop.c, shop.conn
    c.get("/")
    c.get("/search", params={"q": "Lift Kit"})
    lk = pid(c, "LK-100")
    slug = conn.execute("SELECT slug FROM products WHERE id = %s", (lk,)).fetchone()[0]
    c.get(f"/p/{slug}")
    assert c.post("/api/t", json={"t": "home brand: ReadyLift", "p": "/"}).status_code == 204
    c.post("/api/t", json={"t": "<script>", "p": "/"})                 # junk ignored
    c.post("/cart/add", data={"product_id": lk, "qty": 1})
    c.post("/checkout", follow_redirects=False)
    params = shop.created[-1]
    assert params["metadata"]["visitor"] and params["consent_collection"] == {"promotions": "auto"}
    assert params["after_expiration"]["recovery"]["enabled"] is True and params["expires_at"] > time.time() + 3000
    post_webhook(c, paid_event("cs_test_1", int(params["metadata"]["order_id"]), total=100))
    ks = kinds(conn)
    for k in ("page_view", "search", "product_view", "click", "cart_add", "checkout", "purchase"):
        assert k in ks, k
    assert ks.count("click") == 1
    visitors = {r[0] for r in conn.execute("SELECT DISTINCT visitor FROM site_events").fetchall()}
    assert len(visitors) == 1                                           # one shopper, followed through to the purchase
    assert conn.execute("SELECT target FROM site_events WHERE kind = 'search'").fetchone()[0] == "lift kit"

    c.post("/admin/login", data={"password": "pw"})
    page = html.unescape(c.get("/admin/analytics").text)
    assert "Analytics" in page and "lift kit" in page and "home brand: ReadyLift" in page
    n = len(kinds(conn))
    c.get("/")                                                          # the admin isn't counted
    assert len(kinds(conn)) == n


def test_do_not_track_respected(shop):  # noqa: F811
    shop.c.get("/", headers={"Sec-GPC": "1"})
    assert kinds(shop.conn) == []


def test_abandoned_checkout_reminder_and_recovery(shop):  # noqa: F811
    c, conn = shop.c, shop.conn
    c.post("/cart/add", data={"product_id": pid(c, "LK-100"), "qty": 1})
    c.post("/checkout", follow_redirects=False)
    params = shop.created[-1]
    oid, number = int(params["metadata"]["order_id"]), params["metadata"]["order_number"]
    expired = {"id": "evt_x", "type": "checkout.session.expired", "data": {"object": {
        "id": "cs_test_1", "object": "checkout.session", "metadata": params["metadata"], "status": "expired",
        "payment_status": "unpaid", "customer_details": {"email": "maybe@example.com", "name": "Pat Shopper"},
        "consent": {"promotions": "opt_in"},
        "after_expiration": {"recovery": {"enabled": True, "url": "https://buy.stripe.com/r/test_abc", "expires_at": int(time.time()) + 86400}}}}}
    post_webhook(c, expired)
    time.sleep(0.3)
    assert order_row(conn, number)[1] == "cancelled"
    assert ("maybe@example.com", "You left something in your cart") in shop.emails
    r = conn.execute("SELECT email, consent, recovery_url, emailed_at IS NOT NULL FROM checkout_recovery WHERE order_id = %s", (oid,)).fetchone()
    assert r == ("maybe@example.com", "opt_in", "https://buy.stripe.com/r/test_abc", True)

    # the shopper comes back through the link: Stripe makes a new checkout carrying the order number
    ev = paid_event("cs_test_recovered", oid, total=100)
    post_webhook(c, ev)
    assert order_row(conn, number)[1] == "paid"
    assert conn.execute("SELECT recovered_at IS NOT NULL FROM checkout_recovery WHERE order_id = %s", (oid,)).fetchone()[0]

    c.post("/admin/login", data={"password": "pw"})
    page = html.unescape(c.get("/admin/analytics").text)
    assert number in page and "maybe@example.com" in page and "Paid" in page


def test_no_consent_no_email(shop):  # noqa: F811
    c = shop.c
    c.post("/cart/add", data={"product_id": pid(c, "LK-100"), "qty": 1})
    c.post("/checkout", follow_redirects=False)
    params = shop.created[-1]
    post_webhook(c, {"id": "evt_y", "type": "checkout.session.expired", "data": {"object": {
        "id": "cs_test_1", "object": "checkout.session", "metadata": params["metadata"], "status": "expired",
        "customer_details": {"email": "nope@example.com"}, "consent": {"promotions": "opt_out"},
        "after_expiration": {"recovery": {"url": "https://buy.stripe.com/r/x", "expires_at": int(time.time()) + 99}}}}})
    time.sleep(0.2)
    assert not any(to == "nope@example.com" for to, _ in shop.emails)
