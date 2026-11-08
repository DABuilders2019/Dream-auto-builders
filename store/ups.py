"""UPS: connection test and shipping rates (Rating API, "Shop" = every service in one call).

Settings (Render > Environment): UPS_CLIENT_ID, UPS_CLIENT_SECRET, UPS_ACCOUNT_NUMBER, UPS_ENV ("test" uses UPS's
testing server wwwcie.ups.com; "production" uses onlinetools.ups.com).

For now this only powers the test on Admin > UPS. Rates at checkout come once the shipping rules are decided.
"""
import os
import threading
import time

import httpx

SERVICES = {"01": "UPS Next Day Air", "02": "UPS 2nd Day Air", "03": "UPS Ground", "12": "UPS 3 Day Select",
            "13": "UPS Next Day Air Saver", "14": "UPS Next Day Air Early", "59": "UPS 2nd Day Air A.M.",
            "75": "UPS Heavy Goods", "92": "UPS SurePost Less than 1 lb", "93": "UPS SurePost 1 lb or Greater"}
RATING_VERSION = "v2409"


class UPSError(Exception):
    pass


def settings():
    env = (os.environ.get("UPS_ENV") or "test").strip().lower()
    return {
        "client_id": (os.environ.get("UPS_CLIENT_ID") or "").strip().strip('"'),
        "client_secret": (os.environ.get("UPS_CLIENT_SECRET") or "").strip().strip('"'),
        "account": (os.environ.get("UPS_ACCOUNT_NUMBER") or "").strip().strip('"').upper(),
        "env": "production" if env.startswith("prod") else "test",
    }


def base_url(cfg=None):
    cfg = cfg or settings()
    return "https://onlinetools.ups.com" if cfg["env"] == "production" else "https://wwwcie.ups.com"


def configured():
    c = settings()
    return bool(c["client_id"] and c["client_secret"])


_token = {"value": None, "until": 0.0, "key": None}
_lock = threading.Lock()


def _errors(resp):
    try:
        data = resp.json()
        errs = (data.get("response") or {}).get("errors") or data.get("errors") or []
        msgs = [f"{e.get('code', '')} {e.get('message', '')}".strip() for e in errs if isinstance(e, dict)]
        if msgs:
            return "; ".join(msgs)
    except ValueError:
        pass
    return (resp.text or "").strip()[:300] or f"HTTP {resp.status_code}"


def token(client=None):
    """An OAuth access token (client credentials), kept until shortly before it expires."""
    cfg = settings()
    if not (cfg["client_id"] and cfg["client_secret"]):
        raise UPSError("UPS_CLIENT_ID and UPS_CLIENT_SECRET aren't set in Render > Environment.")
    key = (cfg["client_id"], cfg["env"])
    with _lock:
        if _token["value"] and _token["key"] == key and time.time() < _token["until"]:
            return _token["value"]
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if cfg["account"]:
        headers["x-merchant-id"] = cfg["account"]
    c = client or httpx.Client(timeout=20)
    try:
        r = c.post(f"{base_url(cfg)}/security/v1/oauth/token", data={"grant_type": "client_credentials"},
                   auth=(cfg["client_id"], cfg["client_secret"]), headers=headers)
        if r.status_code == 401 and "x-merchant-id" in headers:
            # some UPS apps refuse the sign-in when the account number header is sent; try once without it
            r2 = c.post(f"{base_url(cfg)}/security/v1/oauth/token", data={"grant_type": "client_credentials"},
                        auth=(cfg["client_id"], cfg["client_secret"]),
                        headers={"Content-Type": "application/x-www-form-urlencoded"})
            if r2.status_code == 200:
                r = r2
    except httpx.HTTPError as e:
        raise UPSError(f"Couldn't reach UPS: {e}") from e
    finally:
        if client is None:
            c.close()
    if r.status_code != 200:
        raise UPSError(f"UPS refused the sign-in (HTTP {r.status_code}): {_errors(r)}")
    data = r.json()
    with _lock:
        _token.update(value=data["access_token"], key=key,
                      until=time.time() + max(60, int(data.get("expires_in", 3600))) - 120)
    return data["access_token"]


def rates(from_zip, to_zip, weight_lb, dims=(20, 12, 8), from_state="", to_state="", residential=True, client=None):
    """Every UPS service's price for one package: [{"code", "service", "price", "list_price", "days"}], cheapest first."""
    cfg = settings()
    tok = token(client)

    def addr(z, st):
        a = {"PostalCode": str(z).strip()[:10], "CountryCode": "US"}
        if st:
            a["StateProvinceCode"] = st.strip().upper()[:2]
        return a

    shipper = {"Name": "Dream Auto Builders", "Address": addr(from_zip, from_state)}
    shipment = {
        "Shipper": shipper,
        "ShipFrom": {"Name": "Dream Auto Builders", "Address": addr(from_zip, from_state)},
        "ShipTo": {"Name": "Customer", "Address": {**addr(to_zip, to_state), **({"ResidentialAddressIndicator": ""} if residential else {})}},
        "Package": [{
            "PackagingType": {"Code": "02"},
            "Dimensions": {"UnitOfMeasurement": {"Code": "IN"}, "Length": f"{dims[0]:g}", "Width": f"{dims[1]:g}", "Height": f"{dims[2]:g}"},
            "PackageWeight": {"UnitOfMeasurement": {"Code": "LBS"}, "Weight": f"{max(0.1, float(weight_lb)):g}"},
        }],
    }
    if cfg["account"]:
        shipper["ShipperNumber"] = cfg["account"]
        shipment["PaymentDetails"] = {"ShipmentCharge": [{"Type": "01", "BillShipper": {"AccountNumber": cfg["account"]}}]}
        shipment["ShipmentRatingOptions"] = {"NegotiatedRatesIndicator": ""}
    body = {"RateRequest": {"Request": {"RequestOption": "Shop", "TransactionReference": {"CustomerContext": "Dream Auto Builders rates"}},
                            "Shipment": shipment}}
    c = client or httpx.Client(timeout=25)
    try:
        r = c.post(f"{base_url(cfg)}/api/rating/{RATING_VERSION}/Shop", json=body,
                   headers={"Authorization": f"Bearer {tok}", "transId": f"dab{int(time.time() * 1000)}",
                            "transactionSrc": "dreamautobuilders"})
    except httpx.HTTPError as e:
        raise UPSError(f"Couldn't reach UPS: {e}") from e
    finally:
        if client is None:
            c.close()
    if r.status_code != 200:
        raise UPSError(f"UPS couldn't price that (HTTP {r.status_code}): {_errors(r)}")
    rated = (r.json().get("RateResponse") or {}).get("RatedShipment") or []
    if isinstance(rated, dict):
        rated = [rated]
    out = []
    for s in rated:
        code = (s.get("Service") or {}).get("Code", "")
        list_price = float((s.get("TotalCharges") or {}).get("MonetaryValue") or 0)
        neg = ((s.get("NegotiatedRateCharges") or {}).get("TotalCharge") or {}).get("MonetaryValue")
        days = ((s.get("GuaranteedDelivery") or {}).get("BusinessDaysInTransit"))
        out.append({"code": code, "service": SERVICES.get(code, f"UPS service {code}"),
                    "price": float(neg) if neg else list_price, "list_price": list_price,
                    "negotiated": bool(neg), "days": days})
    out.sort(key=lambda x: x["price"])
    return out
