# Brand page banners

Every brand page opens with a banner: the brand's logo and name, a short summary, the number of parts, and the
brand's best catalog photo with its white background removed, on a red gradient.

## How the photo is chosen
`importer/banners.py` scores the brand's 160 priciest parts that have a photo (complete kits and systems first) for
a clean white background, size, detail, contrast and how well the part fills the frame, and cuts the winner out
(WebP with transparency, saved under `banners/` in photo storage).

## When banners are made
- At the end of every photo download (Catalog upload or SEMA load), for that brand.
- A background pass every 20 minutes (`store/banner_schedule.py`) makes any that are missing: new brands, or brands
  whose banner part was discontinued or lost its photo. It waits while a catalog or SEMA job is running.
- Bumping `VERSION` in `importer/banners.py` rebuilds every automatic banner.

## Changing one (Admin > Catalog > "Page banner")
- **Summary:** written there, or `config/brand_blurbs.json`, or one built from the brand's categories.
- **Photo:** pick from the brand's best 12, upload a picture (kept until changed), or let the site pick again.

## Logo and blending (v3)
- The logo sits straight on the red in its own colours: only the plain white around it is removed. Each logo's outer
  edge is checked for contrast against the red (WCAG ratio); when more than a fifth of it is too close (black, dark
  or red lettering), it gets a thin, soft light outline on the page (CSS) so it stays readable.
- Per-brand tweaks in `config/home.json` "banner_logo_tweaks", e.g. "darken_grey" (Mickey Thompson's grey
  "TIRES & WHEELS" is printed dark on the banner).
  Made once per logo (`brands.banner_logo_key`) and again when the logo changes.
- Photo backgrounds are removed by the photo's own edge colour (white or light grey), with soft de-haloed edges;
  soft grey floor shadows become see-through shadows. On the page the part is blended (multiply) onto the light
  grey end of the gradient, with a soft floor shadow under it.
