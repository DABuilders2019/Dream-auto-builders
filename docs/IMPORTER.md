# Catalog importer

Loads SEMA Data exports into the store's Postgres database, then downloads and checks photos.

## Files per brand (from SEMA Data)

| File | SEMA export | Used for |
|---|---|---|
| PIES 7.0 XML | Brand Data → PIES 7.0 XML | Products, prices, specs, descriptions, Prop 65, CARB, photo and PDF links |
| ACES 4.0 XML | Brand Data → ACES 4.0 XML | Raw fitment with VCdb IDs (kept for engine/drive-type decoding later) |
| Admark Vehicle Assignment Template | Ad-Hoc Exports → W/D Merchandiser Sheets – ACES → Admark Vehicle Assignment Template (main dataset, not "Pricing") | Fitment in words: Year, Make, Model, Submodel, Engine… This powers Add a Vehicle. |
| DIGAST PIES Digital Assets (optional) | Digital Assets → DIGAST PIES | Photos as files. Without it, photos are downloaded from the links in the PIES file. |

The `.zip` files exactly as downloaded from SEMA can be passed in directly.

## Commands

```bash
export DATABASE_URL=postgresql://user:pass@host:5432/db
pip install -r requirements.txt

python -m importer migrate                      # create / upgrade tables
python -m importer load --brand readylift \
    --pies Readylift_PIES_7.0_XML.zip \
    --aces Readylift_ACES_4.0_XML.zip \
    --vehicles SDC_BKMQ_Admark_Vehicle_Assignment_Template.zip
python -m importer photos --brand readylift [--zip Readylift_DIGAST_PIES_Digital_Assets.zip]
python -m importer report --brand readylift --out readylift_photos.xlsx
python -m importer status
```

## Adding a brand

1. Add it to `config/brands.json`: `id`, display `name`, SEMA brand code (`aaia_id`, shown in SEMA export file names, e.g. `SDC_FBHB_…` = MagnaFlow),
   `display_price` (which PIES price to show: `RET` retail, `RMP` MAP, `LST` list), and `strip_prefix`.
2. Run `load`, then `photos`, then `report`.
3. Check `uncategorized_part_types` in the load output. Add a rule to `config/catalog.json` for each, then load again.
4. Open the report's "Check These Photos" tab (if present): one photo used by many different kinds of parts is usually a logo. Add its md5 to
   `config/image_reviews.json` as `blocked`, and run `photos` again.

## How it behaves

- **All or nothing per brand.** A load runs in one transaction. If a file is truncated or anything fails, the previous catalog is left untouched and
  the run is recorded as `failed` in `import_runs`.
- **Safe to re-run.** Products are matched on brand + part number and updated in place, so product IDs and URLs stay stable.
- **Discontinued parts.** When SEMA sends a FULL file, parts no longer in it are marked `discontinued` (kept for order history, hidden from the store).
- **No price, no listing.** Items without a usable price are loaded as `hidden`.
- **Prices.** The shopper price comes from the brand's `display_price` order. MAP (`RMP`) is stored separately so the store can never advertise below it.
- **Fitment status.** `vehicle_specific` (has vehicle rows), `universal` (the brand marks it universal, or it's a weld-in part type such as mufflers,
  tips, pipes), or `missing` (the brand sent no vehicle data). `missing` parts are hidden whenever a shopper filters by vehicle, and are listed in the
  load output so the brand can be asked for fitment.
- **Emissions.** Catalytic converters get `carb_legal` (with the CARB Executive Order number) or `federal_only`. Checkout will block `federal_only`
  parts from shipping to California and other CARB states.
- **Photos.** Primary photo first, then up to three more. Each file is checked against `config/image_reviews.json` (logos, placeholders and warranty
  cards are blocked; truck/install shots are marked `vehicle` and shown after photos of the part). If the zip copy is unusable, the link is tried.
  Products left without a part photo get their remaining photos tried. Photos found on the brand's own product page (exact part number match) are
  listed in `config/extra_images.json`. Progress is saved as it goes, so an interrupted run resumes where it stopped.
- **Photo storage.** Local folder by default (`PHOTO_DIR`). In production set `PHOTO_STORAGE=s3` and the `S3_*` variables to use object storage
  (Cloudflare R2 or S3) so photos are served from a CDN.

## Known gaps

- Engine and drive type in the fit check: ReadyLift's vehicle sheet has no engine column (MagnaFlow's does). The raw ACES IDs are stored in
  `aces_applications`; decoding them needs the VCdb reference database (Auto Care Association), after which 2WD/4WD and engine can be checked.
- PADB attributes (numeric attribute IDs, e.g. color/material codes) are stored with a `padb:` prefix but not shown until the PAdb reference table is loaded.
- Stock and dealer cost come later from Turn14 and Keystone.
