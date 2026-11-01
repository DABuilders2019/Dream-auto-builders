# Dream Auto Builders

Online store for automotive parts and accessories, with Year / Make / Model fitment
built on SEMA Data (PIES product data and ACES fitment).

## What's in this repository

| Folder | What it is |
|---|---|
| `prototype/` | The clickable design prototype. Open `prototype/index.html` in a browser. This is the design reference for the real storefront: colors, layout, slideshow, category cards, product pages, Add a Vehicle bar, My Garage and cart. |
| `tools/` | Scripts that turn SEMA Data exports into store products. They will become the nightly import on the live server. |

The live application (database, importer, search, storefront and Stripe checkout) will be
added to this repository next and deployed on Render.

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
