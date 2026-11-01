"""Checkout: order saved, Stripe payment page opened (Stripe is faked here), the webhook records the
payment, the shopper's confirmation page, CARB shipping rules, order tracking, emails, and the admin
order page (ship, release a hold, refund)."""
import hashlib
import hmac
import html
import json
import time
from types import SimpleNamespace

import pytest
import stripe
from fastapi.testclient import TestClient

from conftest import make_pies, make_vehicles
from importer import load
from store import mailer
from test_search import FITMENT, ITEMS

WHSEC = "whsec_test"


@pytest.fixture
def shop(conn, tmp_path, monkeypatch):
    load.load_brand(conn, "testbrand", make_pies(tmp_path / "p.xml", ITEMS), None,
                    make_vehicles(tmp_path / "v.xlsx", FITMENT), log=lambda *a: None)
    for k, v in {"DATA_DIR": str(tmp_path / "data"), "PHOTO_DIR": str(tmp_path / "photos"), "SECRET_KEY": "s",
                 "ADMIN_PASSWORD": "pw", "STRIPE_SECRET_KEY": "sk_test_fake", "STRIPE_WEBHOOK_SECRET": WHSEC,
                 "SMTP_HOST": "smtp.example.com", "MAIL_FROM": "orders@example.com", "ORDER_NOTIFY_EMAIL": "owner@example.com"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("SITE_PASSWORD", raising=False)

    created, refunds, emails = [], [], []

    def fake_create(api_key=None, **params):
        created.append(params)
        return SimpleNamespace(id=f"cs_test_{len(created)}", url=f"https://checkout.stripe.com/c/pay/cs_test_{len(created)}")

    monkeypatch.setattr(stripe.checkout.Session, "create", staticmethod(fake_create))
    monkeypatch.setattr(stripe.checkout.Session, "retrieve", staticmethod(
        lambda sid, api_key=None: {"id": sid, "payment_status": "unpaid", "status": "open", "metadata": {}}))
    monkeypatch.setattr(stripe.Refund, "create", staticmethod(lambda **kw: refunds.append(kw) or {"id": "re_1"}))
    monkeypatch.setattr(mailer, "_deliver", lambda msg: emails.append((msg["To"], msg["Subject"])))

    from store.app import create_app
    app = create_app(conn.info.dsn)
    with TestClient(app, base_url="https://shop.test") as c:
        yield SimpleNamespace(c=c, created=created, refunds=refunds, emails=emails, conn=conn)


def pid(c, pn):
    return next(it["id"] for it in c.get("/api/search", params={"q": pn}).json()["items"] if it["part_number"] == pn)


def paid_event(session_id, order_id, state="TX", total=0):
    return {"id": "evt_1", "type": "checkout.session.completed", "data": {"object": {
        "id": session_id, "object": "checkout.session", "metadata": {"order_id": str(order_id)},
        "payment_status": "paid", "status": "complete", "payment_intent": "pi_123", "currency": "usd",
        "amount_total": total, "total_details": {"amount_tax": 0}, "shipping_cost": {"amount_total": 0},
        "customer_details": {"email": "buyer@example.com", "name": "Sam Buyer", "phone": "+15555550100",
                             "address": {"line1": "1 Main St", "city": "Austin", "state": state, "postal_code": "78701", "country": "US"}},
        "collected_information": {"shipping_details": {"name": "Sam Buyer", "address": {
            "line1": "1 Main St", "city": "Austin", "state": state, "postal_code": "78701", "country": "US"}}}}}}


def post_webhook(c, event, secret=WHSEC):
    body = json.dumps(event)
    ts = int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.{body}".encode(), hashlib.sha256).hexdigest()
    return c.post("/stripe/webhook", content=body, headers={"stripe-signature": f"t={ts},v1={sig}", "content-type": "application/json"})


def order_row(conn, number):
    return conn.execute("SELECT id, status, total, email, hold_reason, jsonb_array_length(events) FROM orders WHERE number = %s",
                        (number,)).fetchone()


def test_checkout_payment_and_confirmation(shop):
    c = shop.c
    lk = pid(c, "LK-100")
    c.post("/cart/add", data={"product_id": lk, "qty": 1})
    r = c.post("/checkout", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("https://checkout.stripe.com/")
    params = shop.created[0]
    item = params["line_items"][0]
    assert item["price_data"]["unit_amount"] == 99995 and item["quantity"] == 1
    assert params["shipping_options"][0]["shipping_rate_data"]["fixed_amount"]["amount"] == 0     # over $149: free
    assert params["shipping_address_collection"]["allowed_countries"] == ["US"]
    number = params["metadata"]["order_number"]
    assert params["success_url"] == f"https://shop.test/order/{number}?session_id={{CHECKOUT_SESSION_ID}}"
    oid = int(params["metadata"]["order_id"])
    assert order_row(shop.conn, number)[1] == "pending"

    assert post_webhook(c, paid_event("cs_test_1", oid, total=99995)).json() == {"received": True}
    row = order_row(shop.conn, number)
    assert row[1] == "paid" and float(row[2]) == 999.95 and row[3] == "buyer@example.com"
    time.sleep(0.3)
    assert ("buyer@example.com", f"Your Dream Auto Builders order {number}") in shop.emails
    assert any(to == "owner@example.com" for to, _ in shop.emails)

    events_before = row[5]
    post_webhook(c, paid_event("cs_test_1", oid, total=99995))         # Stripe may send it twice
    assert order_row(shop.conn, number)[5] == events_before

    page = html.unescape(c.get(f"/order/{number}", params={"session_id": "cs_test_1"}).text)
    assert "Thank you, Sam!" in page and number in page and "1 Main St" in page
    assert "Your cart is empty" in c.get("/cart").text                 # cart cleared after paying


def test_small_order_pays_flat_shipping(shop):
    c = shop.c
    c.post("/cart/add", data={"product_id": pid(c, "35116"), "qty": 1})
    c.post("/checkout", follow_redirects=False)
    rate = shop.created[0]["shipping_options"][0]["shipping_rate_data"]
    assert rate["fixed_amount"]["amount"] == 1495 and rate["display_name"] == "Standard shipping"


def test_bad_webhook_signature_is_refused(shop):
    assert post_webhook(shop.c, paid_event("cs_x", 1), secret="whsec_wrong").status_code == 400


def test_order_page_is_private(shop):
    c = shop.c
    c.post("/cart/add", data={"product_id": pid(c, "LK-100")})
    c.post("/checkout", follow_redirects=False)
    number = shop.created[0]["metadata"]["order_number"]
    other = TestClient(c.app, base_url="https://shop.test")
    assert other.get(f"/order/{number}").status_code == 404
    assert other.get(f"/order/{number}", params={"session_id": "cs_wrong"}).status_code == 404


def test_carb_rules(shop):
    c = shop.c
    cat2 = pid(c, "CAT-2")           # federal-only catalytic converter
    c.post("/cart/add", data={"product_id": cat2})
    cart = html.unescape(c.get("/cart").text)
    assert "Shipping to which state?" in cart and "California" in cart
    r = c.post("/checkout", follow_redirects=False)
    assert "error=" in r.headers["location"] and not shop.created            # state required
    r = c.post("/checkout", data={"ship_state": "CA"})
    assert "only allows CARB-approved" in html.unescape(r.text) and not shop.created
    r = c.post("/checkout", data={"ship_state": "TX"}, follow_redirects=False)
    assert r.status_code == 303 and shop.created
    # shopper picked Texas but entered a California address at Stripe: paid, but held for review
    oid = int(shop.created[0]["metadata"]["order_id"])
    post_webhook(c, paid_event("cs_test_1", oid, state="CA", total=30000))
    row = order_row(shop.conn, shop.created[0]["metadata"]["order_number"])
    assert row[1] == "on_hold" and "CAT-2" in row[4]


def test_track_and_admin_fulfilment(shop):
    c = shop.c
    c.post("/cart/add", data={"product_id": pid(c, "LK-100")})
    c.post("/checkout", follow_redirects=False)
    number = shop.created[0]["metadata"]["order_number"]
    post_webhook(c, paid_event("cs_test_1", int(shop.created[0]["metadata"]["order_id"]), total=99995))

    t = TestClient(c.app, base_url="https://shop.test")
    r = t.post("/track", data={"number": number.lower(), "email": "BUYER@example.com"}, follow_redirects=False)
    assert r.headers["location"] == f"/order/{number}"
    assert "Paid, being prepared" in t.get(f"/order/{number}").text
    assert "couldn" in t.post("/track", data={"number": number, "email": "x@example.com"}).text

    c.post("/admin/login", data={"password": "pw"})
    assert number in c.get("/admin/orders").text
    assert "Mark as shipped" in c.get(f"/admin/orders/{number}").text
    c.post(f"/admin/orders/{number}/ship", data={"carrier": "UPS", "tracking": "1Z999", "notify": "1"})
    assert order_row(shop.conn, number)[1] == "shipped"
    time.sleep(0.3)
    assert ("buyer@example.com", f"Your order {number} has shipped") in shop.emails
    assert "1Z999" in t.get(f"/order/{number}").text

    r = c.post(f"/admin/orders/{number}/refund", data={})
    assert "Tick the box" in html.unescape(r.text) and not shop.refunds
    c.post(f"/admin/orders/{number}/refund", data={"confirm": "1"})
    assert shop.refunds == [{"payment_intent": "pi_123", "api_key": "sk_test_fake", "metadata": {"order_number": number}}]
    assert order_row(shop.conn, number)[1] == "refunded"


def test_checkout_off_without_stripe_key(shop, monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "")
    c = shop.c
    c.post("/cart/add", data={"product_id": pid(c, "LK-100")})
    assert "switched on yet" in html.unescape(c.get("/cart").text)
    assert "switched on yet" in html.unescape(c.post("/checkout").text)
