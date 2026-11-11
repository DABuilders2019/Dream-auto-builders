"""Detailing category; Cargo Liners (Interior), Filter Care (Air Intake), Brake Pads and Rotors (Brakes)."""
import json
import pathlib

from importer.catalog import Categorizer
from test_checkout import shop  # noqa: F401

CAT = json.loads((pathlib.Path(__file__).resolve().parent.parent / "config" / "catalog.json").read_text())


def test_every_category_has_at_least_three_subcategories():
    for c in CAT["categories"]:
        if c["id"] != "uncategorized":
            assert len(c["subs"]) >= 3, c["name"]


def test_new_subcategories_get_the_right_parts():
    k = Categorizer(CAT)
    cases = {
        ("Car Wash Soap", ""): "wash-wax", ("Polishing Compound", ""): "polish-coatings",
        ("Leather Cleaner", ""): "interior-care", ("Microfiber Towel", ""): "detailing-tools",
        ("Cleaner", "mr pink car wash shampoo"): "wash-wax", ("Cleaner", "leather cleaner for seats"): "interior-care",
        ("Cleaner", "fuel system cleaner"): "additives", ("Thread Locking Compound", ""): "uncategorized",
        ("Disc Brake Pad Set", ""): "brake-pads", ("Disc Brake Rotor", ""): "brake-rotors",
        ("Cargo Liner", ""): "cargo-liners", ("Floor Mat", ""): "floor-liners",
        ("Air Filter Cleaner", ""): "intake-accessories", ("Air Filter", ""): "air-filters",
        ("Bucket Seat", ""): "interior-accessories",
    }
    for (pt, text), want in cases.items():
        assert k.place(pt, text, "") == want, (pt, text)


def test_old_pads_rotors_link_redirects(shop):
    r = shop.c.get("/c/pads-rotors", follow_redirects=False)
    assert r.status_code == 301 and r.headers["location"] == "/c/brake-pads"
