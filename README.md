# Dream Auto Builders

Online store for automotive parts and accessories, with Year / Make / Model fitment
built on SEMA Data (PIES product data and ACES fitment).

## What's in this repository

| Folder | What it is |
|---|---|
| `prototype/` | The clickable design prototype. Open `prototype/index.html` in a browser. This is the design reference for the real storefront: colors, layout, slideshow, category cards, product pages, Add a Vehicle bar, My Garage and cart. |
| `importer/` | **Step 1 of the live site:** the database schema and the catalog importer (SEMA PIES/ACES/vehicle sheet → Postgres, plus photo download and checks). See [docs/IMPORTER.md](docs/IMPORTER.md). |
| `store/` | **Step 2:** search, autocomplete and Year/Make/Model menus, served as a JSON API (FastAPI). See [docs/SEARCH.md](docs/SEARCH.md). **Step 3:** the storefront pages, built from the prototype's design. See [docs/STOREFRONT.md](docs/STOREFRONT.md). **Step 4:** the admin page (`/admin`) for uploading SEMA files. |
| `render.yaml` | **Step 4:** how Render runs the store (web service, disk, database). See [docs/DEPLOY.md](docs/DEPLOY.md). |
| `config/` | Brands, store categories and the rules that sort SEMA part types into them, search vocabulary, photo review results. |
| `tests/` | Importer, search and page tests (`DATABASE_URL=... pytest tests/`). |
| `tools/` | The original prototype-era scripts, kept for reference. |

**Step 5:** Stripe checkout, orders and order emails. See [docs/CHECKOUT.md](docs/CHECKOUT.md).

## Status

| Brand | Products | Fitment rows | Vehicles (year/make/model) | With a real photo |
|---|---|---|---|---|
| ReadyLift | 561 active (+2 hidden: no price) | 54,470 | 1,100 | 550 (11 small parts have none anywhere) |
| MagnaFlow | 6,894 active | 143,426 | 14,030 | 6,894 |
| Mickey Thompson | 766 active (+50 hidden: no retail or MAP price) | none (tires and wheels fit by size) | n/a | 703 (63 have no photo link in SEMA) |

## Prototype data

- **ReadyLift**: full catalog, 561 priced parts, fitment from SEMA's ACES file plus the
  Admark Vehicle Assignment sheet (vehicle names), photos checked by eye.
- **MagnaFlow**: sample of 1,235 parts (all non-converter parts plus 160 catalytic
  converters) at retail price, with California CARB status for converters.
- Everything else (other brands, stock levels) is placeholder data.

Photos are packed into sprite sheets (`prototype/img/rls`, `prototype/img/mfs`) because the
hosted prototype allows at most 512 files. The live site stores photos individually.

## Tools

| Script | Purpose |
|---|---|
| `convert_brand.py` | Any brand: PIES 7.0 XML + Admark vehicle sheet → products JSON (price type selectable, e.g. `RET` retail or `RMP` MAP). |
| `convert_readylift.py` | ReadyLift version with photo review, link-photo fallback and photo report. |
| `fetch.py`, `fetch_brand.py` | Download photos from the links in PIES files. |
| `pack_brand.py`, `sprites.py` | Pack photos into sprite sheets for the prototype. |
| `readylift_image_review.json` | Results of the by-eye photo review (logos, placeholders, warranty cards, vehicle-only shots). |

Raw SEMA exports and downloaded photos are **not** stored here: they are large and licensed.
The server will download them from SEMA Data directly.

## Known gaps to close in the live build

- Engine and drive type (2WD/4WD) in fitment: needs the VCdb vehicle database to decode
  ACES IDs. Important for lift kits and exhaust.
- Stock and dealer cost: Turn14 and Keystone feeds (accounts pending).
- California emissions: block non-CARB-legal converters from shipping to CARB states at checkout.
