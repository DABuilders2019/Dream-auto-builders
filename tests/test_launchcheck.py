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
