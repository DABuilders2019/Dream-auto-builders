"""Checkout with Stripe (Stripe's hosted payment page), the order confirmation page, order tracking,
and the Stripe webhook that records payments.

Settings: STRIPE_SECRET_KEY (sk_test_... until launch), STRIPE_WEBHOOK_SECRET (whsec_..., from the
webhook endpoint added in the Stripe dashboard for <site>/stripe/webhook), SITE_URL (optional:
the address used in Stripe and email links, e.g. https://dreamautobuilders.com).
"""
import os
import time
from urllib.parse import urlencode

import logging

import stripe
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from . import accounts, installers, mailer, tracking, web
from . import orders as O

router = APIRouter()
log = logging.getLogger("store.checkout")
WEBHOOK_EVENTS = ("checkout.session.completed", "checkout.session.async_payment_succeeded",
                  "checkout.session.async_payment_failed", "checkout.session.expired")


def stripe_key():
    return os.environ.get("STRIPE_SECRET_KEY", "")


def recovery_cfg(cfg=None):
    """Abandoned checkout recovery (config/store.json "cart_recovery")."""
    r = (cfg or O.store_config()).get("cart_recovery") or {}
    hours = max(1, min(23, int(r.get("hours") or 3)))      # Stripe: a checkout page lasts 30 minutes to 24 hours
    return {"enabled": r.get("enabled", True), "hours": hours, "email": r.get("email", True),
            "allow_promotion_codes": bool(r.get("allow_promotion_codes", False))}


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
def checkout(request: Request, ship_state: str = Form(""), save_account: str = Form("")):
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
        acct = accounts.current(request, conn)
        if acct:
            conn.execute("UPDATE orders SET account_id = %s WHERE id = %s", (acct["id"], order_id))
        vid = request.session.get("v") if tracking._ok_to_track(request) else None
        if vid:
            conn.execute("UPDATE orders SET visitor = %s WHERE id = %s", (vid, order_id))
        tracking.record(request, "checkout", number, conn=conn)
        rec = recovery_cfg(cfg)

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
            "metadata": {"order_id": str(order_id), "order_number": number, **({"visitor": vid} if vid else {}),
                         **({"save_account": "1"} if save_account and not acct else {})},
            **_customer_params(conn, acct),
            **({"consent_collection": {"promotions": "auto"},
                "after_expiration": {"recovery": {"enabled": True, "allow_promotion_codes": rec["allow_promotion_codes"]}},
                "expires_at": int(time.time()) + rec["hours"] * 3600} if rec["enabled"] else {}),
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
        allowed = o and ((session_id and session_id == o["stripe_session_id"]) or o["number"] in request.session.get("orders", [])
                         or accounts.owns_order(conn, request, o["number"]))
        if not allowed:
            return web.not_found(request)
        if o["status"] == "pending" and o["stripe_session_id"] and checkout_ready():
            # The webhook may not have arrived yet: ask Stripe directly.
            try:
                sess = stripe.checkout.Session.retrieve(o["stripe_session_id"], api_key=stripe_key())
                sd = sess.to_dict() if hasattr(sess, "to_dict") else dict(sess)
                o2, new = O.complete(conn, sd)
                if new:
                    on_paid(request, conn, o2, sd)
                o = o2 or o
            except stripe.StripeError:
                pass
        if o["status"] in ("paid", "on_hold", "shipped"):
            remember_order(request, o["number"])
            if session_id:
                web.save_cart(request, {})
        for it in o["items"]:
            it["image_url"] = s.storage.url(it["main_image"]) if it.get("main_image") else None
        install = installers.for_order(conn, s, o)
        return web.render(request, conn, s, "order.html", o=o, title=f"Order {o['number']}", states=O.US_STATES,
                          install=install, picked=request.query_params.get("picked"))


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
                on_paid(request, conn, o, s)
            elif o is None and s.get("payment_status") == "paid":
                unmatched_payment(request, s)
        else:
            o = O.get(conn, session_id=s.get("id"))
            if o and o["status"] == "pending":
                if kind == "checkout.session.expired":
                    O.set_status(conn, o["id"], "cancelled", "Checkout abandoned (the Stripe page expired)")
                    conn.execute("UPDATE orders SET abandoned_at = now() WHERE id = %s", (o["id"],))
                    abandoned(request, conn, o, s)
                else:
                    O.set_status(conn, o["id"], "payment_failed", "Payment failed")


def _customer_params(conn, acct):
    """Signed in: check out as the account's Stripe customer, so a saved card and address come back and the shopper
    can choose to save a new card. Otherwise Stripe asks for everything (guest)."""
    if not acct:
        return {}
    cid = acct.get("stripe_customer_id")
    if not cid:
        try:
            c = stripe.Customer.create(api_key=stripe_key(), email=acct["email"], name=acct.get("name") or None,
                                       metadata={"account_id": str(acct["id"])})
            cid = c.id
            conn.execute("UPDATE accounts SET stripe_customer_id = %s WHERE id = %s", (cid, acct["id"]))
        except stripe.StripeError:
            return {"customer_email": acct["email"]}
    return {"customer": cid, "customer_update": {"name": "auto", "address": "auto", "shipping": "auto"},
            "saved_payment_method_options": {"payment_method_save": "enabled"}}


def on_paid(request, conn, o, s):
    send_order_emails(request, o)
    if (s.get("metadata") or {}).get("save_account") == "1":
        try:
            a, made = accounts.save_at_checkout(conn, o, s)
            if made and a:
                base = site_url(request)
                mailer.send(a["email"], "Your Dream Auto Builders account is ready",
                            f"Hi {(a['name'] or '').split(' ')[0] or 'there'},\n\nWe saved your name and email, so you have a "
                            f"Dream Auto Builders account now. There's no password to remember: go to {base}/account, choose "
                            "\"Email me a code\", and we'll send you a code to sign in.\n\nYour orders, garage and saved "
                            "builds are all in your account.")
        except Exception:       # noqa: BLE001 - never lose the payment over this
            log.warning("account at checkout not made", exc_info=True)
    v = (s.get("metadata") or {}).get("visitor") or o.get("visitor")
    if v:
        tracking.record(None, "purchase", o["number"], path="/checkout", conn=conn, visitor_id=v)
    conn.execute("UPDATE checkout_recovery SET recovered_at = now() WHERE order_id = %s AND recovered_at IS NULL", (o["id"],))


def unmatched_payment(request, s):
    """A paid checkout we can't tie to an order: tell the owner rather than lose it."""
    to = mailer.notify_address()
    cd = s.get("customer_details") or {}
    if to:
        mailer.send(to, "Payment received that needs checking", f"Stripe checkout {s.get('id')} was paid "
                    f"({(s.get('amount_total') or 0) / 100:.2f} {(s.get('currency') or 'usd').upper()}) by {cd.get('email')}, "
                    "but no matching order was found. Look it up in the Stripe dashboard.")


def abandoned(request, conn, o, s):
    """Save the abandoned checkout and, if the shopper agreed to marketing emails, email the link back to it."""
    cd = s.get("customer_details") or {}
    rec = ((s.get("after_expiration") or {}).get("recovery") or {})
    consent = (s.get("consent") or {}).get("promotions")
    import datetime as _dt
    exp = _dt.datetime.fromtimestamp(rec["expires_at"], _dt.timezone.utc) if rec.get("expires_at") else None
    conn.execute("""INSERT INTO checkout_recovery (order_id, email, name, consent, recovery_url, recovery_expires)
                    VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (order_id) DO UPDATE SET email = EXCLUDED.email,
                    consent = EXCLUDED.consent, recovery_url = EXCLUDED.recovery_url, recovery_expires = EXCLUDED.recovery_expires""",
                 (o["id"], cd.get("email"), cd.get("name"), consent, rec.get("url"), exp))
    if not (cd.get("email") and rec.get("url") and consent == "opt_in" and recovery_cfg()["email"] and mailer.configured()):
        return
    first = (cd.get("name") or "").split(" ")[0]
    items = "\n".join(f"- {it['qty']} x {it['brand_name']} {it['title']} ({web.money(it['line_total'])})" for it in o["items"])
    text = (f"Hi {first},\n\n" if first else "Hi,\n\n") + \
        "You left some parts in your cart at Dream Auto Builders:\n\n" + items + \
        f"\n\nPick up where you left off (the link works for 30 days):\n{rec['url']}\n\n" \
        "Questions about fitment? Just reply to this email.\n\n" \
        "Don't want these reminders? Reply \"unsubscribe\" and we'll stop.\nDream Auto Builders"
    html = web.templates.get_template("email_recovery.html").render(o=o, first=first, url=rec["url"],
                                                                     site=site_url(request), money=web.money)
    if mailer.send(cd["email"], "You left something in your cart", text, html):
        conn.execute("UPDATE checkout_recovery SET emailed_at = now() WHERE order_id = %s", (o["id"],))
        O.add_event(conn, o["id"], f"Cart reminder emailed to {cd['email']}")


# ------------------------------------------------------------------ emails

def _email(request, o, kind, install=None):
    return web.templates.get_template("email.html").render(o=o, kind=kind, site=site_url(request), money=web.money,
                                                            states=O.US_STATES, install=install)


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
        try:
            install = installers.for_order(conn, st.get_searcher(conn), o)
        except Exception:      # noqa: BLE001 - the shop list is extra: never hold up the confirmation
            install = None
        text = _text(request, o, "confirm")
        if install:
            text += "\n\nNeed it installed? Shops near you (from Google Maps):\n" + "\n".join(
                f"- {x['name']}, {x['address']}" for x in install["shops"]) + "\n" + install["disclaimer"] + \
                f"\nPick the shop you'll use: {site_url(request)}/install/{install['token']}"
        if mailer.send(o["email"], f"Your Dream Auto Builders order {o['number']}", text,
                       _email(request, o, "confirm", install)):
            O.add_event(conn, o["id"], f"Order confirmation emailed to {o['email']}")
        if mailer.notify_address():
            mailer.send(mailer.notify_address(), f"New order {o['number']}" + (" (ON HOLD)" if o["status"] == "on_hold" else "")
                        + f": {web.money(o['total'])}", _text(request, o, "notify"), _email(request, o, "notify"))


def send_shipped_email(request, o):
    if mailer.send(o["email"], f"Your order {o['number']} has shipped", _text(request, o, "shipped"),
                   _email(request, o, "shipped")):
        return True
    return False
