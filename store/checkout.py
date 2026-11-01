"""Checkout with Stripe (Stripe's hosted payment page), the order confirmation page, order tracking,
and the Stripe webhook that records payments.

Settings: STRIPE_SECRET_KEY (sk_test_... until launch), STRIPE_WEBHOOK_SECRET (whsec_..., from the
webhook endpoint added in the Stripe dashboard for <site>/stripe/webhook), SITE_URL (optional:
the address used in Stripe and email links, e.g. https://dreamautobuilders.com).
"""
import os
from urllib.parse import urlencode

import stripe
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from . import mailer, web
from . import orders as O

router = APIRouter()
WEBHOOK_EVENTS = ("checkout.session.completed", "checkout.session.async_payment_succeeded",
                  "checkout.session.async_payment_failed", "checkout.session.expired")


def stripe_key():
    return os.environ.get("STRIPE_SECRET_KEY", "")


def checkout_ready():
    return bool(stripe_key())


def site_url(request):
    return (os.environ.get("SITE_URL") or str(request.base_url)).rstrip("/")


def _cart_redirect(error):
    return RedirectResponse("/cart?" + urlencode({"error": error}), status_code=303)


def remember_order(request, number):
    seen = [n for n in request.session.get("orders", []) if n != number][-9:]
    request.session["orders"] = seen + [number]


# ------------------------------------------------------------------ start checkout

@router.post("/checkout")
def checkout(request: Request, ship_state: str = Form("")):
    if not checkout_ready():
        return _cart_redirect("Checkout isn't switched on yet.")
    st = web._state(request)
    cfg = O.store_config()
    with st.pool.connection() as conn:
        s = st.get_searcher(conn)
        cart, prods, count, subtotal = web.cart_summary(conn, s, request)
        with conn.cursor() as cur:
            cur.execute("SELECT id, emissions FROM products WHERE id = ANY(%s)", (list(prods),))
            emissions = dict(cur.fetchall())
        lines = [{"product_id": pid, "slug": prods[pid]["slug"], "brand_name": prods[pid]["brand_name"],
                  "part_number": prods[pid]["part_number"], "title": prods[pid]["title"], "qty": q,
                  "unit_price": prods[pid]["price"], "emissions": emissions.get(pid), "image_url": prods[pid].get("image_url")}
                 for pid, q in cart.items() if pid in prods]
        if not lines:
            return _cart_redirect("Your cart is empty.")
        state = ship_state.strip().upper()
        if any(l["emissions"] == "federal_only" for l in lines):
            if state not in O.US_STATES:
                return _cart_redirect("Choose the state you're shipping to. Some catalytic converters can't ship to every state.")
            bad = O.carb_problem(lines, state, cfg)
            if bad:
                return _cart_redirect(f"{O.US_STATES[state]} only allows CARB-approved catalytic converters. Remove "
                                      + ", ".join(f"{b['brand_name']} {b['part_number']}" for b in bad) + " to continue.")
        aid = web.active_id(request)
        vehicle = next((v["label"] for v in web.garage_vehicles(conn, request) if v["id"] == aid), None)
        order_id, number = O.create(conn, lines, vehicle, note=f"shipping to {state}" if state else None)

        base = site_url(request)
        ship = O.shipping_for(subtotal, cfg)
        params = {
            "mode": "payment",
            "line_items": [{
                "quantity": l["qty"],
                "price_data": {
                    "currency": cfg["currency"],
                    "unit_amount": round(l["unit_price"] * 100),
                    "product_data": {"name": l["title"][:250],
                                     "description": f"{l['brand_name']} part # {l['part_number']}",
                                     "metadata": {"product_id": str(l["product_id"])},
                                     **({"images": [base + l["image_url"]]} if l["image_url"] and l["image_url"].startswith("/") else {})},
                }} for l in lines],
            "shipping_address_collection": {"allowed_countries": cfg["ship_to_countries"]},
            "shipping_options": [{"shipping_rate_data": {
                "type": "fixed_amount", "display_name": "Free shipping" if ship == 0 else "Standard shipping",
                "fixed_amount": {"amount": round(ship * 100), "currency": cfg["currency"]},
                "delivery_estimate": {"minimum": {"unit": "business_day", "value": 3},
                                      "maximum": {"unit": "business_day", "value": 7}}}}],
            "phone_number_collection": {"enabled": True},
            "automatic_tax": {"enabled": bool(cfg.get("automatic_tax"))},
            "client_reference_id": number,
            "metadata": {"order_id": str(order_id), "order_number": number},
            "payment_intent_data": {"metadata": {"order_id": str(order_id), "order_number": number},
                                    "description": f"Dream Auto Builders order {number}"},
            "success_url": f"{base}/order/{number}?session_id={{CHECKOUT_SESSION_ID}}",
            "cancel_url": f"{base}/cart?cancelled=1",
        }
        try:
            session = stripe.checkout.Session.create(api_key=stripe_key(), **params)
        except stripe.StripeError as e:
            O.set_status(conn, order_id, "cancelled", f"Stripe refused the checkout: {e.user_message or e}")
            return _cart_redirect("The payment page couldn't be opened. Please try again in a minute.")
        O.set_session(conn, order_id, session.id)
    remember_order(request, number)
    return RedirectResponse(session.url, status_code=303)


# ------------------------------------------------------------------ after payment

@router.get("/order/{number}", response_class=HTMLResponse)
def order_page(request: Request, number: str, session_id: str = ""):
    st = web._state(request)
    with st.pool.connection() as conn:
        s = st.get_searcher(conn)
        o = O.get(conn, number=number.upper())
        allowed = o and ((session_id and session_id == o["stripe_session_id"]) or o["number"] in request.session.get("orders", []))
        if not allowed:
            return web.not_found(request)
        if o["status"] == "pending" and o["stripe_session_id"] and checkout_ready():
            # The webhook may not have arrived yet: ask Stripe directly.
            try:
                sess = stripe.checkout.Session.retrieve(o["stripe_session_id"], api_key=stripe_key())
                o2, new = O.complete(conn, sess.to_dict() if hasattr(sess, "to_dict") else dict(sess))
                if new:
                    send_order_emails(request, o2)
                o = o2 or o
            except stripe.StripeError:
                pass
        if o["status"] in ("paid", "on_hold", "shipped"):
            remember_order(request, o["number"])
            if session_id:
                web.save_cart(request, {})
        for it in o["items"]:
            it["image_url"] = s.storage.url(it["main_image"]) if it.get("main_image") else None
        return web.render(request, conn, s, "order.html", o=o, title=f"Order {o['number']}", states=O.US_STATES)


@router.post("/track")
def track(request: Request, number: str = Form(""), email: str = Form("")):
    st = web._state(request)
    with st.pool.connection() as conn:
        o = O.lookup(conn, number, email)
    if not o:
        return RedirectResponse("/track?" + urlencode({"error": "1", "number": number}), status_code=303)
    remember_order(request, o["number"])
    return RedirectResponse(f"/order/{o['number']}", status_code=303)


# ------------------------------------------------------------------ Stripe webhook

@router.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    if not secret:
        return JSONResponse({"error": "webhook secret not set"}, status_code=503)
    payload = await request.body()
    try:
        event = stripe.Webhook.construct_event(payload, request.headers.get("stripe-signature"), secret)
    except (ValueError, stripe.SignatureVerificationError):
        return JSONResponse({"error": "bad signature"}, status_code=400)
    await run_in_threadpool(handle_event, request, event)
    return {"received": True}


def handle_event(request, event):
    kind = event["type"]
    if kind not in WEBHOOK_EVENTS:
        return
    obj = event["data"]["object"]
    s = obj.to_dict() if hasattr(obj, "to_dict") else dict(obj)
    st = web._state(request)
    with st.pool.connection() as conn:
        if kind in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
            o, new = O.complete(conn, s)
            if new:
                send_order_emails(request, o)
        else:
            o = O.get(conn, session_id=s.get("id"))
            if o and o["status"] == "pending":
                if kind == "checkout.session.expired":
                    O.set_status(conn, o["id"], "cancelled", "Checkout abandoned (the Stripe page expired)")
                else:
                    O.set_status(conn, o["id"], "payment_failed", "Payment failed")


# ------------------------------------------------------------------ emails

def _email(request, o, kind):
    return web.templates.get_template("email.html").render(o=o, kind=kind, site=site_url(request), money=web.money,
                                                            states=O.US_STATES)


def _text(request, o, kind):
    lines = []
    if kind == "confirm":
        lines += [f"Thanks for your order, {o['name'] or ''}".strip() + ".", "",
                  f"Order {o['number']}. We'll email you the tracking number as soon as it ships.", ""]
    elif kind == "shipped":
        lines += [f"Your order {o['number']} has shipped.", f"Carrier: {o['carrier']}", f"Tracking number: {o['tracking_number']}", ""]
    else:
        lines += [f"New order {o['number']} from {o['name']} <{o['email']}>, {o['phone'] or ''}",
                  *(["ON HOLD: " + o["hold_reason"]] if o["hold_reason"] else []), ""]
    for it in o["items"]:
        lines.append(f"{it['qty']} x {it['brand_name']} {it['part_number']}  {it['title']}  {web.money(it['line_total'])}")
    lines += ["", f"Subtotal {web.money(o['subtotal'])}", f"Shipping {web.money(o['shipping'] or 0)}",
              f"Tax {web.money(o['tax'] or 0)}", f"Total {web.money(o['total'])}", ""]
    a = o["ship_address"] or {}
    lines += ["Ship to:", a.get("name") or "", a.get("line1") or "", *( [a["line2"]] if a.get("line2") else []),
              f"{a.get('city', '')}, {a.get('state', '')} {a.get('postal_code', '')}", "",
              f"Order status: {site_url(request)}/track"]
    return "\n".join(lines)


def send_order_emails(request, o):
    st = web._state(request)
    with st.pool.connection() as conn:
        if not mailer.configured():
            O.add_event(conn, o["id"], "Emails not sent: email isn't set up yet (SMTP settings)")
            return
        if mailer.send(o["email"], f"Your Dream Auto Builders order {o['number']}", _text(request, o, "confirm"),
                       _email(request, o, "confirm")):
            O.add_event(conn, o["id"], f"Order confirmation emailed to {o['email']}")
        if mailer.notify_address():
            mailer.send(mailer.notify_address(), f"New order {o['number']}" + (" (ON HOLD)" if o["status"] == "on_hold" else "")
                        + f": {web.money(o['total'])}", _text(request, o, "notify"), _email(request, o, "notify"))


def send_shipped_email(request, o):
    if mailer.send(o["email"], f"Your order {o['number']} has shipped", _text(request, o, "shipped"),
                   _email(request, o, "shipped")):
        return True
    return False
