"""UPS: sign-in and rate parsing (UPS's servers are faked)."""
import json

import httpx
import pytest

from store import ups
from test_checkout import pid, shop  # noqa: F401  (fixture)


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv("UPS_CLIENT_ID", "id")
    monkeypatch.setenv("UPS_CLIENT_SECRET", "secret")
    monkeypatch.setenv("UPS_ACCOUNT_NUMBER", "a1b2c3")
    monkeypatch.setenv("UPS_ENV", "test")
    ups._token.update(value=None, until=0, key=None)


def fake(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_rates(keys):
    seen = []

    def handler(req):
        seen.append(req)
        if req.url.path.endswith("/oauth/token"):
            assert req.headers["x-merchant-id"] == "A1B2C3"
            return httpx.Response(200, json={"access_token": "tok", "expires_in": "14399"})
        body = json.loads(req.content)
        assert body["RateRequest"]["Request"]["RequestOption"] == "Shop"
        assert body["RateRequest"]["Shipment"]["Shipper"]["ShipperNumber"] == "A1B2C3"
        assert body["RateRequest"]["Shipment"]["ShipFrom"]["Address"]["StateProvinceCode"] == "NY"
        assert body["RateRequest"]["Shipment"]["ShipTo"]["Address"]["StateProvinceCode"] == "CA"
        return httpx.Response(200, json={"RateResponse": {"RatedShipment": [
            {"Service": {"Code": "02"}, "TotalCharges": {"MonetaryValue": "60.10"},
             "NegotiatedRateCharges": {"TotalCharge": {"MonetaryValue": "41.20"}}, "GuaranteedDelivery": {"BusinessDaysInTransit": "2"}},
            {"Service": {"Code": "03"}, "TotalCharges": {"MonetaryValue": "21.50"}}]}})

    out = ups.rates("10001", "90210", 12, client=fake(handler))
    assert [r["service"] for r in out] == ["UPS Ground", "UPS 2nd Day Air"]
    assert out[1]["price"] == 41.20 and out[1]["negotiated"] and out[1]["days"] == "2"
    assert seen[0].url.host == "wwwcie.ups.com" and seen[1].url.path == f"/api/rating/{ups.RATING_VERSION}/Shop"


def test_refused_keys(keys):
    def handler(req):
        return httpx.Response(401, json={"response": {"errors": [{"code": "250002", "message": "Invalid Authentication Information."}]}})
    with pytest.raises(ups.UPSError, match="Invalid Authentication"):
        ups.token(client=fake(handler))


def test_admin_page(shop, keys, monkeypatch):
    from test_checkout import shop as _  # noqa: F401
    c = shop.c
    c.post("/admin/login", data={"password": "pw"})
    page = c.get("/admin/ups").text
    assert "UPS connection" in page and "UPS_CLIENT_ID is set" in page
    monkeypatch.setattr(ups, "token", lambda client=None: (_ for _ in ()).throw(ups.UPSError("UPS refused the sign-in (HTTP 401): bad")))
    assert "UPS refused the sign-in" in c.post("/admin/ups/test", data={}).text


def test_sign_in_retries_without_account_header(keys):
    tries = []

    def handler(req):
        tries.append("x-merchant-id" in req.headers)
        if "x-merchant-id" in req.headers:
            return httpx.Response(401, json={"response": {"errors": [{"code": "10401", "message": "ClientId is Invalid"}]}})
        return httpx.Response(200, json={"access_token": "tok2", "expires_in": "3600"})

    assert ups.token(client=fake(handler)) == "tok2" and tries == [True, False]


def test_state_from_zip():
    assert ups.state_for_zip("11691") == "NY" and ups.state_for_zip("30309") == "GA"
    assert ups.state_for_zip("90210") == "CA" and ups.state_for_zip("73301") == "TX" and ups.state_for_zip("") == ""


def test_checkout_with_ups_rates(shop, keys, monkeypatch):
    from store import shipping
    shipping._cache.clear()
    calls = []

    def fake_rates(from_zip, to_zip, packages=None, **kw):
        calls.append((from_zip, to_zip, packages))
        return [{"code": "03", "service": "UPS Ground", "price": 21.37, "list_price": 30.0, "negotiated": True, "days": None},
                {"code": "02", "service": "UPS 2nd Day Air", "price": 55.1, "list_price": 70.0, "negotiated": True, "days": "2"},
                {"code": "14", "service": "UPS Next Day Air Early", "price": 140.0, "list_price": 150.0, "negotiated": True, "days": "1"}]

    monkeypatch.setattr(ups, "rates", fake_rates)
    c = shop.c
    c.post("/cart/add", data={"product_id": pid(c, "LK-100"), "qty": 2})
    page = c.get("/cart").text
    assert "Shipping to ZIP code" in page and "Enter your ZIP" in page
    assert "Enter your ZIP" in c.post("/checkout", follow_redirects=True).text      # can't check out without a ZIP
    assert "5-digit" in c.post("/cart/zip", data={"zip": "123"}, follow_redirects=True).text
    page = c.post("/cart/zip", data={"zip": "30309"}, follow_redirects=True).text
    assert "UPS Ground" in page and "$21.37" in page and "from $21.37" in page and "Next Day Air Early" not in page
    assert calls[0][0] == "11691" and calls[0][1] == "30309" and len(calls[0][2]) == 2      # two units, two boxes
    c.post("/checkout")
    params = shop.created[-1]
    opts = [o["shipping_rate_data"] for o in params["shipping_options"]]
    assert [o["display_name"] for o in opts] == ["UPS Ground", "UPS 2nd Day Air"]
    assert opts[0]["fixed_amount"]["amount"] == 2137 and opts[1]["delivery_estimate"]["minimum"]["value"] == 2
    assert params["metadata"]["ship_zip"] == "30309" and len(calls) == 1                  # the cart's quote was reused


def test_ups_down_uses_fallback(shop, keys, monkeypatch):
    from store import shipping
    shipping._cache.clear()
    monkeypatch.setattr(ups, "rates", lambda *a, **k: (_ for _ in ()).throw(ups.UPSError("timeout")))
    c = shop.c
    c.post("/cart/add", data={"product_id": pid(c, "LK-100"), "qty": 1})
    page = c.post("/cart/zip", data={"zip": "30309"}, follow_redirects=True).text
    assert "Standard shipping" in page and "standard rate" in page
    c.post("/checkout")
    assert shop.created[-1]["shipping_options"][0]["shipping_rate_data"]["fixed_amount"]["amount"] == 1495


def test_freight_parts_block_checkout(shop, keys, monkeypatch, conn):
    from store import shipping
    shipping._cache.clear()
    monkeypatch.setattr(ups, "rates", lambda *a, **k: [])
    c = shop.c
    p = pid(c, "LK-100")
    conn.execute("UPDATE products SET weight_lb = 200 WHERE id = %s", (p,))
    conn.commit()
    c.post("/cart/add", data={"product_id": p, "qty": 1})
    page = c.post("/cart/zip", data={"zip": "30309"}, follow_redirects=True).text
    assert "freight quote" in page


def test_dims_and_limits():
    from store import shipping
    assert shipping.parse_dims("29.5 x 29.5 x 8.8 in.", [1, 1, 1]) == (29.5, 29.5, 8.8)
    assert shipping.parse_dims(None, [20, 12, 8]) == (20, 12, 8)
    assert shipping.too_big(151, (10, 10, 10)) and shipping.too_big(20, (110, 5, 5)) and not shipping.too_big(45, (30, 30, 9))


def _rates_for_free(monkeypatch):
    from store import shipping
    shipping._cache.clear()
    monkeypatch.setattr(ups, "rates", lambda *a, **k: [
        {"code": "03", "service": "UPS Ground", "price": 21.37, "list_price": 30.0, "negotiated": True, "days": None},
        {"code": "02", "service": "UPS 2nd Day Air", "price": 55.1, "list_price": 70.0, "negotiated": True, "days": "2"}])


def test_free_shipping_rule(shop, keys, monkeypatch, conn):
    from store import shipping
    _rates_for_free(monkeypatch)
    c = shop.c
    c.post("/admin/login", data={"password": "pw"})
    page = c.get("/admin/ups").text
    assert "Free shipping" in page and "Save free shipping" in page
    p = pid(c, "LK-100")
    price = float(conn.execute("SELECT price FROM products WHERE id = %s", (p,)).fetchone()[0])
    # threshold above one unit: the cart says how much more to add
    r = c.post("/admin/ups/free", data={"on": "1", "over": str(price + 10), "max_lb": "50", "service": "03", "lower48": "1"},
               follow_redirects=True)
    assert "Saved." in r.text
    c.post("/cart/add", data={"product_id": p, "qty": 1})
    page = c.post("/cart/zip", data={"zip": "30309"}, follow_redirects=True).text
    assert "more for free UPS Ground" in page and "$10.00" in page and "$21.37" in page
    # two units pass it: Ground is free and listed first, 2nd Day keeps its price
    c.post("/cart/add", data={"product_id": p, "qty": 1})
    page = c.get("/cart").text
    assert "Free UPS Ground" in page and "ships free" in page
    c.post("/checkout")
    opts = [o["shipping_rate_data"] for o in shop.created[-1]["shipping_options"]]
    assert [(o["display_name"], o["fixed_amount"]["amount"]) for o in opts] == [("Free UPS Ground", 0), ("UPS 2nd Day Air", 5510)]
    # Alaska: not free
    assert "lower 48" in c.post("/cart/zip", data={"zip": "99501"}, follow_redirects=True).text
    # a heavy part: not free
    conn.execute("UPDATE products SET weight_lb = 60 WHERE id = %s", (p,))
    conn.commit()
    shipping._cache.clear()
    page = c.post("/cart/zip", data={"zip": "30309"}, follow_redirects=True).text
    assert "over 50 lb" in page and "Free UPS Ground" not in page
    # switched off: plain UPS prices, no note
    c.post("/admin/ups/free", data={"over": "1", "max_lb": "0", "service": "03"})
    page = c.get("/cart").text
    assert "Free UPS Ground" not in page and "free UPS Ground" not in page


def test_free_rule_defaults_and_fallback(conn):
    from store import shipping
    assert shipping.free_rule(conn) == shipping.FREE_DEFAULT
    rule = shipping.save_free_rule(conn, {"on": True, "over": 100, "max_lb": 0, "service": "03", "lower48": True})
    raw = {"options": [{"code": "", "name": "Standard shipping", "amount": 14.95, "days": 7}], "big": [], "live": False,
           "error": "x", "sizes": {1: (5, (20, 12, 8))}}
    q = shipping.apply_free(raw, rule, [{"product_id": 1, "qty": 2, "unit_price": 60}], "30309")
    assert q["free"]["ok"] and q["options"][0]["amount"] == 0 and raw["options"][0]["amount"] == 14.95   # cache untouched


def test_mixed_cart_only_oversized_parts_pay(shop, keys, monkeypatch, conn):
    """A normal part and an oversized one over the minimum: Ground costs only the oversized part's UPS price."""
    from store import shipping
    shipping._cache.clear()
    seen = []

    def fake_rates(from_zip, to_zip, packages=None, **kw):       # $10 a box for Ground, $40 a box for 2nd Day
        seen.append(len(packages))
        return [{"code": "03", "service": "UPS Ground", "price": 10.0 * len(packages), "list_price": 0, "negotiated": True, "days": None},
                {"code": "02", "service": "UPS 2nd Day Air", "price": 40.0 * len(packages), "list_price": 0, "negotiated": True, "days": "2"}]
    monkeypatch.setattr(ups, "rates", fake_rates)
    c = shop.c
    c.post("/admin/login", data={"password": "pw"})
    c.post("/admin/ups/free", data={"on": "1", "over": "1", "max_lb": "50", "service": "03", "lower48": "1", "oversize": "1"})
    normal = pid(c, "LK-100")
    other = conn.execute("SELECT id FROM products WHERE id <> %s AND price > 0 ORDER BY id LIMIT 1", (normal,)).fetchone()[0]
    conn.execute("UPDATE products SET weight_lb = 5, package_dims = '20 x 12 x 8 in.' WHERE id = %s", (normal,))
    conn.execute("UPDATE products SET weight_lb = 30, package_dims = '60 x 10 x 8 in.' WHERE id = %s", (other,))   # long box
    conn.commit()
    c.post("/cart/add", data={"product_id": normal, "qty": 1})
    c.post("/cart/add", data={"product_id": other, "qty": 1})
    page = c.post("/cart/zip", data={"zip": "30309"}, follow_redirects=True).text
    assert "free except oversized parts" in page and "oversized box" in page and "$10.00" in page
    c.post("/checkout")
    opts = [(o["shipping_rate_data"]["display_name"], o["shipping_rate_data"]["fixed_amount"]["amount"])
            for o in shop.created[-1]["shipping_options"]]
    assert opts == [("UPS Ground (free except oversized parts)", 1000), ("UPS 2nd Day Air", 8000)]
    # with the oversized setting off, the long box is fine: everything ships free
    c.post("/admin/ups/free", data={"on": "1", "over": "1", "max_lb": "50", "service": "03", "lower48": "1"})
    assert "Free UPS Ground" in c.get("/cart").text


def test_surcharge_sizes():
    from store import shipping
    assert not shipping.ups_surcharge(20, (40, 20, 10))
    assert shipping.ups_surcharge(51, (20, 12, 8))           # over 50 lb
    assert shipping.ups_surcharge(10, (49, 10, 10))          # longest side over 48 in
    assert shipping.ups_surcharge(10, (40, 31, 5))           # second side over 30 in
    assert shipping.ups_surcharge(10, (44, 30, 30))          # length + girth 164 > 130
