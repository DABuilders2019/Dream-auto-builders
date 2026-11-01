"""Reports: photo report spreadsheet and a quick catalog status."""
import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill

from . import photos

RED = "A8131A"
FILLS = {"Part photo": "E2F0E6", "Part photo (from the web)": "E2F0E6", "Vehicle or box photo only": "FFF6CC",
         "No usable photo": "F8D7DA", "Not checked yet": "EEEEEE"}
ORDER = ["No usable photo", "Not checked yet", "Vehicle or box photo only", "Part photo (from the web)", "Part photo"]


def photo_report(conn, brand_id, out):
    summary = photos.summarize(conn, brand_id)
    rows = sorted(summary["products"], key=lambda p: (ORDER.index(p["photo_status"]), p["part_number"]))
    wb = openpyxl.Workbook()
    hdr, body = Font(name="Arial", bold=True, color="FFFFFF"), Font(name="Arial", size=10)

    s = wb.active
    s.title = "Summary"
    s["A1"] = f"Photo report: {brand_id}"
    s["A1"].font = Font(name="Arial", bold=True, size=14, color=RED)
    s.append([])
    s.append(["Photo status", "Products"])
    for c in s[3]:
        c.font, c.fill = hdr, PatternFill("solid", fgColor=RED)
    n = len(rows) + 1
    for i, st in enumerate(ORDER):
        s.append([st, f"=COUNTIF('Products'!$C$2:$C${n},A{4 + i})"])
        s[f"A{4 + i}"].fill = PatternFill("solid", fgColor=FILLS[st])
    s.append(["Total", f"=SUM(B4:B{3 + len(ORDER)})"])
    s.column_dimensions["A"].width = 30

    ws = wb.create_sheet("Products")
    ws.append(["Part Number", "Product Name", "Photo Status", "Part Photos", "Vehicle Photos", "Blocked", "Failed", "Part Type", "Price", "First Photo Link"])
    for p in rows:
        ws.append([p["part_number"], p["short_name"], p["photo_status"], p["part_photos"], p["vehicle_photos"], p["blocked"],
                   p["failed"], p["part_type"], float(p["price"]) if p["price"] is not None else None, p["first_link"]])
    for c in ws[1]:
        c.font, c.fill = hdr, PatternFill("solid", fgColor=RED)
    for r in ws.iter_rows(min_row=2):
        for c in r:
            c.font = body
        r[2].fill = PatternFill("solid", fgColor=FILLS[r[2].value])
        r[8].number_format = "$#,##0.00"
    for col, w in zip("ABCDEFGHIJ", [14, 60, 24, 11, 11, 9, 8, 32, 11, 50]):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = f"A1:J{ws.max_row}"

    if summary["suspicious"]:
        sx = wb.create_sheet("Check These Photos")
        sx.append(["Same file used by", "Across part types", "Stored as", "Why"])
        for x in summary["suspicious"]:
            sx.append([x["products"], x["part_types"], x["storage_key"], "One photo shared by many different kinds of parts: may be a logo or placeholder. Look at it and block it if so."])
        for c in sx[1]:
            c.font, c.fill = hdr, PatternFill("solid", fgColor=RED)
        sx.column_dimensions["C"].width = 60
        sx.column_dimensions["D"].width = 90
        for r in sx.iter_rows(min_row=2):
            r[3].alignment = Alignment(wrap_text=True)
    wb.save(out)
    return out


def print_status(conn):
    with conn.cursor() as cur:
        cur.execute("""SELECT b.id, count(p.*) FILTER (WHERE p.status='active'), count(p.*) FILTER (WHERE p.status='hidden'),
                              count(p.*) FILTER (WHERE p.status='discontinued')
                       FROM brands b LEFT JOIN products p ON p.brand_id = b.id GROUP BY b.id ORDER BY b.id""")
        print("brand            active  hidden  discontinued")
        for r in cur.fetchall():
            print(f"{r[0]:<16}{r[1]:>7}{r[2]:>8}{r[3]:>14}")
        cur.execute("SELECT count(*) FROM vehicles"); v = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM fitment"); f = cur.fetchone()[0]
        print(f"vehicles: {v}   fitment rows: {f}")
        cur.execute("SELECT id, brand_id, step, status, started_at, finished_at - started_at FROM import_runs ORDER BY id DESC LIMIT 8")
        print("\nrecent runs:")
        for r in cur.fetchall():
            print(f"  #{r[0]} {r[1]} {r[2]} {r[3]} {r[4]:%Y-%m-%d %H:%M} took {r[5]}")
