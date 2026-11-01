"""Streaming reader for ACES 4.0 XML (fitment applications).

Vehicles are stored as VCdb ID numbers. We keep every ID so engine, drive type and the rest can
be decoded once the VCdb reference database is loaded. Vehicle names for the storefront come
from the Admark Vehicle Assignment sheet (see vehicles.py).
"""
import xml.etree.ElementTree as ET

# Tags that are not VCdb vehicle attributes.
_NON_VCDB = {"Years", "Qty", "PartType", "Part", "MfrLabel", "Position", "Note", "Qual", "AssetName", "AssetItemOrder", "AssetItemRef", "DisplayOrder"}


def read_header(path):
    for event, el in ET.iterparse(path, events=("end",)):
        if el.tag == "Header":
            return {c.tag: " ".join((c.text or "").split()) for c in el}
        if el.tag == "App":
            break
    return {}


def read_apps(path):
    """Yield one dict per ACES <App>."""
    for event, el in ET.iterparse(path, events=("end",)):
        if el.tag != "App":
            continue
        app = {"app_id": el.get("id"), "action": el.get("action", "A"), "vcdb": {}, "qualifiers": [], "notes": []}
        for c in el:
            tag = c.tag
            if tag == "Part":
                app["part"] = (c.text or "").strip()
            elif tag == "Years":
                app["year_from"], app["year_to"] = int(c.get("from")), int(c.get("to"))
            elif tag == "BaseVehicle":
                app["base_vehicle_id"] = int(c.get("id"))
            elif tag == "Make":
                app["make_id"] = int(c.get("id"))
            elif tag == "Model":
                app["model_id"] = int(c.get("id"))
            elif tag == "Qty":
                app["qty"] = (c.text or "").strip()
            elif tag == "PartType":
                app["part_type_id"] = int(c.get("id"))
            elif tag == "Position":
                app["position_id"] = int(c.get("id"))
            elif tag == "MfrLabel":
                app["mfr_label"] = " ".join((c.text or "").split())
            elif tag == "Note":
                if c.text and c.text.strip():
                    app["notes"].append(" ".join(c.text.split()).strip('"'))
            elif tag == "Qual":
                t = c.find("text")
                if t is not None and t.text:
                    app["qualifiers"].append(" ".join(t.text.split()))
            elif tag not in _NON_VCDB and c.get("id"):
                app["vcdb"][tag] = int(c.get("id")) if c.get("id").isdigit() else c.get("id")
        yield app
        el.clear()
