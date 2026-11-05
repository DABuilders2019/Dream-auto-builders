"""Reader for SEMA's "Admark Vehicle Assignment Template" export (xlsx).

Each row is one part on one vehicle, written out in words: Make, Model, Year, Submodel,
Engine, Body, and so on. This is what powers the Year / Make / Model picker until the VCdb
reference database is available to decode the ACES ID numbers directly.
"""
import openpyxl

# Header name -> our field. The sheet repeats "Submodel"; the first one is the one SEMA fills.
_FIELDS = {
    "Make": "make", "Model": "model", "Year": "year", "Submodel": "submodel",
    "Submodel two": "submodel2", "Submodel three": "submodel3", "Product Name": "product_name",
    "SKU": "sku", "Notes": "notes", "Body": "body", "Engine": "engine", "Transmission": "transmission",
    "Bed Info": "bed", "Wheelbase (in)": "wheelbase", "Mfr Label": "mfr_label", "Position": "position",
    "Engine Range": "engine_range", "Transmission Range": "transmission_range",
}


def _clean(v):
    if v is None:
        return None
    v = " ".join(str(v).split()).strip('"').strip()
    return v or None


def read_rows(path):
    """Yield one dict per vehicle-sheet row (.xlsx), or per line of a SEMA API fitment file (.jsonl)."""
    if str(path).lower().endswith(".jsonl"):
        yield from _read_jsonl(path)
        return
    ws = openpyxl.load_workbook(path, read_only=True).worksheets[0]
    rows = ws.iter_rows(values_only=True)
    header = next(rows)
    index = {}
    for i, name in enumerate(header):
        field = _FIELDS.get(name)
        if field and field not in index:
            index[field] = i
    missing = {"make", "model", "year", "sku"} - set(index)
    if missing:
        raise ValueError(f"{path}: vehicle sheet is missing columns {sorted(missing)}")
    for r in rows:
        row = {f: _clean(r[i]) if i < len(r) else None for f, i in index.items()}
        if not (row["make"] and row["model"] and row["year"] and row["sku"]):
            continue
        try:
            row["year"] = int(float(row["year"]))
        except ValueError:
            continue
        yield row


def _read_jsonl(path):
    import json
    with open(path, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            row = {k: _clean(r.get(k)) for k in ("sku", "make", "model", "submodel", "notes", "year")}
            if not (row["make"] and row["model"] and row["year"] and row["sku"]):
                continue
            try:
                row["year"] = int(float(row["year"]))
            except ValueError:
                continue
            yield row
