"""Admin > Launch Check: the automatic checks run (services faked), sample emails go out with each result shown,
and the try-it-yourself tick boxes save."""
import html
import smtplib
import time

import stripe

from test_checkout import shop  # noqa: F401


class FakeSMTP:
    def __init__(self, host, port, timeout=None):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        pass

    def login(self, u, p):
        pass


def test_launch_check(shop, monkeypatch):
    from store import mailer
    c = shop.c
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(stripe.Account, "retrieve", staticmethod(lambda api_key=None: {"id": "acct_1", "charges_enabled": True,
                                                                                     "settings": {"dashboard": {"display_name": "Dream Auto"}}}))
    monkeypatch.setattr(stripe.WebhookEndpoint, "list", staticmethod(lambda api_key=None, limit=30: {"data": [
        {"url": "https://shop.test/stripe/webhook", "status": "enabled",
         "enabled_events": ["checkout.session.completed", "checkout.session.async_payment_succeeded", "checkout.session.async_payment_failed"]}]}))
    assert c.get("/admin/launch", follow_redirects=False).status_code == 303          # admins only
    c.post("/admin/login", data={"password": "pw"})
    page = c.get("/admin/launch").text
    assert "Run all checks" in page and "Sample emails" in page and "0 of" in page
    for t in ("Nearby installers (Google Maps)", "Price match: customer and admin emails", "Sign in with an emailed one-time code",
              "Save name and email at checkout", "Abandoned cart reminder email"):
        assert t in html.unescape(page)

    page = html.unescape(c.post("/admin/launch/run").text)
    assert "Signed in to smtp.example.com" in page
    assert "Connected to Dream Auto, test mode" in page
    assert "checkout.session.expired" in page                    # the missing webhook event is called out
    assert "GOOGLE_MAPS_API_KEY isn't set" in page
    assert "Database" in page and "Photo disk" in page

    # sample emails: each one sent, result shown per email
    def deliver(msg):
        mailer.recent.append((time.time(), msg["To"], msg["Subject"], msg["To"] != "bounce@example.com", "" if msg["To"] != "bounce@example.com" else "550: no"))
    monkeypatch.setattr(mailer, "_deliver", deliver)
    page = html.unescape(c.post("/admin/launch/emails", data={"to": "me@example.com", "kinds": ["code", "pm_alert", "reminder"]}).text)
    subjects = [r[2] for r in mailer.recent if r[1] == "me@example.com"]
    assert "[SAMPLE] 123456 is your Dream Auto Builders sign-in code" in subjects
    assert any(s.startswith("[SAMPLE] Price match request") for s in subjects) and "[SAMPLE] You left something in your cart" in subjects
    assert page.count("Accepted by the email service") == 3
    page = html.unescape(c.post("/admin/launch/emails", data={"to": "bounce@example.com", "kinds": ["code"]}).text)
    assert "550: no" in page

    # ticking a test saves it
    c.post("/admin/launch/tick", data={"test": "pricematch", "done": "1", "note": "worked fine"})
    page = c.get("/admin/launch").text
    assert "1 of" in page and "worked fine" in page
    c.post("/admin/launch/tick", data={"test": "nope", "done": "1"})       # unknown tests are ignored
    assert "1 of" in c.get("/admin/launch").text


def test_domain_check():
    """The Domain section reads the real DNS records (faked here) and says what points where."""
    import httpx
    from store import launchcheck as lc
    records = {
        ("dreamautobuilders.com", "NS"): ["ns1.a2hosting.com.", "ns2.a2hosting.com."],
        ("dreamautobuilders.com", "A"): ["203.0.113.7"], ("dreamautobuilders.com", "AAAA"): ["2001:db8::7"],
        ("www.dreamautobuilders.com", "CNAME"): ["dreamautobuilders.com."],
        ("dreamautobuilders.com", "MX"): ["0 mail.dreamautobuilders.com."],
        ("dreamautobuilders.com", "TXT"): ["v=spf1 a mx include:spf.a2hosting.com ~all", "google-site-verification=abc"],
        ("mail.dreamautobuilders.com", "A"): ["203.0.113.7"], ("shop.dreamautobuilders.com", "A"): ["203.0.113.7"]}
    types = {"A": 1, "NS": 2, "CNAME": 5, "MX": 15, "TXT": 16, "AAAA": 28}

    def handler(req):
        name, t = req.url.params["name"], req.url.params["type"]
        return httpx.Response(200, json={"Answer": [{"type": types[t], "data": v} for v in records.get((name, t), [])]})
    lc._dns_transport = httpx.MockTransport(handler)
    try:
        items = {i["name"]: i for i in lc.check_domain(None)}
    finally:
        lc._dns_transport = None
    assert "a2hosting" in items["Who manages the DNS"]["text"]
    assert items["dreamautobuilders.com (the website)"]["status"] == "warn" and "216.24.57.1" in items["dreamautobuilders.com (the website)"]["fix"]
    assert "AAAA" in items["dreamautobuilders.com IPv6 (AAAA) record"]["name"]
    assert "keep that hosting plan" in items["Email (MX records)"]["fix"]
    assert "hosting.com (A2 Hosting)" in items["Allowed email senders (SPF)"]["fix"]
    assert "Verification record" in items
    assert "same server as the old website" in items["shop.dreamautobuilders.com"]["text"]
    assert "Check what this is" in items["shop.dreamautobuilders.com"]["fix"]

    records[("dreamautobuilders.com", "A")] = ["216.24.57.1"]
    records[("dreamautobuilders.com", "AAAA")] = []
    records[("www.dreamautobuilders.com", "CNAME")] = ["dream-auto-builders.onrender.com."]
    lc._dns_transport = httpx.MockTransport(handler)
    try:
        items = {i["name"]: i for i in lc.check_domain(None)}
    finally:
        lc._dns_transport = None
    assert items["dreamautobuilders.com (the website)"]["status"] == "ok" and items["www.dreamautobuilders.com"]["status"] == "ok"


def test_go_live_switches(monkeypatch):
    from store import launchcheck as lc
    for k, v in {"STRIPE_SECRET_KEY": "sk_test_x", "STRIPE_WEBHOOK_SECRET": "whsec_x", "MAIL_FROM": "onboarding@resend.dev",
                 "SITE_URL": "https://dream-auto-builders.onrender.com", "SITE_PASSWORD": "pw", "SITE_PREVIEW": "1",
                 "UPS_ENV": "test", "TURN14_ENV": "test", "COOKIE_SECURE": "1"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("TURN14_AUTO", raising=False)
    monkeypatch.delenv("SEMA_AUTO", raising=False)
    sw = {x["name"]: x for x in lc.go_live()}
    assert not sw["STRIPE_SECRET_KEY"]["live"] and "test key" in sw["STRIPE_SECRET_KEY"]["now"]
    assert not sw["STRIPE_WEBHOOK_SECRET"]["live"] and not sw["MAIL_FROM"]["live"] and not sw["SITE_URL"]["live"]
    assert not sw["SITE_PASSWORD"]["live"] and not sw["UPS_ENV"]["live"] and sw["COOKIE_SECURE"]["live"]
    for k, v in {"STRIPE_SECRET_KEY": "sk_live_x", "MAIL_FROM": "Dream Auto Builders <admin@dreamautobuilders.com>",
                 "SITE_URL": "https://dreamautobuilders.com", "SITE_PREVIEW": "0", "UPS_ENV": "production",
                 "TURN14_ENV": "production", "TURN14_AUTO": "1", "SEMA_AUTO": "1"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("SITE_PASSWORD")
    sw = {x["name"]: x for x in lc.go_live()}
    assert all(x["live"] for n, x in sw.items() if n != "Ship-from ZIP")
