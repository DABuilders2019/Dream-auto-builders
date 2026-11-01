"""Brand and category configuration (config/brands.json, config/catalog.json)."""
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"


def brands():
    return {b["id"]: b for b in json.loads((CONFIG / "brands.json").read_text())["brands"]}


def brand_by_aaia(aaia_id):
    for b in brands().values():
        if b.get("aaia_id") == aaia_id:
            return b
    return None


def catalog():
    return json.loads((CONFIG / "catalog.json").read_text())


def image_reviews():
    path = CONFIG / "image_reviews.json"
    return json.loads(path.read_text())["reviews"] if path.exists() else []


def extra_images():
    """Photos found outside SEMA (e.g. the brand's own product page), matched by exact part number."""
    path = CONFIG / "extra_images.json"
    return json.loads(path.read_text())["images"] if path.exists() else []
