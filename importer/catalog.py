"""Turn a raw PIES item into a store product: name, category, price, specs, emissions status."""
import re

from . import config

PROP65_DEFAULT = ("WARNING: This product can expose you to chemicals known to the State of California to cause "
                  "cancer and/or reproductive harm. For more information go to www.P65Warnings.ca.gov.")

# Attributes that are handled separately or are not useful as specs.
_NOT_SPECS = {"Title", "CARB Executive Order Number", "Fitment", "Clean Oil", "Brand Code"}


def _is_prop65_key(k):
    k = re.sub(r"[^a-z0-9]", "", k.lower())
    return "prop65" in k or "proposition65" in k


def prop65_warning(attrs):
    """Brands label Prop 65 differently ('California Proposition 65', 'Prop 65 Y/N', 'Prop 65 Warning Label'...)."""
    flagged = False
    for k, v in attrs.items():
        if not _is_prop65_key(k):
            continue
        if v.strip().upper().startswith("WARNING"):
            return v
        if v.strip().lower().startswith("y"):
            flagged = True
    return PROP65_DEFAULT if flagged else None


SIZE_RE = re.compile(r"(?<![0-9.])(?:P|LT)?(\d{1,3}(?:\.\d{1,2})?[/X]\d{1,2}(?:\.\d{1,2})?(?:Z?R|-)\d{2}(?:\.\d)?)", re.I)


def size_norm(text):
    """Tire size with punctuation removed: 'LT285/70R17' -> '28570r17', '35X12.50R20LT' -> '351250r20'."""
    m = SIZE_RE.search(text or "")
    return normalize_size(m.group(1)) if m else None


def normalize_size(s):
    """Same key however a size is written: 35X12.50R20 = 35x12.5r20, 26.0/10.0-15 = 26x10-15, 275/40ZR18 = 275/40R18."""
    s = s.lower()
    s = re.sub(r"(\.\d)0(?=\D|$)", r"\1", s)      # 12.50 -> 12.5
    s = re.sub(r"\.0(?=\D|$)", "", s)               # 26.0 -> 26
    s = s.replace("/", "x").replace("zr", "r")
    return re.sub(r"[^a-z0-9]", "", s)


_KEEP_CASE = re.compile(r"[0-9/]|^(MT|LT|DC|SD|MM|ET|JR|II|III|IV|XS|SXS|ATZ|MTZ|GHT|EXP|OD|BC|SB|STD|SUV|UTV)$")


def _title(word):
    return word if _KEEP_CASE.search(word) else word.capitalize()


def _size_last(text):
    """'LT325/50R22 122Q BAJA ATZP3' -> 'Baja ATZP3 LT325/50R22 122Q'."""
    words = text.split()
    lead = []
    while words and re.search(r"\d", words[0]):
        lead.append(words.pop(0))
    return " ".join([_title(w) for w in words] + lead)


class Categorizer:
    def __init__(self, catalog=None):
        self.rules = (catalog or config.catalog())["rules"]

    def place(self, part_type, text, name=""):
        pt = (part_type or "").strip()
        low = (text or "").lower()
        padded_name = " " + (name or "").lower() + " "
        for rule in self.rules:
            if "name_contains" in rule and not any(k in padded_name for k in rule["name_contains"]):
                continue
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
    src = brand.get("name_source")
    if src and d.get(src):
        name = d[src][0]
        if brand.get("name_style") == "size_last":
            name = _size_last(name)
        part_type = (d.get("DEF") or [""])[0]
        if part_type and part_type.split()[-1].lower() not in name.lower():
            name += " " + part_type
    elif src:
        a = item["attributes"]
        line, size = a.get("Brand Line"), a.get("Tire Size") or a.get("Product Size")
        base = " ".join(_title(w) for w in (line or "").split())
        name = " ".join(x for x in (base, size, (d.get("DEF") or [""])[0]) if x) or f"{(d.get('DES') or [''])[0].title()} {item['part_number']}"
    else:
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
    category = categorizer.place(part_type, " ".join([name] + d.get("SHO", []) + d.get("DES", []) + d.get("EXT", [])), name)
    price, price_type = choose_price(item["prices"], brand.get("display_price", ["RET", "RMP", "LST"]))
    map_price = next((p["amount"] for p in item["prices"] if p["type"] == "RMP"), None)

    features = [f for f in dict.fromkeys(d.get("FAB", [])) if f != name][:12]
    eo = attrs.get("CARB Executive Order Number")
    eo = eo if eo and eo.lower() not in ("not applicable", "n/a", "na", "none") else None
    emissions = None
    if category == "catalytic-converters":
        emissions = "carb_legal" if eo else "federal_only"

    prop65 = prop65_warning(attrs)

    ext = item["extended"]
    specs = {"Part Number": item["part_number"], "Brand": brand["name"]}
    if part_type:
        specs["Part Type"] = part_type
    for k, v in attrs.items():
        if k not in _NOT_SPECS and not _is_prop65_key(k) and len(v) < 120:
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
        "size_norm": size_norm(attrs.get("Tire Size") or name) if category.endswith("tires") or part_type == "Tire" else None,
    }


PHOTO_TYPES = ("JPG", "JPEG", "PNG", "WEBP", "GIF", "TIF", "TIFF")


def photo_assets(item):
    """Product photos from the PIES digital assets, primary (P04) first. Skips 360 spins, logos, videos, PDFs."""
    photos = [a for a in item["assets"] if a["asset_type"][:2] in ("P0", "ZZ") and a["file_type"] in PHOTO_TYPES]
    # P04 = primary photo, other P0x = more product photos, ZZ1-ZZ9 = brand-defined extra views
    photos.sort(key=lambda a: (0 if a["asset_type"] == "P04" else 1 if a["asset_type"].startswith("P0") else 2, a["order"]))
    return photos


def install_docs(item):
    return [a["uri"] for a in item["assets"] if a["asset_type"] == "INS" and a["uri"]]
