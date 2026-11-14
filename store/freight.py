"""Freight orders: parts too big for UPS (store/shipping.py "big").

1. Checkout: Stripe places a HOLD for the parts (capture_method=manual) and saves the card (setup_future_usage=off_session).
   No money moves. The order becomes "awaiting_freight"; the owner gets an email.
2. Admin > Orders > the order: the owner enters the freight price. The order becomes "freight_quoted" and the customer
   gets an email with Approve and Cancel buttons (/freight/<token>).
3. Approve: freight is charged to the saved card, then the parts hold is captured; the order becomes "paid" and the
   normal confirmation emails go out. If the bank wants the customer to confirm the freight charge, they're sent to a
   Stripe payment page for it instead, and the webhook finishes the job.
4. Cancel, or no answer: the hold is released and nothing is charged. Card holds last about 7 days, so orders still
   waiting CANCEL_AFTER_DAYS after the hold are cancelled automatically (reminders go out before that).
"""
import datetime as dt
import logging
import os
import secrets
import threading

import stripe
from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from . import mailer, web
from . import orders as O

router = APIRouter()
log = logging.getLogger("store.freight")

MESSAGE = "We'll place a temporary hold on your card for the parts. You won't be charged until you approve the freight price."
CANCEL_AFTER_DAYS = 5          # card holds last about 7 days (Visa merchant-initiated: under 5); stay inside that
OWNER_REMIND_HOURS = 24        # quote not sent yet
CUSTOMER_REMIND_HOURS = 48     # quote sent, not answered
OPEN = ("awaiting_freight", "freight_quoted")


def _key():
    return os.environ.get("STRIPE_SECRET_KEY", "")


def _g(obj, k, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(k, default)
    return getattr(obj, k, default)


def new_token():
    return secrets.token_urlsafe(18)


# ------------------------------------------------------------------ checkout

def checkout_params(params, has_customer):
    """Turn a normal Stripe Checkout request into a freight one: parts only, held not charged, card saved."""
    params = dict(params)
    for k in ("shipping_options", "consent_collection", "after_expiration", "expires_at"):
        params.pop(k, None)
    pid = dict(params.get("payment_intent_data") or {})
    pid["capture_method"] = "manual"
    pid["setup_future_usage"] = "off_session"
    pid["description"] = (pid.get("description") or "Dream Auto Builders order") + " (parts held until freight is approved)"
    params["payment_intent_data"] = pid
    if not has_customer:
        params["customer_creation"] = "always"      # needed to keep the card for the freight charge
    params["custom_text"] = {"submit": {"message": MESSAGE}}
    params["metadata"] = {**(params.get("metadata") or {}), "freight": "1"}
    return params


# ------------------------------------------------------------------ emails

def _site(request=None):
    if os.environ.get("SITE_URL"):
        return os.environ["SITE_URL"].rstrip("/")
    if request is not None:
        return str(request.base_url).rstrip("/")
    return (os.environ.get("RENDER_EXTERNAL_URL") or "").rstrip("/")


def _email(kind, o, site, **extra):
    """(subject, text, html) for the freight emails."""
    first = (o.get("name") or "").split(" ")[0] or "there"
    link = f"{site}/freight/{o['freight_token']}"
    items = "\n".join(f"- {it['qty']} x {it['brand_name']} {it['title']} ({web.money(it['line_total'])})" for it in o["items"])
    parts = web.money(o["total"] or o["subtotal"])
    quote = web.money(o.get("freight_quote") or 0)
    new_total = web.money(round((o["total"] or o["subtotal"]) + float(o.get("freight_quote") or 0), 2))
    if kind == "reserved":
        subject = f"Order {o['number']} reserved: freight quote coming"
        body = (f"Hi {first},\n\nThanks for your order. Part of it is too big for UPS, so it ships by freight. We're getting "
                "the freight price for your address and will email it to you, usually within 1 business day.\n\n"
                f"We placed a temporary hold of {parts} on your card for the parts. You won't be charged until you approve "
                "the freight price, and if you decide not to go ahead, the hold is released.\n\n"
                f"{items}\n\nOrder {o['number']}: {link}")
        button = None
    elif kind in ("quote", "reminder"):
        subject = (f"Freight price for order {o['number']}: {quote}" if kind == "quote"
                   else f"Reminder: approve the freight price for order {o['number']}")
        body = (f"Hi {first},\n\n" + ("Just a reminder: " if kind == "reminder" else "") +
                f"freight shipping for your order to your address is {quote}."
                + (f" ({o['freight_note']})" if o.get("freight_note") else "") +
                f"\n\nParts: {parts}\nFreight: {quote}\nNew total: {new_total}\n\n"
                f"Approve it here and we'll charge your card and place your order:\n{link}\n\n"
                "Not what you expected? You can cancel on the same page and the hold on your card is released. "
                f"If we don't hear back within {CANCEL_AFTER_DAYS} days of your order, it's cancelled and the hold released "
                "automatically.\n\nQuestions? Just reply to this email.")
        button = ("Approve or cancel", link)
    elif kind == "cancelled":
        subject = f"Order {o['number']} cancelled: hold released"
        body = (f"Hi {first},\n\nOrder {o['number']} has been cancelled"
                + (f" ({extra['why']})" if extra.get("why") else "") +
                ". The hold on your card has been released and you weren't charged. Depending on your bank, the pending "
                "amount can take a few days to disappear.\n\nIf you'd still like the parts, just reply and we'll help.")
        button = None
    elif kind == "owner_new":
        subject = f"Freight quote needed: order {o['number']} ({parts} held)"
        a = o.get("ship_address") or {}
        body = (f"Order {o['number']} has parts too big for UPS. {parts} is held on the customer's card (not charged).\n\n"
                f"{items}\n\nShip to: {a.get('name') or o.get('name') or ''}, {a.get('line1') or ''} {a.get('city') or ''}, "
                f"{a.get('state') or ''} {a.get('postal_code') or ''}\n\nGet the freight price and enter it here:\n"
                f"{site}/admin/orders/{o['number']}\n\nThe hold lasts about a week; the order cancels itself after "
                f"{CANCEL_AFTER_DAYS} days if the customer hasn't approved a price by then.")
        button = ("Enter the freight price", f"{site}/admin/orders/{o['number']}")
    elif kind == "owner_remind":
        subject = f"Still waiting: freight price for order {o['number']}"
        body = (f"Order {o['number']} is still waiting for a freight price. The customer's hold cancels automatically "
                f"{CANCEL_AFTER_DAYS} days after the order.\n\n{site}/admin/orders/{o['number']}")
        button = ("Enter the freight price", f"{site}/admin/orders/{o['number']}")
    elif kind == "owner_problem":
        subject = f"Freight order {o['number']} needs attention"
        body = f"Order {o['number']}: {extra.get('why')}\n\n{site}/admin/orders/{o['number']}"
        button = ("Open the order", f"{site}/admin/orders/{o['number']}")
    else:
        raise ValueError(kind)
    html = web.templates.get_template("email_freight.html").render(body=body, button=button, subject=subject)
    return subject, body, html


def _send(conn, o, kind, to, site, **extra):
    if not to:
        return
    if not mailer.configured():
        O.add_event(conn, o["id"], f"Freight email to {to} not sent: email isn't set up yet (SMTP settings)")
        return
    subject, text, html = _email(kind, o, site, **extra)

    def done(ok, err):        # runs once the email service answers
        if not _POOL:
            return
        try:
            with _POOL[0].connection() as c2:
                O.add_event(c2, o["id"], f"Emailed '{subject}' to {to}" if ok else f"Email '{subject}' NOT delivered to {to}: {err}")
        except Exception:      # noqa: BLE001
            log.warning("freight email note not saved", exc_info=True)
    mailer.send(to, subject, text, html, on_done=done)


_POOL = []      # the app's connection pool, set by launch(), so email results can be noted from the mail thread


# ------------------------------------------------------------------ steps

def on_reserved(conn, o, site):
    """The hold is in place: tell the customer and the owner."""
    _send(conn, o, "reserved", o.get("email"), site)
    if mailer.notify_address():
        _send(conn, o, "owner_new", mailer.notify_address(), site)
    else:
        O.add_event(conn, o["id"], "Freight quote request not emailed: ORDER_NOTIFY_EMAIL isn't set")


def send_quote(conn, o, amount, note, site):
    conn.execute("""UPDATE orders SET status = 'freight_quoted', freight_quote = %s, freight_note = %s, freight_quoted_at = now(),
                        freight_reminded_at = NULL, updated_at = now() WHERE id = %s""", (amount, note or None, o["id"]))
    O.add_event(conn, o["id"], f"Freight price {web.money(amount)} sent to the customer" + (f" ({note})" if note else ""))
    o = O.get(conn, order_id=o["id"])
    _send(conn, o, "quote", o.get("email"), site)
    return o


def cancel(conn, o, why, site, notify_customer=True):
    """Release the hold and cancel. Safe to call twice."""
    if o["status"] not in OPEN:
        return o
    if o.get("payment_intent") and _key():
        try:
            stripe.PaymentIntent.cancel(o["payment_intent"], api_key=_key())
        except stripe.StripeError as e:     # already released or expired: nothing is held either way
            log.info("cancel %s: %s", o["payment_intent"], e)
    O.set_status(conn, o["id"], "cancelled", f"Freight order cancelled ({why}); hold released, nothing charged")
    o = O.get(conn, order_id=o["id"])
    if notify_customer:
        _send(conn, o, "cancelled", o.get("email"), site, why=why)
    return o


def _intent(pi_id):
    return stripe.PaymentIntent.retrieve(pi_id, api_key=_key())


def approve(conn, o, site, request=None):
    """Charge the freight, capture the parts. Returns ("paid", None), ("pay", stripe_url) when the bank wants the
    customer to confirm the freight charge, or ("problem", message)."""
    if o["status"] != "freight_quoted":
        return ("problem", "This order isn't waiting for approval.")
    amount = round(float(o["freight_quote"] or 0) * 100)
    pi = _intent(o["payment_intent"])
    pm = _g(pi, "payment_method")
    pm = _g(pm, "id", pm)
    customer = _g(pi, "customer") or o.get("stripe_customer")
    customer = _g(customer, "id", customer)
    if amount > 0 and not o.get("freight_payment"):
        try:
            fp = stripe.PaymentIntent.create(
                api_key=_key(), amount=amount, currency=o.get("currency") or "usd", customer=customer, payment_method=pm,
                off_session=True, confirm=True, description=f"Dream Auto Builders order {o['number']}: freight shipping",
                metadata={"order_id": str(o["id"]), "order_number": o["number"], "kind": "freight"})
            conn.execute("UPDATE orders SET freight_payment = %s WHERE id = %s", (_g(fp, "id"), o["id"]))
            O.add_event(conn, o["id"], f"Freight {web.money(amount / 100)} charged to the saved card")
        except stripe.CardError as e:
            O.add_event(conn, o["id"], f"Saved card needs the customer to confirm the freight charge ({e.code or e.user_message})")
            sess = stripe.checkout.Session.create(
                api_key=_key(), mode="payment", customer=customer,
                line_items=[{"quantity": 1, "price_data": {"currency": o.get("currency") or "usd", "unit_amount": amount,
                                                           "product_data": {"name": f"Freight shipping, order {o['number']}"}}}],
                payment_intent_data={"metadata": {"order_id": str(o["id"]), "order_number": o["number"], "kind": "freight"},
                                     "description": f"Dream Auto Builders order {o['number']}: freight shipping"},
                metadata={"order_id": str(o["id"]), "order_number": o["number"], "kind": "freight"},
                success_url=f"{site}/freight/{o['freight_token']}?paid=1", cancel_url=f"{site}/freight/{o['freight_token']}")
            return ("pay", _g(sess, "url"))
    return finish(conn, O.get(conn, order_id=o["id"]), site, pi=pi, request=request)


def finish(conn, o, site, pi=None, request=None):
    """Freight is paid (or free): capture the parts hold and mark the order paid."""
    from . import checkout
    pi = pi or _intent(o["payment_intent"])
    status = _g(pi, "status")
    try:
        if status == "requires_capture":
            stripe.PaymentIntent.capture(o["payment_intent"], api_key=_key())
        elif status != "succeeded":      # the hold expired or was released: charge the parts now instead
            pm = _g(pi, "payment_method")
            new = stripe.PaymentIntent.create(
                api_key=_key(), amount=round((o["total"] or o["subtotal"]) * 100), currency=o.get("currency") or "usd",
                customer=_g(_g(pi, "customer"), "id", _g(pi, "customer")) or o.get("stripe_customer"), payment_method=_g(pm, "id", pm),
                off_session=True, confirm=True, description=f"Dream Auto Builders order {o['number']} (parts)",
                metadata={"order_id": str(o["id"]), "order_number": o["number"], "kind": "parts"})
            conn.execute("UPDATE orders SET payment_intent = %s WHERE id = %s", (_g(new, "id"), o["id"]))
            O.add_event(conn, o["id"], "Parts hold had expired, so the parts were charged to the saved card instead")
    except stripe.StripeError as e:
        why = f"the freight was paid but the parts couldn't be charged ({getattr(e, 'user_message', None) or e}). Contact the customer."
        O.set_status(conn, o["id"], "on_hold", "ON HOLD: " + why, hold_reason=why[:300])
        o = O.get(conn, order_id=o["id"])
        if mailer.notify_address():
            _send(conn, o, "owner_problem", mailer.notify_address(), site, why=why)
        return ("problem", "We couldn't complete the payment for the parts. We've been notified and will contact you.")
    freight = float(o["freight_quote"] or 0)
    total = round((o["total"] or o["subtotal"]) + freight, 2)
    conn.execute("""UPDATE orders SET status = 'paid', shipping = %s, total = %s, paid_at = now(), updated_at = now()
                    WHERE id = %s AND status IN ('freight_quoted', 'awaiting_freight')""", (freight, total, o["id"]))
    O.add_event(conn, o["id"], f"Customer approved freight. Parts captured; paid {web.money(total)} in total")
    o = O.get(conn, order_id=o["id"])
    if request is not None:
        checkout.send_order_emails(request, o)
    return ("paid", None)


def freight_paid_on_stripe_page(conn, s, site, request=None):
    """Webhook: the customer paid the freight on the Stripe page (their bank wanted confirmation)."""
    meta = s.get("metadata") or {}
    o = O.get(conn, order_id=int(meta["order_id"])) if (meta.get("order_id") or "").isdigit() else None
    if not o or o["status"] != "freight_quoted" or s.get("payment_status") != "paid":
        return
    conn.execute("UPDATE orders SET freight_payment = %s WHERE id = %s", (s.get("payment_intent"), o["id"]))
    O.add_event(conn, o["id"], "Freight paid on the Stripe payment page")
    finish(conn, O.get(conn, order_id=o["id"]), site, request=request)


# ------------------------------------------------------------------ reminders and automatic cancel

def run_due(conn, site, now=None):
    """Owner reminder after a day without a quote; customer reminder two days after the quote; cancel (hold released)
    CANCEL_AFTER_DAYS after the hold. Returns what was done, for tests and logs."""
    now = now or dt.datetime.now(dt.timezone.utc)
    done = []
    rows = conn.execute("""SELECT id FROM orders WHERE freight AND status IN ('awaiting_freight', 'freight_quoted')""").fetchall()
    for (oid,) in rows:
        o = O.get(conn, order_id=oid)
        held = o.get("hold_placed_at") or o.get("paid_at") or o.get("created_at")
        if held and now - held >= dt.timedelta(days=CANCEL_AFTER_DAYS):
            cancel(conn, o, f"no approval within {CANCEL_AFTER_DAYS} days", site)
            if mailer.notify_address():
                _send(conn, O.get(conn, order_id=oid), "owner_problem", mailer.notify_address(), site,
                      why=f"cancelled automatically: no approved freight price within {CANCEL_AFTER_DAYS} days. The hold was released.")
            done.append(("cancelled", o["number"]))
        elif o["status"] == "awaiting_freight" and not o.get("owner_reminded_at") and held \
                and now - held >= dt.timedelta(hours=OWNER_REMIND_HOURS) and mailer.notify_address():
            _send(conn, o, "owner_remind", mailer.notify_address(), site)
            conn.execute("UPDATE orders SET owner_reminded_at = now() WHERE id = %s", (oid,))
            done.append(("owner_reminded", o["number"]))
        elif o["status"] == "freight_quoted" and not o.get("freight_reminded_at") and o.get("freight_quoted_at") \
                and now - o["freight_quoted_at"] >= dt.timedelta(hours=CUSTOMER_REMIND_HOURS):
            _send(conn, o, "reminder", o.get("email"), site)
            conn.execute("UPDATE orders SET freight_reminded_at = now() WHERE id = %s", (oid,))
            done.append(("customer_reminded", o["number"]))
    return done


def launch(state):
    """Every 30 minutes: freight reminders and automatic cancels."""
    _POOL[:] = [state.pool]
    stop = threading.Event()

    def loop():
        while not stop.wait(1800):
            site = _site()
            try:
                with state.pool.connection() as conn:
                    run_due(conn, site)
            except Exception:      # noqa: BLE001 - keep the scheduler alive
                log.exception("freight reminders")
    threading.Thread(target=loop, daemon=True, name="freight").start()
    return stop


# ------------------------------------------------------------------ customer pages

def _by_token(conn, token):
    r = conn.execute("SELECT number FROM orders WHERE freight_token = %s", (token,)).fetchone()
    return O.get(conn, number=r[0]) if r else None


@router.get("/freight/{token}")
def freight_link(request: Request, token: str, paid: str = ""):
    """The link in the freight emails: opens the order page from any device."""
    from . import checkout
    st = web._state(request)
    with st.pool.connection() as conn:
        o = _by_token(conn, token)
        if not o:
            return web.not_found(request)
    checkout.remember_order(request, o["number"])
    return RedirectResponse(f"/order/{o['number']}" + ("?freight=paid" if paid else "") + "#freight", status_code=303)


@router.post("/freight/{token}/approve")
def freight_approve(request: Request, token: str):
    from . import checkout
    st = web._state(request)
    with st.pool.connection() as conn:
        o = _by_token(conn, token)
        if not o:
            return web.not_found(request)
        checkout.remember_order(request, o["number"])
        try:
            result, extra = approve(conn, o, _site(request), request)
        except stripe.StripeError as e:
            log.warning("freight approve %s: %s", o["number"], e)
            O.add_event(conn, o["id"], f"Approval failed at Stripe: {getattr(e, 'user_message', None) or e}")
            result, extra = "problem", "Payment couldn't be completed right now. Please try again in a few minutes."
    if result == "pay":
        return RedirectResponse(extra, status_code=303)
    q = "freight=approved" if result == "paid" else "freight=problem"
    return RedirectResponse(f"/order/{o['number']}?{q}#freight", status_code=303)


@router.post("/freight/{token}/cancel")
def freight_cancel(request: Request, token: str, confirm: str = Form("")):
    from . import checkout
    st = web._state(request)
    with st.pool.connection() as conn:
        o = _by_token(conn, token)
        if not o:
            return web.not_found(request)
        checkout.remember_order(request, o["number"])
        if o["status"] in OPEN:
            o = cancel(conn, o, "cancelled by the customer", _site(request))
            if mailer.notify_address():
                _send(conn, o, "owner_problem", mailer.notify_address(), _site(request),
                      why="the customer cancelled instead of approving the freight price. The hold was released.")
    return RedirectResponse(f"/order/{o['number']}?freight=cancelled#freight", status_code=303)
