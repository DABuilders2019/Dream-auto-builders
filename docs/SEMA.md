# SEMA Data API connection

Loads brands straight from SEMA Data, so brands don't need to be downloaded and uploaded by hand.
Admin page: **/admin/sema**. Manual uploads on the Catalog page still work as a backup.

## Setup (Render → the web service → Environment)

| Variable | Value |
|---|---|
| `SEMA_USERNAME` | the SEMA Data website login (apps.semadata.org) |
| `SEMA_PASSWORD` | its password |
| `SEMA_AUTO` | `0` until the first load is done, then `1` for the nightly update |

The login is only sent to SEMA. Request logs keep the endpoint name, never the login or token.

## The brand list

`config/sema_brands.json` holds the brands we want on the site, by the names we use. The admin page
can edit the list. **Test connection** reads the brands SEMA has approved for us and matches the list
to them. Names that could be more than one SEMA brand (for example "Real Truck Bak": BAK or RealTruck),
or that aren't approved at SEMA, ask for a choice on the admin page.

## Loading a brand

| Step | SEMA endpoint | Calls |
|---|---|---|
| part numbers | `GET /products` | 1 |
| product data | `GET /products/bulk` (100 parts per call) | parts ÷ 100 |
| which parts fit vehicles | `POST /lookup/products` | 1 |
| vehicles per part | `POST /lookup/vehiclesbyproduct` | 1 per part with fitment |

The product data is written out as a PIES file and loaded by the same loader as manual uploads
(`importer/load.py`), in one transaction. Then photos are downloaded, checked and resized like uploaded brands.
SEMA asks that its image links are not shown directly on the site, so photos are always downloaded.

Vehicle answers are kept on disk (`DATA_DIR/sema/<brand code>/fitment.jsonl`). A reload asks only
about parts that are new, which keeps the fitment step cheap after the first load. "Reload" with
fresh fitment can be done per brand.

## Limits we keep

* Speed: under 120 calls a minute on `/products…` and under 60 a minute on everything else.
* Allowance: before loading, `/receiver` (free) gives the plan's used and total API calls. The job stops
  before the allowance is used up and always leaves 5% (at least 1,000 calls) unused.
* 429: wait as long as SEMA asks (up to 2 minutes) and try once more; a second 429 stops the job.
  Any other error answer stops the job. A problem with one brand (for example no longer approved) is
  recorded on that brand and the job moves on to the next.
* Photos are skipped when less than 1 GB is free on the photo disk.

## Nightly update (`SEMA_AUTO=1`)

After 3:30 AM Eastern: brands on the list that SEMA has updated since our last load, new on the list,
or failed last time are loaded again, then their photos. Nothing runs while a catalog upload or
another SEMA job is running.

## To confirm with SEMA (API assistance call)

* Whether the 100,000 API calls and 1,000 exports reset monthly or yearly.
* Whether `/lookup/vehiclesbyproduct` can take several part numbers, or whether an ACES export plan
  can be downloaded through the API: either would make the first fitment load much cheaper.
