"""Admin > Launch Check: is everything ready to go live?

1. Automatic checks: each one asks the real service (Google Maps, the email service, Stripe, UPS) or reads the latest
   result (SEMA, Turn 14), and says OK, needs attention, or not working, with what to do.
2. Sample emails: sends the real email templates (sign-in code, price match, cart reminder, account ready) to an address
   you choose, and shows whether the email service accepted each one.
3. Try-it-yourself tests: step-by-step instructions for every feature, with tick boxes saved in the database.
"""
import json
import os
import shutil
import smtplib
import time

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import mailer, web

router = APIRouter()
OK, WARN, BAD, INFO = "ok", "warn", "bad", "info"
TESTS_KEY = "launch_tests"


def _env(k):
    return (os.environ.get(k) or "").strip().strip('"').strip()


PAGES = {"/admin/email": "Email page", "/admin/ups": "UPS page", "/admin/ups#free": "free shipping settings",
         "/admin/sema": "SEMA Data page", "/admin/turn14": "Turn 14 page", "/admin/price-match": "Price Match page"}


def item(name, status, text, fix="", link=""):
    return {"name": name, "status": status, "text": text, "fix": fix, "link": link, "link_label": PAGES.get(link, "page")}


# ------------------------------------------------------------------ automatic checks

def check_site(conn):
    out = []
    url = _env("SITE_URL")
    if not url:
        out.append(item("Site address (SITE_URL)", WARN, "Not set, so emails link to the Render address.",
                        "On moving day set SITE_URL to https://dreamautobuilders.com in Render > Environment."))
    elif "onrender.com" in url:
        out.append(item("Site address (SITE_URL)", WARN, f"Emails link to {url}.", "Change to https://dreamautobuilders.com on moving day."))
    else:
        out.append(item("Site address (SITE_URL)", OK if url.startswith("https://") else WARN, f"Emails link to {url}."))
    out.append(item("Test-site password (SITE_PASSWORD)", WARN if _env("SITE_PASSWORD") else OK,
                    "On: visitors need the password." if _env("SITE_PASSWORD") else "Off: the shop is open to everyone.",
                    "Delete SITE_PASSWORD in Render on launch day." if _env("SITE_PASSWORD") else ""))
    out.append(item("Test-site banner (SITE_PREVIEW)", WARN if web.is_preview() else OK,
                    "The pink \"Test site\" banner shows." if web.is_preview() else "Banner off.",
                    "Set SITE_PREVIEW to 0 on launch day." if web.is_preview() else ""))
    pw = _env("ADMIN_PASSWORD")
    out.append(item("Admin password", OK if len(pw) >= 12 else WARN, f"{len(pw)} characters.",
                    "" if len(pw) >= 12 else "Use at least 12 characters (Render > Environment > ADMIN_PASSWORD)."))
    out.append(item("Secure cookies (COOKIE_SECURE)", OK if _env("COOKIE_SECURE") == "1" else WARN,
                    "On." if _env("COOKIE_SECURE") == "1" else "Off.", "" if _env("COOKIE_SECURE") == "1" else "Set COOKIE_SECURE to 1."))
    return out


def check_storage(conn):
    out = []
    size = conn.execute("SELECT pg_database_size(current_database())").fetchone()[0]
    out.append(item("Database", OK, f"Connected. Using {size / 1e9:.2f} GB.",
                    "Watch it against the storage size on the database's page in Render (5 GB to start)."))
    path = os.environ.get("PHOTO_DIR") or os.environ.get("DATA_DIR") or "."
    try:
        du = shutil.disk_usage(path)
        free, pct = du.free / 1e9, 100 * du.used / du.total
        out.append(item("Photo disk", OK if pct < 80 else WARN if pct < 92 else BAD,
                        f"{pct:.0f}% full, {free:.1f} GB free of {du.total / 1e9:.0f} GB.",
                        "" if pct < 80 else "Make the disk bigger in Render (web service > Disks) before loading more brands."))
    except OSError:
        out.append(item("Photo disk", WARN, "Couldn't read the disk size."))
    return out


def check_email(conn):
    out = []
    if not mailer.configured():
        return [item("Email service", BAD, "SMTP_HOST and MAIL_FROM aren't both set, so no emails go out.",
                     "Add the Resend settings in Render > Environment (Launch checklist > Moving day).", "/admin/email")]
    host, port = _env("SMTP_HOST"), int(_env("SMTP_PORT") or "587")
    try:
        cls = smtplib.SMTP_SSL if port == 465 else smtplib.SMTP
        with cls(host, port, timeout=15) as s:
            if port != 465:
                s.starttls()
            if _env("SMTP_USERNAME"):
                s.login(_env("SMTP_USERNAME"), _env("SMTP_PASSWORD"))
        out.append(item("Email service", OK, f"Signed in to {host}."))
    except Exception as e:      # noqa: BLE001
        out.append(item("Email service", BAD, f"Couldn't sign in to {host}: {mailer._why(e)}",
                        "Check SMTP_USERNAME and the re_ key in SMTP_PASSWORD.", "/admin/email"))
    sender = _env("MAIL_FROM")
    test_sender = "resend.dev" in sender
    out.append(item("Sender (MAIL_FROM)", WARN if test_sender else OK, sender,
                    "This is Resend's test sender: it only delivers to the email on your Resend account. Switch to "
                    "admin@dreamautobuilders.com after the domain is verified." if test_sender else ""))
    to = mailer.notify_address()
    out.append(item("Alerts address (ORDER_NOTIFY_EMAIL)", OK if to else WARN, to or "Not set: no new-order or price match alerts.",
                    "" if to else "Set ORDER_NOTIFY_EMAIL in Render > Environment."))
    fails = [r for r in mailer.recent if not r[3]]
    if fails:
        t, to_, subj, _, err = fails[-1]
        out.append(item("Recent email failures", WARN, f"{len(fails)} since the last restart. Latest: \"{subj}\" to {to_}: {err}",
                        "See Admin > Email > Recent emails.", "/admin/email"))
    return out


def check_maps(conn):
    from . import installers
    if not installers.configured():
        return [item("Google Maps (installer finder)", BAD, "GOOGLE_MAPS_API_KEY isn't set, so no nearby shops are shown.",
                     "Add the key in Render > Environment (docs/INSTALLERS.md).")]
    shops = installers.nearby({"ship_address": {"postal_code": "10001", "city": "New York", "state": "NY"}})
    if shops:
        return [item("Google Maps (installer finder)", OK,
                     f"Found {len(shops)} repair shops near ZIP 10001, e.g. {shops[0]['name']}. (Uses 1 of the 5,000 free searches a month.)")]
    return [item("Google Maps (installer finder)", BAD, "Google didn't return any shops for a test address.",
                 "Check that Places API (New) is enabled for the key and billing is on (Render logs show Google's answer).")]


def check_stripe(conn):
    import stripe
    key = _env("STRIPE_SECRET_KEY")
    if not key:
        return [item("Stripe", BAD, "STRIPE_SECRET_KEY isn't set, so checkout is off.")]
    out = []
    live = key.startswith("sk_live_") or key.startswith("rk_live_")
    try:
        acct = stripe.Account.retrieve(api_key=key)
        name = ((acct.get("settings") or {}).get("dashboard") or {}).get("display_name") or acct.get("id")
        out.append(item("Stripe account", OK if live else WARN, f"Connected to {name}, {'LIVE mode (real charges)' if live else 'test mode (no real charges)'}.",
                        "" if live else "Switch to the live keys (sk_live_...) on launch day."))
        if live and not acct.get("charges_enabled"):
            out.append(item("Stripe payments", BAD, "Stripe hasn't enabled charges on this account yet.", "Finish activating the account in Stripe."))
    except Exception as e:      # noqa: BLE001
        return [item("Stripe account", BAD, f"Stripe refused the key: {e}", "Check STRIPE_SECRET_KEY in Render.")]
    if not _env("STRIPE_WEBHOOK_SECRET"):
        out.append(item("Stripe webhook", BAD, "STRIPE_WEBHOOK_SECRET isn't set, so paid orders won't be marked paid."))
        return out
    try:
        eps = stripe.WebhookEndpoint.list(api_key=key, limit=30).get("data") or []
    except Exception as e:      # noqa: BLE001
        out.append(item("Stripe webhook", WARN, f"Couldn't list webhooks: {e}"))
        return out
    mine = [e for e in eps if (e.get("url") or "").rstrip("/").endswith("/stripe/webhook")]
    if not mine:
        out.append(item("Stripe webhook", BAD, f"No webhook in Stripe {'live' if live else 'test'} mode points to /stripe/webhook.",
                        "Stripe > Workbench > Webhooks > add the site's /stripe/webhook address."))
        return out
    from .checkout import WEBHOOK_EVENTS
    site = _env("SITE_URL").rstrip("/")
    for e in mine:
        events = set(e.get("enabled_events") or [])
        missing = [x for x in WEBHOOK_EVENTS if "*" not in events and x not in events]
        right_site = not site or e["url"].startswith(site)
        ok = e.get("status") == "enabled" and not missing and right_site
        text = f"{e['url']} is {e.get('status')}"
        fixes = []
        if missing:
            fixes.append("add the events " + ", ".join(missing))
        if not right_site:
            fixes.append(f"change the address to {site}/stripe/webhook")
        if "checkout.session.expired" in missing:
            fixes.append("(checkout.session.expired is what sends cart reminders)")
        out.append(item("Stripe webhook", OK if ok else WARN, text + ("." if ok else ""), "In Stripe: " + "; ".join(fixes) if fixes else ""))
    return out


def check_shipping(conn):
    from . import shipping, ups
    out = []
    sh = shipping.settings()
    if sh["carrier"] != "ups":
        return [item("Shipping", INFO, f"Flat-rate shipping (live UPS prices are off).")]
    if not ups.configured():
        return [item("UPS", BAD, "UPS keys aren't set, so checkout uses the fallback price.", "Add UPS_CLIENT_ID and UPS_CLIENT_SECRET.", "/admin/ups")]
    env = ups.settings()["env"]
    try:
        ups.token()
        r = ups.rates(sh["from_zip"], "90210", 10, (20, 12, 8))
        ground = next((x for x in r if x["code"] == "03"), None)
        out.append(item("UPS prices", OK if env == "production" else WARN,
                        f"UPS answered: Ground from {sh['from_zip']} to 90210 for a 10 lb box is ${ground['price']:.2f}." if ground else "UPS answered.",
                        "" if env == "production" else "These are UPS's testing-server sample prices: set UPS_ENV to production at launch.", "/admin/ups"))
    except ups.UPSError as e:
        out.append(item("UPS prices", BAD, str(e), "", "/admin/ups"))
    if sh["from_zip"] in ("", "11691"):
        out.append(item("Ship-from ZIP", WARN, f"{sh['from_zip'] or 'Not set'} is a placeholder.",
                        "Set the supplier warehouse ZIP (config/store.json > shipping.from_zip): tell Claude the ZIP."))
    rule = shipping.free_rule(conn)
    out.append(item("Free shipping", INFO, f"{'On' if rule['on'] else 'Off'}: orders over ${rule['over']:,.0f}, "
                    f"{'oversized boxes excluded, ' if rule.get('oversize') else ''}parts over {rule['max_lb']:g} lb excluded.", "", "/admin/ups#free"))
    return out


def check_feeds(conn):
    out = []
    r = conn.execute("SELECT value FROM sema_state WHERE key = 'last_test'").fetchone()
    rec = conn.execute("SELECT value FROM sema_state WHERE key = 'receiver'").fetchone()
    if r and r[0]:
        t = r[0]
        left = ""
        if rec and rec[0] and rec[0].get("APIMaxCount"):
            left = f" {int(rec[0]['APIMaxCount']) - int(rec[0].get('APICount') or 0):,} of {int(rec[0]['APIMaxCount']):,} API calls left."
        out.append(item("SEMA Data", OK if t.get("status") == "ok" else BAD,
                        f"Last connection test {t.get('finished', '')[:16].replace('T', ' ')} UTC: {t.get('status')}.{left}",
                        t.get("error") or "", "/admin/sema"))
    else:
        out.append(item("SEMA Data", WARN, "No connection test yet.", "Press Test connection on the SEMA Data page.", "/admin/sema"))
    r = conn.execute("SELECT value FROM t14_state WHERE key = 'last_test'").fetchone()
    env = "production" if (_env("TURN14_ENV") or "test").startswith("prod") else "test"
    if r and r[0]:
        t = r[0]
        ok = t.get("status") in ("ok", "done", None) and not t.get("error")
        out.append(item("Turn 14", OK if ok and env == "production" else WARN if ok else BAD,
                        f"Last connection test on the {env} server: {'worked' if ok else (t.get('error') or t.get('status'))}.",
                        "" if ok and env == "production" else ("Waiting for Turn 14 to approve production access." if ok else
                                                                "Reply to Turn 14 about API access, then test again."), "/admin/turn14"))
    else:
        out.append(item("Turn 14", WARN, "No connection test yet.", "Press Test connection on the Turn 14 page.", "/admin/turn14"))
    return out


def check_features(conn):
    from .checkout import recovery_cfg
    out = []
    rec = recovery_cfg()
    out.append(item("Cart reminder emails", OK if rec["enabled"] and rec["email"] and mailer.configured() else WARN,
                    f"{'On' if rec['enabled'] else 'Off'}; a reminder goes out {rec['hours']} hours after a shopper who ticked "
                    "\"send me updates\" leaves the payment page.",
                    "Also needs Stripe's promotional-email terms accepted in live mode (Launch checklist) and the "
                    "checkout.session.expired webhook event."))
    out.append(item("Sign-in codes and \"save my account\"", OK if mailer.configured() else BAD,
                    "Email is set up, so codes and the account-ready email can go out." if mailer.configured()
                    else "Email isn't set up: the code option and the \"save my name and email\" box are hidden."))
    pending = conn.execute("SELECT count(*) FROM price_matches WHERE status = 'pending'").fetchone()[0]
    out.append(item("Price match", OK if mailer.notify_address() else WARN,
                    f"{pending} request{'s' if pending != 1 else ''} waiting.",
                    "" if mailer.notify_address() else "Set ORDER_NOTIFY_EMAIL so you hear about new requests.", "/admin/price-match"))
    return out


SECTIONS = [("site", "Site settings", check_site), ("storage", "Database and disk", check_storage),
            ("email", "Email", check_email), ("stripe", "Payments (Stripe)", check_stripe),
            ("maps", "Google Maps", check_maps), ("shipping", "Shipping (UPS)", check_shipping),
            ("feeds", "Catalog feeds", check_feeds), ("features", "Features", check_features)]


def run_checks(conn):
    out = []
    for key, title, fn in SECTIONS:
        t0 = time.time()
        try:
            items = fn(conn)
        except Exception as e:      # noqa: BLE001 - one broken check shouldn't hide the rest
            items = [item(title, BAD, f"The check itself failed: {e.__class__.__name__}: {e}")]
        out.append({"key": key, "title": title, "items": items, "ms": int((time.time() - t0) * 1000)})
    return out


# ------------------------------------------------------------------ sample emails

def sample_emails(conn, base):
    """[(kind, label, subject, text, html)] built with the same functions the real emails use."""
    from . import accounts, checkout, pricematch
    row = conn.execute("""SELECT p.id, p.title, p.price, p.part_number, b.name FROM products p JOIN brands b ON b.id = p.brand_id
                          WHERE p.status = 'active' AND p.price > 50 ORDER BY p.id LIMIT 1""").fetchone()
    pid, title, price, pn, brand = row if row else (0, "Sample part", 199.99, "SAMPLE-1", "Sample Brand")
    price = float(price)
    theirs = round(price * 0.93, 2)
    link = "https://www.competitor-store.example/part/" + str(pn)
    out = []
    s, t, h = accounts.code_email("123456", "Sample")
    out.append(("code", "One-time sign-in code", "[SAMPLE] " + s, t, h))
    s, t = checkout.account_ready_email("Sample Customer", base)
    out.append(("account", "Account saved at checkout", "[SAMPLE] " + s, t, None))
    s, t = pricematch.mail_received(0, title, price, theirs, 1, link, base)
    out.append(("pm_received", "Price match: customer's \"we got it\"", "[SAMPLE] " + s, t, None))
    s, t = pricematch.mail_alert(0, title, price, theirs, 1, link, "customer@example.com", base)
    out.append(("pm_alert", "Price match: your new-request alert", "[SAMPLE] " + s, t, None))
    s, t = pricematch.mail_approved(title, theirs, 1, price, "", base + "/price-match/claim/SAMPLE-LINK", "tomorrow at this time")
    out.append(("pm_approved", "Price match: approved", "[SAMPLE] " + s, t, None))
    s, t = pricematch.mail_declined(title, "That seller isn't an authorized dealer.")
    out.append(("pm_declined", "Price match: declined", "[SAMPLE] " + s, t, None))
    o = {"number": "SAMPLE", "items": [{"qty": 1, "brand_name": brand, "title": title, "part_number": pn, "line_total": price}]}
    s, t, h = checkout.recovery_email(o, "Sample", base + "/cart", base)
    out.append(("reminder", "Cart reminder", "[SAMPLE] " + s, t, h))
    return out


# ------------------------------------------------------------------ try-it-yourself tests

TESTS = [
    ("Your five features", [
        ("maps", "Nearby installers (Google Maps)", [
            "Add a part that usually needs a shop (an exhaust, lift kit or tires) to the cart.",
            "Check out with test card 4242 4242 4242 4242 (any future date, any CVC), shipping to a real address.",
            "On the order confirmation page, look for \"Need it installed? Shops near you\".",
            "Click a shop's map link, and \"I'll use this shop\"."],
         "Up to 5 real repair shops near that address with Google Maps links; the same list is in the confirmation email; "
         "the shop you picked shows on Admin > Installers."),
        ("pricematch", "Price match: customer and admin emails", [
            "On any product page open \"Found it for less?\", paste a competitor link, a lower price and your email, and send.",
            "Check your inbox for \"We got your price match request\", and the alerts inbox for \"Price match request #...\".",
            "Admin > Price Match: open the link, then Approve and email.",
            "Open the \"Your price match is approved\" email and click the link.",
            "Check out with the test card, then open the same link again."],
         "Both emails arrive; the cart shows the matched price with the regular price crossed out; the order's history says "
         "\"Price match #...\"; the link afterwards says it was already used. Also try Decline: the customer gets \"About your price match request\"."),
        ("code", "Sign in with an emailed one-time code", [
            "Sign out (Sign In > Sign out) if you're signed in.",
            "Sign In > enter the email of an account (one made at checkout or with a password) > Email me a code.",
            "Type the 6-digit code from the email.",
            "Try a wrong code once, and a code older than the time limit."],
         "The code arrives within a minute and signs you in; a wrong or old code shows a clear error."),
        ("saveacct", "Save name and email at checkout (account without a password)", [
            "Use an email that has no account yet. Add a part to the cart.",
            "In the cart tick \"Save my name and email to create an account\", then check out with the test card.",
            "Look for \"Your Dream Auto Builders account is ready\" in that inbox.",
            "Sign In > Email me a code with that email."],
         "The account email arrives, the code signs you in, and My Account lists the order."),
        ("reminder", "Abandoned cart reminder email", [
            "Only possible in live mode, after Stripe's promotional-email terms are accepted (Launch checklist).",
            "Start a checkout, tick \"send me updates\" on the payment page, enter your email, and close the page without paying.",
            "Wait for the payment page to expire (the hours set under Cart reminder emails above).",
            "Before then, send yourself the sample reminder (Sample emails) to see what it looks like."],
         "\"You left something in your cart\" arrives with the parts and a link back to the payment page; Admin > Analytics shows "
         "the checkout as \"reminder sent\"."),
    ]),
    ("Checkout and orders", [
        ("order", "A test order from start to finish", [
            "Add two different parts, enter a ZIP, check out with 4242 4242 4242 4242.",
            "Check the customer email and the new-order alert.",
            "Admin > Orders: the order is Paid; mark it shipped with a tracking number; check the shipped email.",
            "Track Order on the site with the order number and email."],
         "Paid order in Admin > Orders, both emails, shipped email with tracking, tracking page works. In Stripe > Webhooks the "
         "delivery shows Succeeded."),
        ("declined", "A declined card", ["Check out with card 4000 0000 0000 0002."],
         "Stripe says the card was declined; no order is marked paid."),
        ("refund", "A refund", ["Admin > Orders > the test order > Refund."], "Stripe shows the refund; the order says Refunded."),
        ("carb", "California emissions rule", [
            "Add a catalytic converter that is NOT CARB-approved; ship to a California ZIP (e.g. 90210)."],
         "Checkout is blocked with a message naming the part; a CARB-legal converter goes through."),
    ]),
    ("Shipping", [
        ("ups", "UPS prices and free shipping", [
            "Cart: enter a ZIP for a small part under $149, then add parts until over $149.",
            "Add a heavy or oversized part (e.g. a large tire), and try an Alaska ZIP (99501)."],
         "UPS prices under the minimum; Free UPS Ground over it; the oversized part pays only its own Ground price; "
         "Alaska isn't free; freight parts ask for a quote."),
    ]),
    ("Shopping", [
        ("vehicle", "Vehicle finder and fitment", [
            "Add a vehicle in the vehicle bar; browse a category and a brand.", "Open a product that fits and one that doesn't."],
         "Fit badges are right; \"Will this fit?\" agrees; the vehicle stays selected on every page."),
        ("search", "Search", ["Search a part number, a brand, and a phrase like \"cold air intake\"."], "Sensible results; part numbers find the exact part."),
        ("garage", "Garage and saved builds", ["Signed in: add two vehicles, switch between them, save a build; sign in on your phone."],
         "Garage and builds show on both devices."),
        ("phone", "On a phone", ["Browse, search, add to cart and check out on your phone."], "Everything fits the screen and works."),
    ]),
    ("Launch day", [
        ("domain", "Moving day steps", ["Work through Launch checklist > Moving day."], "Site loads at https://dreamautobuilders.com with the padlock."),
        ("outside_email", "Email reaches outside addresses", [
            "After the domain is verified in Resend: Sample emails > send to a personal Gmail that isn't your Resend login."],
         "Arrives in the inbox (not spam), from admin@dreamautobuilders.com."),
        ("live_order", "One real order", ["With live Stripe keys, buy a cheap part with a real card, then refund it."],
         "Charged and refunded in Stripe; emails arrive; the order flows to the supplier as planned."),
    ]),
]


def get_ticks(conn):
    r = conn.execute("SELECT value FROM app_state WHERE key = %s", (TESTS_KEY,)).fetchone()
    try:
        return json.loads(r[0]) if r and r[0] else {}
    except ValueError:
        return {}


def save_ticks(conn, ticks):
    conn.execute("""INSERT INTO app_state (key, value, updated_at) VALUES (%s, %s, now())
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""", (TESTS_KEY, json.dumps(ticks)))
    conn.commit()


# ------------------------------------------------------------------ pages

def _guard(request):
    from .admin import _guard as g
    return g(request)


@router.get("/admin/launch", response_class=HTMLResponse)
def page(request: Request):
    stop = _guard(request)
    if stop:
        return stop
    return _render(request)


def _render(request, results=None, sent=None, to=""):
    from .admin import _page
    st = web._state(request)
    with st.pool.connection() as conn:
        ticks = get_ticks(conn)
        samples = [(k, label) for k, label, *_ in sample_emails(conn, _site(request))]
    total = sum(len(ts) for _, ts in TESTS)
    done = sum(1 for _, ts in TESTS for t in ts if (ticks.get(t[0]) or {}).get("done"))
    counts = None
    if results:
        flat = [i["status"] for s in results for i in s["items"]]
        counts = {k: flat.count(k) for k in (OK, WARN, BAD)}
    return _page(request, "admin_launch.html", title="Launch Check", section="launch", results=results, counts=counts,
                 tests=TESTS, ticks=ticks, total=total, done=done, samples=samples, sent=sent,
                 to=to or mailer.notify_address(), mail_on=mailer.configured())


def _site(request):
    return (_env("SITE_URL") or str(request.base_url)).rstrip("/")


@router.post("/admin/launch/run", response_class=HTMLResponse)
def run(request: Request):
    stop = _guard(request)
    if stop:
        return stop
    st = web._state(request)
    with st.pool.connection() as conn:
        results = run_checks(conn)
    return _render(request, results=results)


@router.post("/admin/launch/emails", response_class=HTMLResponse)
def send_samples(request: Request, to: str = Form(""), kinds: list[str] = Form([])):
    stop = _guard(request)
    if stop:
        return stop
    to = to.strip()
    sent = []
    if not mailer.configured():
        sent.append(("", False, "Email isn't set up (SMTP_HOST and MAIL_FROM)."))
    elif "@" not in to:
        sent.append(("", False, "Enter the email address to send the samples to."))
    else:
        st = web._state(request)
        with st.pool.connection() as conn:
            emails = [e for e in sample_emails(conn, _site(request)) if e[0] in kinds]
        for kind, label, subject, text, html in emails:
            before = len(mailer.recent)
            mailer.send(to, subject, text, html, wait=True)
            r = mailer.recent[-1] if len(mailer.recent) > before else None
            sent.append((label, bool(r and r[3]), "" if r and r[3] else (r[4] if r else "No answer from the email service in 30 seconds.")))
    return _render(request, sent=sent, to=to)


@router.post("/admin/launch/tick")
def tick(request: Request, test: str = Form(...), done: str = Form(""), note: str = Form("")):
    stop = _guard(request)
    if stop:
        return stop
    ids = {t[0] for _, ts in TESTS for t in ts}
    if test in ids:
        st = web._state(request)
        with st.pool.connection() as conn:
            ticks = get_ticks(conn)
            ticks[test] = {"done": done == "1", "note": note.strip()[:500], "at": time.strftime("%b %-d, %Y")}
            save_ticks(conn, ticks)
    return RedirectResponse(f"/admin/launch#t-{test}", status_code=303)
