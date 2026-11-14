"""Freight orders: parts held at checkout, owner sends the freight price, customer approves (freight charged, parts
captured) or cancels (hold released); reminders and the automatic cancel. Stripe is faked."""
import datetime as dt
import html
import time

import pytest
import stripe

from store import freight, mailer, shipping, ups
from test_checkout import pid, post_webhook, shop  # noqa: F401  (fixture)
from test_ups import keys  # noqa: F401  (fixture)


@pytest.fixture
def fr(shop, keys, monkeypatch, conn):  # noqa: F811
    shipping._cache.clear()
    monkeypatch.setattr(ups, "rates", lambda *a, **k: [])
    calls = {"create": [], "capture": [], "cancel": [], "retrieve": []}

    def pi_create(api_key=None, **kw):
        calls["create"].append(kw)
        if calls.get("fail_next"):
            calls["fail_next"] = False
            raise stripe.CardError("Your card was declined. This transaction requires authentication.", None,
                                   code="authentication_required")
        return {"id": f"pi_new_{len(calls['create'])}", "status": "succeeded"}

    def pi_retrieve(pid_, api_key=None):
        calls["retrieve"].append(pid_)
        return {"id": pid_, "status": calls.get("hold_status", "requires_capture"), "payment_method": "pm_1", "customer": "cus_1"}

    monkeypatch.setattr(stripe.PaymentIntent, "create", staticmethod(pi_create))
    monkeypatch.setattr(stripe.PaymentIntent, "retrieve", staticmethod(pi_retrieve))
    monkeypatch.setattr(stripe.PaymentIntent, "capture", staticmethod(lambda pid_, api_key=None: calls["capture"].append(pid_)))
    monkeypatch.setattr(stripe.PaymentIntent, "cancel", staticmethod(lambda pid_, api_key=None: calls["cancel"].append(pid_)))
    c = shop.c
    p = pid(c, "LK-100")
    conn.execute("UPDATE products SET weight_lb = 200 WHERE id = %s", (p,))
    conn.commit()
    c.post("/cart/add", data={"product_id": p, "qty": 1})
    c.post("/cart/zip", data={"zip": "78701"})
    shop.calls = calls
    return shop


def held_event(session_id, order_id, total=99995):
    return {"id": "evt_h", "type": "checkout.session.completed", "data": {"object": {
        "id": session_id, "object": "checkout.session", "metadata": {"order_id": str(order_id), "freight": "1"},
        "payment_status": "unpaid", "status": "complete", "payment_intent": "pi_hold", "customer": "cus_1",
        "currency": "usd", "amount_total": total, "total_details": {"amount_tax": 0},
        "customer_details": {"email": "buyer@example.com", "name": "Sam Buyer", "phone": "+15555550100",
                             "address": {"line1": "1 Main St", "city": "Austin", "state": "TX", "postal_code": "78701", "country": "US"}},
        "collected_information": {"shipping_details": {"name": "Sam Buyer", "address": {
            "line1": "1 Main St", "city": "Austin", "state": "TX", "postal_code": "78701", "country": "US"}}}}}}


def reserve(s):
    c = s.c
    cart = html.unescape(c.get("/cart").text)
    assert "Reserve order</button>" in cart and freight.MESSAGE in cart
    r = c.post("/checkout", follow_redirects=False)
    assert r.status_code == 303
    params = s.created[-1]
    oid, number = int(params["metadata"]["order_id"]), params["metadata"]["order_number"]
    post_webhook(c, held_event("cs_test_1", oid))
    time.sleep(0.2)
    return oid, number, params


def status(conn, oid):
    return conn.execute("SELECT status, total, shipping, freight_payment, freight_token FROM orders WHERE id = %s", (oid,)).fetchone()


def test_hold_quote_approve(fr, conn):
    oid, number, params = reserve(fr)
    # checkout: parts only, held not charged, card saved, the message on Stripe's button
    assert "shipping_options" not in params
    assert params["payment_intent_data"]["capture_method"] == "manual"
    assert params["payment_intent_data"]["setup_future_usage"] == "off_session"
    assert params["customer_creation"] == "always" and params["custom_text"]["submit"]["message"] == freight.MESSAGE
    assert status(conn, oid)[0] == "awaiting_freight"
    assert ("buyer@example.com", f"Order {number} reserved: freight quote coming") in fr.emails
    assert any(to == "owner@example.com" and subj.startswith("Freight quote needed") for to, subj in fr.emails)

    page = html.unescape(fr.c.get(f"/order/{number}").text)
    assert "Freight price coming" in page and "You won't be charged until you approve the freight price." in page

    c = fr.c
    c.post("/admin/login", data={"password": "pw"})
    assert "Send freight price to customer" in c.get(f"/admin/orders/{number}").text
    c.post(f"/admin/orders/{number}/freight-quote", data={"amount": "$249.00", "note": "Liftgate delivery"})
    time.sleep(0.2)
    assert status(conn, oid)[0] == "freight_quoted"
    assert ("buyer@example.com", f"Freight price for order {number}: $249.00") in fr.emails
    token = status(conn, oid)[4]
    page = html.unescape(c.get(f"/freight/{token}", follow_redirects=True).text)
    assert "Approve and pay $1,248.95" in page and "Liftgate delivery" in page

    r = c.post(f"/freight/{token}/approve", follow_redirects=False)
    assert "freight=approved" in r.headers["location"]
    st, total, ship, fpay, _ = status(conn, oid)
    assert st == "paid" and float(total) == 1248.95 and float(ship) == 249.0 and fpay == "pi_new_1"
    made = fr.calls["create"][0]
    assert made["amount"] == 24900 and made["off_session"] and made["payment_method"] == "pm_1" and made["customer"] == "cus_1"
    assert fr.calls["capture"] == ["pi_hold"]
    time.sleep(0.2)
    assert ("buyer@example.com", f"Your Dream Auto Builders order {number}") in fr.emails

    # refund covers both charges
    c.post(f"/admin/orders/{number}/refund", data={"confirm": "1"})
    assert [r["payment_intent"] for r in fr.refunds] == ["pi_hold", "pi_new_1"]


def test_bank_wants_confirmation(fr, conn):
    oid, number, _ = reserve(fr)
    fr.c.post("/admin/login", data={"password": "pw"})
    fr.c.post(f"/admin/orders/{number}/freight-quote", data={"amount": "300"})
    token = status(conn, oid)[4]
    fr.calls["fail_next"] = True
    r = fr.c.post(f"/freight/{token}/approve", follow_redirects=False)
    assert r.headers["location"].startswith("https://checkout.stripe.com/")
    sess = fr.created[-1]
    assert sess["metadata"]["kind"] == "freight" and sess["line_items"][0]["price_data"]["unit_amount"] == 30000
    assert status(conn, oid)[0] == "freight_quoted" and fr.calls["capture"] == []
    post_webhook(fr.c, {"id": "evt_f", "type": "checkout.session.completed", "data": {"object": {
        "id": "cs_f", "object": "checkout.session", "metadata": {"order_id": str(oid), "kind": "freight"},
        "payment_status": "paid", "status": "complete", "payment_intent": "pi_freight_page"}}})
    st, total, ship, fpay, _ = status(conn, oid)
    assert st == "paid" and fpay == "pi_freight_page" and float(total) == 1299.95 and fr.calls["capture"] == ["pi_hold"]


def test_customer_cancels(fr, conn):
    oid, number, _ = reserve(fr)
    fr.c.post("/admin/login", data={"password": "pw"})
    fr.c.post(f"/admin/orders/{number}/freight-quote", data={"amount": "300"})
    token = status(conn, oid)[4]
    fr.c.post(f"/freight/{token}/cancel")
    time.sleep(0.2)
    assert status(conn, oid)[0] == "cancelled" and fr.calls["cancel"] == ["pi_hold"]
    assert ("buyer@example.com", f"Order {number} cancelled: hold released") in fr.emails
    page = html.unescape(fr.c.get(f"/order/{number}?freight=cancelled").text)
    assert "Order cancelled" in page and "Total paid" not in page


def test_expired_hold_charges_parts_instead(fr, conn):
    oid, number, _ = reserve(fr)
    fr.c.post("/admin/login", data={"password": "pw"})
    fr.c.post(f"/admin/orders/{number}/freight-quote", data={"amount": "100"})
    fr.calls["hold_status"] = "canceled"
    fr.c.post(f"/freight/{status(conn, oid)[4]}/approve")
    assert status(conn, oid)[0] == "paid" and fr.calls["capture"] == []
    assert [k["amount"] for k in fr.calls["create"]] == [10000, 99995]


def test_reminders_and_auto_cancel(fr, conn):
    oid, number, _ = reserve(fr)
    now = dt.datetime.now(dt.timezone.utc)
    site = "https://shop.test"
    assert freight.run_due(conn, site, now) == []
    assert freight.run_due(conn, site, now + dt.timedelta(hours=25)) == [("owner_reminded", number)]
    assert freight.run_due(conn, site, now + dt.timedelta(hours=26)) == []
    from store import orders as O
    freight.send_quote(conn, O.get(conn, order_id=oid), 200, None, site)
    assert freight.run_due(conn, site, now + dt.timedelta(hours=50)) == [("customer_reminded", number)]
    assert freight.run_due(conn, site, now + dt.timedelta(days=5, minutes=1)) == [("cancelled", number)]
    assert status(conn, oid)[0] == "cancelled" and fr.calls["cancel"] == ["pi_hold"]


def test_email_results_noted_on_order(fr, conn, monkeypatch):
    monkeypatch.setattr(mailer, "_deliver", lambda msg, on_done=None: on_done and on_done(False, "450 domain not verified"))
    oid, number, _ = reserve(fr)
    time.sleep(0.3)
    events = conn.execute("SELECT events FROM orders WHERE id = %s", (oid,)).fetchone()[0]
    assert any("NOT delivered" in e["text"] and "domain not verified" in e["text"] for e in events)
