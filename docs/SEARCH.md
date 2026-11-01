# Search

Search runs inside Postgres (full-text search + `pg_trgm`), so there is no separate search service to pay for or
keep in sync. Code: `store/search.py` (search, autocomplete) and `store/vehicles.py` (Year/Make/Model menus and
spotting a vehicle typed into the search bar). Vocabulary: `config/search.json`.

## What a search does

1. **Vehicle in the query.** "2019 silverado 1500 lift kit" → vehicle *2019 Chevrolet Silverado 1500* + words "lift kit".
   - Leading words of a model match the whole family: "silverado" = every Silverado; "silverado 1500" = 1500, 1500 Classic/HD/LD.
   - Punctuation doesn't matter: "f150", "F-150", "f 150". Nicknames: "chevy", "vw", "mercedes"... (`make_aliases`).
   - "dodge ram 1500" covers Dodge Ram 1500 (to 2010) and Ram 1500 (2011 on).
   - Number-only models ("1500") need the make. Product-line words ("street", "baja", "touring") and the words in
     `generic_model_words` only count as a vehicle when the make is typed too.
2. **Part numbers** in any format: "69-3485", "693485", "69 3485". Only real catalog part numbers count.
3. **Tire sizes** in any format: "35x12.50r20", "35X12.5R20LT", "LT285/70R17", "275/40ZR18", "26x10-15".
4. **Synonyms** (`synonyms`): "catback" also searches "cat-back", "a/t" → "all-terrain", "rims" → "wheels"...
5. **Typos**: words not used anywhere in the catalog are corrected to the closest catalog word
   ("levling" → "leveling", "micky" → "mickey"). The response lists corrections so the page can say "Showing results for…".
6. **Matching**, in up to three passes until something is found:
   - *strict*: every word in the product name, part number, part type, category or key specs;
   - *and*: every word anywhere, including marketing text;
   - *or*: any word (response has `relaxed: true` so the page can say so).
   Sizes and numbers ("4 inch", "2.5") boost ranking but aren't required, since catalogs write them many ways.
7. **Ranking**: parts that fit the vehicle first, then universal parts, then tires/wheels that fit by size; then
   text relevance, exact part number (+10), a category named in the query, e.g. "exhaust tip" → Exhaust Tips (+1); photo.

## Vehicle fit labels (`fit` on each result)

| fit | Meaning | Shown when a vehicle is selected? |
|---|---|---|
| `fits` | Brand's fitment data lists this vehicle | Yes (first) |
| `universal` | Fits any vehicle (brand says universal, or mufflers/tips/pipes) | Yes |
| `size` | Tires and wheels: fit by tire size / bolt pattern | Yes, labelled "Fits by size" |
| `no` | Has fitment, not for this vehicle | Only with `fit_only=false` |
| `unknown` | Brand sent no fitment | Only with `fit_only=false` |

For parts that fit, `fit_detail` lists the engines, trims and notes for the selected vehicle (e.g. a Mustang cat-back for the 2.3L).

## API

| Endpoint | Parameters |
|---|---|
| `GET /api/search` | `q`, `vehicle` (id), `category` (parent or sub), `brand` (repeatable), `price_min`, `price_max`, `carb`, `fit_only` (default true), `sort` (`relevance`, `price_asc`, `price_desc`, `name`), `page`, `per_page` (max 96) |
| `GET /api/suggest` | `q`, `vehicle`: vehicle spotted, categories, brands, part numbers, top products |
| `GET /api/vehicles/years`, `/makes?year=`, `/models?year=&make=`, `/{id}` | Year / Make / Model menus (only vehicles that have parts) |

Responses include `facets`: category tree with counts, brands, price ranges, CARB counts, and how many results are hidden
because they don't fit. Interactive docs at `/api/docs` when the app is running.

## Speed (full catalog: 8,221 products, 198k fitment rows)

Typical searches take 8–25 ms; the broadest ("exhaust", 6,895 results) about 90 ms; autocomplete about 25 ms.
