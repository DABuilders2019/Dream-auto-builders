# Storefront (step 3)

The shop pages are rendered on the server (FastAPI + Jinja2 templates) from the catalog database,
using the prototype's design (`prototype/index.html`). Pages work without JavaScript; `site.js`
adds search suggestions, the Year/Make/Model menus, the slideshows, filters that apply on click
and add-to-cart without a page reload.

## Run it

```
DATABASE_URL=postgresql://... SECRET_KEY=any-long-random-text PHOTO_DIR=/path/to/photos \
  uvicorn --factory store.app:create_app --reload
```

| Setting | Meaning |
|---|---|
| `SECRET_KEY` | Signs the shopper's cookie. Use a long random value in production (Render can generate one). Without it carts reset on every restart. |
| `COOKIE_SECURE=1` | Cookie only over HTTPS. Set in production. |
| `SITE_PREVIEW` | `1` (default): "Test site" ribbon, and search engines are told not to index. Set `0` at launch. |

## Pages

| URL | Page |
|---|---|
| `/` | Home: slideshow, tagline, Featured Categories, Popular right now (or Top parts for your vehicle), Featured Brands, value props. Content comes from `config/home.json`. |
| `/search?q=` | Search results. A part number that matches exactly one part opens that part. A vehicle typed into the search ("2019 silverado leveling kit") is added to My Garage. |
| `/c/<category>` | Category (top level or subcategory). |
| `/brand/<brand>`, `/brands` | One brand's parts; list of brands. |
| `/p/<slug>` | Product: photos, price, fit check for the selected vehicle, description, specifications, fitment table, install PDFs, Prop 65, related parts. |
| `/cart` | Cart. Checkout is switched off until step 5. |
| `/garage` | My Garage: saved vehicles, pick which one to shop for. |
| `/track`, `/account`, `/info/...` | Placeholders until checkout and accounts exist. The info pages need the store's own wording before launch. |
| `/admin` | Catalog admin (needs `ADMIN_PASSWORD`): upload a brand's SEMA files, watch the import, download photos, photo report. |
| `/login` | Only when `SITE_PASSWORD` is set: the private test site's sign-in. |

Results pages share these filters (all in the URL, so filtered pages can be bookmarked):
`brand` (repeatable), `price` (`0-100`, `100-300`, `300-750`, `750-1500`, `1500-`), `carb=1`,
`all=1` (show parts for all vehicles instead of only the selected one), `sort`
(`relevance`, `price_asc`, `price_desc`, `name`), `page`.

## How fit is shown

With a vehicle selected, results show only parts that fit it, are universal, or fit by size
(tires and wheels). The count line says how many were hidden, with a link to show them.

| Badge | When |
|---|---|
| Fits your 2022 Ram 1500 | The manufacturer's fitment lists that vehicle. |
| Universal fit | Mufflers, tips, pipes, or the part is marked universal. |
| Fits by size | Tires and wheels: the shopper checks the size. |
| Doesn't fit your Ram 1500 | Listed for other vehicles (only seen with "show all"). |
| Fitment not listed | The manufacturer sent no vehicles for it. |
| Fits 2019-2026 Ram 1500 + 3 more | No vehicle selected: the vehicle it fits most. |

## Shopper data

The garage (up to 8 vehicles) and cart are kept in a signed cookie, so nothing is stored for
visitors who don't buy. Accounts (step 5 or later) will move them into the database so they
follow the shopper between devices.

## Files

| File | What it does |
|---|---|
| `store/web.py` | Page routes, garage and cart. |
| `store/queries.py` | Product page, cards, home page numbers. |
| `store/templates/` | `base.html` (header, Add a Vehicle bar, footer), `_macros.html` (product card, fit badge, Y/M/M menus), one template per page. |
| `store/static/` | `css/site.css` (from the prototype), `js/site.js`, logos, category and slide images, brand logos, fonts (Barlow, served from the site). |
| `config/home.json` | Home page slides, category order and photos, popular parts, hand-picked brand logos (other brands use the logo from their SEMA data). Moves into the admin dashboard later. |
| `store/admin.py`, `store/jobs.py` | Admin page, and the background runner for imports. |
