"""Streaming reader for PIES 7.0 XML (product information).

The files are large (MagnaFlow's is ~180 MB), so items are read one at a time with iterparse
and cleared from memory as soon as they've been turned into a plain dict.
"""
import xml.etree.ElementTree as ET


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _text(el):
    return " ".join((el.text or "").split()) if el is not None else ""


def read_header(path):
    """Return the PIES header fields (SubmissionType, BlanketEffectiveDate, ...)."""
    header = {}
    for event, el in ET.iterparse(path, events=("end",)):
        name = _local(el.tag)
        if name == "Header":
            header = {_local(c.tag): _text(c) for c in el}
            break
        if name == "Item":
            break
    return header


def read_items(path):
    """Yield one dict per PIES <Item>."""
    for event, el in ET.iterparse(path, events=("end",)):
        if _local(el.tag) != "Item":
            continue
        yield _item(el)
        el.clear()


def _item(el):
    item = {
        "maintenance_type": el.get("MaintenanceType", "A"),
        "descriptions": {},
        "prices": [],
        "attributes": {},
        "padb_attributes": {},
        "extended": {},
        "assets": [],
        "package": {},
    }
    for child in el:
        name = _local(child.tag)
        if name == "PartNumber":
            item["part_number"] = _text(child)
        elif name == "BrandAAIAID":
            item["brand_aaia"] = _text(child)
        elif name == "BrandLabel":
            item["brand_label"] = _text(child)
        elif name == "ItemLevelGTIN":
            item["gtin"] = _text(child)
        elif name == "PartTerminologyID":
            item["part_terminology_id"] = int(_text(child)) if _text(child).isdigit() else None
        elif name == "Descriptions":
            for d in child:
                if d.text and d.text.strip():
                    item["descriptions"].setdefault(d.get("DescriptionCode"), []).append(_text(d))
        elif name == "Prices":
            for p in child:
                amount = p.find("{*}Price")
                if amount is None or not _text(amount):
                    continue
                cur = p.find("{*}CurrencyCode")
                item["prices"].append({
                    "type": p.get("PriceType"),
                    "amount": round(float(_text(amount)), 2),
                    "currency": _text(cur) or "USD",
                    "uom": amount.get("UOM"),
                })
        elif name == "ProductAttributes":
            for a in child:
                value = _text(a)
                if not value:
                    continue
                key = a.get("AttributeID")
                # PADB attributes are numeric ids that need the PAdb reference table to name them.
                target = item["padb_attributes"] if a.get("PADBAttribute") == "Y" else item["attributes"]
                target.setdefault(key, value)
        elif name == "ExtendedInformation":
            for x in child:
                item["extended"][x.get("EXPICode")] = _text(x)
        elif name == "Packages":
            pk = child.find("{*}Package")
            if pk is not None:
                w = pk.find("{*}Weights/{*}Weight")
                wu = pk.find("{*}Weights")
                if w is not None and _text(w):
                    weight = float(_text(w))
                    unit = (wu.get("UOM") if wu is not None else "PG") or "PG"
                    item["package"]["weight_lb"] = round(weight * 2.20462, 2) if unit.upper() in ("KG",) else round(weight, 2)
                dm = pk.find("{*}Dimensions")
                if dm is not None:
                    vals = [_text(dm.find("{*}" + k)) for k in ("Length", "Width", "Height")]
                    if all(vals):
                        unit = (dm.get("UOM") or "IN").lower()
                        item["package"]["dims"] = " x ".join(f"{float(v):g}" for v in vals) + (" in." if unit == "in" else " " + unit)
        elif name == "DigitalAssets":
            for i, d in enumerate(child):
                g = lambda k: _text(d.find("{*}" + k))
                item["assets"].append({
                    "asset_type": g("AssetType"),
                    "file_type": g("FileType").upper(),
                    "file_name": g("FileName"),
                    "uri": g("URI"),
                    "order": i,
                })
    return item
