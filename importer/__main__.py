"""Command line for the importer.

  python -m importer migrate
  python -m importer load    --pies PIES.xml --aces ACES.xml --vehicles ADMARK.xlsx [--brand readylift]
  python -m importer photos  --brand readylift [--zip DIGITAL_ASSETS.zip] [--max-per-product 4]
  python -m importer report  --brand readylift --out report.xlsx
  python -m importer status

Files may be .zip archives as downloaded from SEMA Data; the XML/XLSX inside is used.
The brand is found from the brand code inside the PIES file. A brand that isn't in
config/brands.json is added with default settings (--brand-name sets its display name).
DATABASE_URL must point at the Postgres database.
"""
import argparse
import json
import pathlib
import sys
import tempfile
import zipfile

from . import brands, db, load, photos, report


def _unzip(path, suffix):
    """Accept either the file itself or the SEMA .zip that contains it."""
    if not path or not path.lower().endswith(".zip"):
        return path
    z = zipfile.ZipFile(path)
    names = [n for n in z.namelist() if n.lower().endswith(suffix)]
    if len(names) != 1:
        raise SystemExit(f"{path}: expected one {suffix} file inside, found {names}")
    out = pathlib.Path(tempfile.mkdtemp()) / pathlib.Path(names[0]).name
    out.write_bytes(z.read(names[0]))
    return str(out)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m importer", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="create/upgrade database tables")
    p = sub.add_parser("load", help="load a brand's PIES, ACES and vehicle files")
    p.add_argument("--brand", help="brand id (optional: found from the PIES file)")
    p.add_argument("--brand-name", help="display name for a brand that isn't set up yet")
    p.add_argument("--pies", required=True)
    p.add_argument("--aces")
    p.add_argument("--vehicles", help="Admark Vehicle Assignment Template (.xlsx or its .zip)")
    p = sub.add_parser("photos", help="download and check product photos")
    p.add_argument("--brand", required=True)
    p.add_argument("--zip", help="SEMA Digital Assets zip (optional; links are used otherwise)")
    p.add_argument("--max-per-product", type=int, default=4)
    p.add_argument("--limit-products", type=int)
    p.add_argument("--workers", type=int, default=12)
    p.add_argument("--retry-failed", action="store_true", help="also try again photos that failed for a temporary reason")
    sub.add_parser("trim-photos", help="one time: trim the white border of photos already on the site")
    p = sub.add_parser("report", help="write the photo report spreadsheet")
    p.add_argument("--brand", required=True)
    p.add_argument("--out", required=True)
    sub.add_parser("status", help="show catalog counts and the latest import runs")
    a = ap.parse_args(argv)

    conn = db.connect()
    if a.cmd == "migrate":
        print("applied:", db.migrate(conn) or "nothing new")
    elif a.cmd == "load":
        db.migrate(conn)
        pies_path = _unzip(a.pies, ".xml")
        aaia, label = brands.peek(pies_path)
        try:
            brand = brands.resolve(conn, aaia, label, a.brand_name, a.brand)
        except ValueError as e:
            raise SystemExit(f"Can't load this file: {e}")
        print(f"Loading {brand['name']} (brand code {aaia}){' as a new brand' if brand.get('new') else ''} ...", flush=True)
        stats = load.load_brand(conn, brand, pies_path, _unzip(a.aces, ".xml"), _unzip(a.vehicles, ".xlsx"))
        print(json.dumps({k: v for k, v in stats.items() if k != "parts_missing_fitment"}, indent=2))
    elif a.cmd == "photos":
        print(f"Photos for {a.brand} ...")
        stats, summary = photos.run(conn, a.brand, a.zip, a.max_per_product, a.limit_products, a.workers,
                                    retry_failed=a.retry_failed)
        print(json.dumps({"files": stats, "products": summary["counts"], "possible_logos": len(summary["suspicious"])}, indent=2))
    elif a.cmd == "trim-photos":
        print("Evening out the margins of photos already on the site ...", flush=True)
        n = photos.trim_existing(conn, log=lambda m: print(m, flush=True))
        conn.execute("INSERT INTO app_state (key, value) VALUES ('photos_trimmed', 'true') "
                     "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value")
        conn.commit()
        print(json.dumps({"photos_trimmed": n}))
    elif a.cmd == "report":
        path = report.photo_report(conn, a.brand, a.out)
        print("wrote", path)
    elif a.cmd == "status":
        report.print_status(conn)
    return 0


if __name__ == "__main__":
    sys.exit(main())
