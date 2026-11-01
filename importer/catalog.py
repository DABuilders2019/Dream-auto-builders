"""Turn a raw PIES item into a store product: name, category, price, specs, emissions status."""
import re

from . import config

PROP65_DEFAULT = ("WARNING: This product can expose you to chemicals known to the State of California to cause "
                  "cancer and/or reproductive harm. For more information go to www.P65Warnings.ca.gov.")

# Attributes that are handled separately or are not useful as specs.
_NOT_SPECS = {"Title", "California Proposition 65", "Prop 65 Y/N", "CARB Executive Order Number", "Fitment"}


class Categorizer:
    def __init__(self, catalog=None):
        self.rules = (catalog or config.catalog())["rules"]

    def place(self, part_type, text):
        pt = (part_type or "").strip()
        low = (text or "").lower()
        for rule in self.rules:
            if "part_type" in rule and pt not in rule["part_type"]:
                continue
            if "part_type_contains" in rule and not any(k.lower() in pt.lower() for k in rule["part_type_contains"]):
                continue
            if "text_contains" in rule and not any(k in low for k in rule["text_contains"]):
                continue
            return rule["category"]
        return "uncategorized"


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def short_name(item, brand):
    d = item["descriptions"]
    name = item["attributes"].get("Title") or (d.get("EXT") or d.get("DES") or d.get("SHO") or d.get("INV") or [item["part_number"]])[0]
    for prefix in sorted(brand.get("strip_prefix", []), key=len, reverse=True):
        if name.lower().startswith(prefix.lower() + " "):
            name = name[len(prefix) + 1:]
            break
    return name.strip(" -")


def choose_price(prices, preference):
    by_type = {p["type"]: p["amount"] for p in prices}
    for t in preference:
        if by_type.get(t):
            return by_type[t], t
    return None, None


def build_product(item, brand, categorizer, file_date=None):
    """Return the products-table row (as a dict) for one PIES item."""
    d, attrs = item["descriptions"], item["attributes"]
    name = short_name(item, brand)
    part_type = (d.get("DEF") or [None])[0]
    category = categorizer.place(part_type, " ".join([name] + d.get("SHO", []) + d.get("DES", []) + d.get("EXT", [])))
    price, price_type = choose_price(item["prices"], brand.get("display_price", ["RET", "RMP", "LST"]))
    map_price = next((p["amount"] for p in item["prices"] if p["type"] == "RMP"), None)

    features = [f for f in dict.fromkeys(d.get("FAB", [])) if f != name][:12]
    eo = attrs.get("CARB Executive Order Number")
    eo = eo if eo and eo.lower() not in ("not applicable", "n/a", "na", "none") else None
    emissions = None
    if category == "catalytic-converters":
        emissions = "carb_legal" if eo else "federal_only"

    prop65 = attrs.get("California Proposition 65")
    if not prop65 and attrs.get("Prop 65 Y/N", "").lower() in ("y", "yes"):
        prop65 = PROP65_DEFAULT

    ext = item["extended"]
    specs = {"Part Number": item["part_number"], "Brand": brand["name"]}
    if part_type:
        specs["Part Type"] = part_type
    for k, v in attrs.items():
        if k not in _NOT_SPECS and not k.startswith("Prop65") and len(v) < 120:
            specs[k] = v
    if emissions == "carb_legal":
        specs["Emissions"] = f"California legal (CARB EO {eo})"
    elif emissions == "federal_only":
        specs["Emissions"] = "Federal (49-state). Not legal for sale in California or CARB states."
    if ext.get("CTO"):
        specs["Country of Origin"] = ext["CTO"]
    upc = (item.get("gtin") or "").lstrip("0") or None
    if upc:
        specs["UPC"] = upc
    pkg = item["package"]
    if pkg.get("weight_lb"):
        specs["Shipping Weight"] = f"{pkg['weight_lb']:g} lb"
    if pkg.get("dims"):
        specs["Package Dimensions"] = pkg["dims"]

    if item["maintenance_type"] == "D":
        status = "discontinued"
    elif price is None:
        status = "hidden"          # no price in the feed: keep it off the storefront
    else:
        status = "active"

    return {
        "brand_id": brand["id"],
        "part_number": item["part_number"],
        "slug": brand["id"] + "-" + slugify(item["part_number"]),
        "status": status,
        "title": f"{brand['name']} {name}",
        "short_name": name,
        "part_type": part_type,
        "part_terminology_id": item.get("part_terminology_id"),
        "category_id": category,
        "price": price,
        "price_type": price_type,
        "map_price": map_price,
        "upc": upc,
        "weight_lb": pkg.get("weight_lb"),
        "package_dims": pkg.get("dims"),
        "country_of_origin": ext.get("CTO"),
        "marketing_copy": (d.get("MKT") or [None])[0],
        "features": features,
        "specs": specs,
        "attributes": {**attrs, **{"padb:" + k: v for k, v in item["padb_attributes"].items()}},
        "descriptions": d,
        "prop65_warning": prop65,
        "carb_eo": eo,
        "emissions": emissions,
        "source_file_date": file_date,
    }


PHOTO_TYPES = ("JPG", "JPEG", "PNG", "WEBP", "GIF", "TIF", "TIFF")


def photo_assets(item):
    """Product photos from the PIES digital assets, primary (P04) first. Skips 360 spins, logos, videos, PDFs."""
    photos = [a for a in item["assets"] if a["asset_type"].startswith("P0") and a["file_type"] in PHOTO_TYPES]
    photos.sort(key=lambda a: (0 if a["asset_type"] == "P04" else 1, a["order"]))
    return photos


def install_docs(item):
    return [a["uri"] for a in item["assets"] if a["asset_type"] == "INS" and a["uri"]]
