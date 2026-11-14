"""Customer accounts: sign up, password and emailed-code sign-in, limits, the account page (orders, garage kept on
the account), saving an account at checkout, checking out as a Stripe customer, and deleting an account."""
import re
import time
from types import SimpleNamespace

import pytest
import stripe
from fastapi.testclient import TestClient

from store import accounts, mailer
from test_checkout import paid_event, pid, post_webhook, shop  # noqa: F401  (fixture)


@pytest.fixture
def mails(monkeypatch):
    got = []
    monkeypatch.setattr(mailer, "_deliver", lambda msg, on_done=None: (got.append(msg), on_done and on_done(True, "")))
    for lim in (accounts.login_fail_email, accounts.login_fail_ip, accounts.signup_ip):
        lim.hits.clear()
    accounts._synced.clear()
    return got


def code_from(mails, to):
    time.sleep(0.2)
    m = [x for x in mails if x["To"] == to][-1]
    return re.search(r"\b(\d{6})\b", m["Subject"]).group(1)


def test_passwords_are_hashed():
    h = accounts.hash_password("correct horse")
    assert h.startswith("scrypt$") and "correct" not in h
    assert accounts.check_password("correct horse", h) and not accounts.check_password("wrong", h)


def test_signup_signin_signout(shop, mails):
    c = shop.c
    assert "Create an account" in c.get("/account").text
    r = c.post("/account/signup", data={"name": "Jo Driver", "email": "Jo@Example.com", "password": "short"})
    assert r.status_code == 400 and "at least 8" in r.text
    r = c.post("/account/signup", data={"name": "Jo Driver", "email": "Jo@Example.com", "password": "longenough1"}, follow_redirects=False)
    assert r.status_code == 303
    page = c.get("/account?msg=welcome").text
    assert "Hi, Jo" in page and "jo@example.com" in page
    assert ">Jo<small>My account</small>" in c.get("/").text.replace("\n", "")            # header greets them
    # the same email can't sign up twice
    c.post("/account/signout")
    r = c.post("/account/signup", data={"name": "X", "email": "jo@example.com", "password": "longenough1"})
    assert "already an account" in r.text
    # sign in: wrong then right password
    assert "That email and password don" in c.post("/account/signin", data={"email": "jo@example.com", "password": "nope"}).text
    c.post("/account/signin", data={"email": "JO@example.com", "password": "longenough1"})
    assert "Hi, Jo" in c.get("/account").text


def test_failed_password_tries_are_limited(shop, mails):
    c = shop.c
    c.post("/account/signup", data={"name": "Al", "email": "al@example.com", "password": "longenough1"})
    c.post("/account/signout")
    for _ in range(8):
        c.post("/account/signin", data={"email": "al@example.com", "password": "bad"})
    r = c.post("/account/signin", data={"email": "al@example.com", "password": "longenough1"})
    assert r.status_code == 429 and "Too many tries" in r.text


def test_code_sign_in(shop, mails):
    c = shop.c
    c.post("/account/signup", data={"name": "Cy Coder", "email": "cy@example.com", "password": ""})   # no password
    c.post("/account/signout")
    # an unknown email gets the same answer, and no email
    r = c.post("/account/code", data={"email": "nobody@example.com"})
    assert "Check your email" in r.text
    time.sleep(0.2)
    assert not [m for m in mails if m["To"] == "nobody@example.com"]
    c.post("/account/code", data={"email": "cy@example.com"})
    code = code_from(mails, "cy@example.com")
    bad = str((int(code) + 1) % 10 ** 6).zfill(6)
    r = c.post("/account/code/verify", data={"email": "cy@example.com", "code": bad})
    assert "That code isn" in r.text and "4 tries left" in r.text
    c.post("/account/code/verify", data={"email": "cy@example.com", "code": code})
    assert "Hi, Cy" in c.get("/account").text
    assert shop.conn.execute("SELECT email_verified FROM accounts WHERE email = 'cy@example.com'").fetchone()[0]
    # a used code doesn't work twice
    c.post("/account/signout")
    assert "expired" in c.post("/account/code/verify", data={"email": "cy@example.com", "code": code}).text
    # at most 3 codes per 15 minutes
    for _ in range(2):
        c.post("/account/code", data={"email": "cy@example.com"})
    assert "Too many codes" in c.post("/account/code", data={"email": "cy@example.com"}).text


def test_password_account_without_email_service(shop, mails, monkeypatch):
    monkeypatch.delenv("SMTP_HOST")
    c = shop.c
    page = c.get("/account").text
    assert "switched on once the store" in page
    r = c.post("/account/signup", data={"name": "Pat", "email": "pat@example.com", "password": ""})
    assert "Choose a password" in r.text


def test_garage_kept_on_the_account(shop, mails, conn):
    c = shop.c
    c.post("/account/signup", data={"name": "Gus", "email": "gus@example.com", "password": "longenough1"})
    vid = conn.execute("SELECT id FROM vehicles LIMIT 1").fetchone()[0]
    c.post("/garage/add", data={"vehicle": vid})
    assert conn.execute("SELECT garage FROM accounts WHERE email = 'gus@example.com'").fetchone()[0] == [vid]
    # another device: signing in brings the garage back
    from store.app import create_app
    with TestClient(create_app(conn.info.dsn), base_url="https://shop.test") as other:
        other.post("/account/signin", data={"email": "gus@example.com", "password": "longenough1"})
        assert "1 vehicle" in other.get("/account").text


def test_orders_on_the_account(shop, mails, conn, monkeypatch):
    c = shop.c
    # a guest order with the same email as a later account
    c.post("/cart/add", data={"product_id": pid(c, "LK-100"), "qty": 1})
    c.post("/checkout")
    guest = shop.created[-1]
    post_webhook(c, paid_event("cs_test_1", int(guest["metadata"]["order_id"]), total=99995))
    number = guest["metadata"]["order_number"]

    created = []
    monkeypatch.setattr(stripe.Customer, "create", staticmethod(lambda api_key=None, **kw: created.append(kw) or SimpleNamespace(id="cus_123")))
    c.cookies.clear()
    c.post("/account/signup", data={"name": "Sam Buyer", "email": "buyer@example.com", "password": "longenough1"})
    page = c.get("/account").text
    assert number not in page and "Confirm your email" in page          # not shown until the email is proved
    c.post("/account/code", data={"email": "buyer@example.com"})
    c.post("/account/code/verify", data={"email": "buyer@example.com", "code": code_from(mails, "buyer@example.com")})
    assert number in c.get("/account").text
    assert c.get(f"/order/{number}").status_code == 200                  # its page opens from the account

    # checking out signed in: as the account's Stripe customer, order linked to the account
    c.post("/cart/add", data={"product_id": pid(c, "LK-100"), "qty": 1})
    c.post("/checkout")
    params = shop.created[-1]
    assert params["customer"] == "cus_123" and params["saved_payment_method_options"] == {"payment_method_save": "enabled"}
    assert created[0]["email"] == "buyer@example.com" and "save_account" not in params["metadata"]
    aid = conn.execute("SELECT account_id FROM orders WHERE number = %s", (params["metadata"]["order_number"],)).fetchone()[0]
    assert aid == conn.execute("SELECT id FROM accounts WHERE email = 'buyer@example.com'").fetchone()[0]
    assert "This order is saved to your account" in c.get("/cart").text


def test_save_account_at_checkout(shop, mails, conn):
    c = shop.c
    c.post("/cart/add", data={"product_id": pid(c, "LK-100"), "qty": 1})
    assert "Save my name and email to create an account" in c.get("/cart").text
    c.post("/checkout", data={"save_account": "1"})
    params = shop.created[-1]
    assert params["metadata"]["save_account"] == "1"
    ev = paid_event("cs_test_1", int(params["metadata"]["order_id"]), total=99995)
    ev["data"]["object"]["metadata"] = params["metadata"]               # Stripe sends the checkout's metadata back
    post_webhook(c, ev)
    row = conn.execute("SELECT id, name, password_hash, source FROM accounts WHERE email = 'buyer@example.com'").fetchone()
    assert row and row[1] == "Sam Buyer" and row[2] is None and row[3] == "checkout"
    assert conn.execute("SELECT account_id FROM orders WHERE id = %s", (int(params["metadata"]["order_id"]),)).fetchone()[0] == row[0]
    time.sleep(0.2)
    assert any(m["To"] == "buyer@example.com" and "account is ready" in m["Subject"] for m in mails)
    # they sign in with a code and see the order
    c.cookies.clear()
    c.post("/account/code", data={"email": "buyer@example.com"})
    c.post("/account/code/verify", data={"email": "buyer@example.com", "code": code_from(mails, "buyer@example.com")})
    assert params["metadata"]["order_number"] in c.get("/account").text
    # not ticked: no account
    c.cookies.clear()
    c.post("/cart/add", data={"product_id": pid(c, "LK-100"), "qty": 1})
    c.post("/checkout")
    assert "save_account" not in shop.created[-1]["metadata"]


def test_change_password_and_delete(shop, mails, conn):
    c = shop.c
    c.post("/account/signup", data={"name": "Del", "email": "del@example.com", "password": "longenough1"})
    r = c.post("/account/password", data={"current_password": "wrong", "new_password": "newpassword2"}, follow_redirects=False)
    assert "error=current" in r.headers["location"]
    c.post("/account/password", data={"current_password": "longenough1", "new_password": "newpassword2"})
    c.post("/account/signout")
    c.post("/account/signin", data={"email": "del@example.com", "password": "newpassword2"})
    assert "Hi, Del" in c.get("/account").text
    r = c.post("/account/delete", data={"confirm": "nope"}, follow_redirects=False)
    assert "error=confirm" in r.headers["location"]
    c.post("/account/delete", data={"confirm": "delete"})
    assert conn.execute("SELECT count(*) FROM accounts WHERE email = 'del@example.com'").fetchone()[0] == 0
    assert "Sign in" in c.get("/account").text


def test_cart_hides_save_account_without_email(shop, mails, monkeypatch):
    c = shop.c
    c.post("/cart/add", data={"product_id": pid(c, "LK-100"), "qty": 1})
    assert "Save my name and email" in c.get("/cart").text
    monkeypatch.delenv("SMTP_HOST")
    assert "Save my name and email" not in c.get("/cart").text


def test_admin_email_page(shop, mails, monkeypatch):
    c = shop.c
    c.post("/admin/login", data={"password": "pw"})
    page = c.get("/admin/email").text
    assert "SMTP_HOST" in page and "smtp.example.com" in page
    monkeypatch.setattr(mailer, "test_send", lambda to: (False, "The email service refused the email (550): only your own email"))
    assert "only your own email" in c.post("/admin/email/test", data={"to": "x@example.com"}).text
