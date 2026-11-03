# Turn 14 Distribution feed

Built from Turn 14's own documentation (API endpoint documentation, Best Practices, API Settings /
Acceptable Usage Policy) and their onboarding email. Code: `turn14/` (client and sync), admin page
`/admin/turn14`, schedule in `store/turn14_schedule.py`, tables in `importer/migrations/007_turn14.sql`.

## What it does

| Task | Turn 14 endpoints | When |
|---|---|---|
| `test` | `/v1/token`, `/v1/brands`, `/v1/locations` | By hand |
| `setup` | brands, locations, then per approved brand: `/v1/items/brand/{id}`, `/v1/pricing/brand/{id}`, `/v1/inventory/brand/{id}` | By hand, once |
| `daily` | brands, `/v1/items/updates?days=N` (N = days since last run, 1-15), pricing and inventory per brand | Daily after 1:30 AM Eastern (pricing after 1:00 AM, as Turn 14 asks) |
| `changes` | locations, `/v1/inventory/updates?minutes=M` (M from 15/30/60/240/480/1440, covering the time since the last run) | Every hour |
| `full-items` | items per brand; items missing from the full list are marked inactive | 1st and 15th of the month |

Only brands we sell (matched by AAIA brand code, then by name) and that Turn 14 approves for our
account are loaded. Item-level `can_purchase` from pricing is respected: parts we can't buy from
Turn 14 show no Turn 14 stock.

Fitment is not taken from Turn 14 (it needs an AutoCare VCdb subscription); vehicle fitment keeps
coming from the brands' SEMA files.

## Turn 14's rules and how they're kept

| Rule (source) | Here |
|---|---|
| Terminate on any non-200 response (email) | Any non-200 stops the task and is logged on the admin page |
| On 429, pause and retry (Best Practices) | One retry after 60 seconds; a second 429 stops |
| Pagination, 1,000 per page; no single-item requests (email, Best Practices) | Only paged list endpoints are used |
| 5 GET/second, 5,000/hour, 30,000/24 hours; 10 token requests/minute per IP (Acceptable Usage Policy) | At most 4/second; stops itself at 4,000/hour and 24,000/day (counted in the database across processes); at most 5 token requests/minute |
| Reuse the token until it expires (Best Practices) | Token stored in the database, renewed 2 minutes before `expires_in` |
| Credentials only in back-end code (Best Practices, API Settings) | Read from Render environment variables by the server; never sent to browsers or shown on pages |
| Map item_id to our parts before using the data; prefer "G" item ids (email, Best Practices) | Brand + manufacturer part number, then UPC, then alternate part number; "G" items preferred |
| Store the data locally; don't call the API per page view (Best Practices) | The shop reads stock only from the local tables |
| Test on apitest.turn14.com before production (email, Best Practices) | `TURN14_ENV=test` is the default; production needs `TURN14_ENV=production` |
| No ordering until the data integration is approved; quotes only per live order (email) | No quote or order code yet |
| Log sync jobs (Best Practices) | Every request is recorded (time, path, status, duration, task); each task's log is on the admin page |

## Settings (Render > Environment)

| Variable | Value |
|---|---|
| `TURN14_CLIENT_ID`, `TURN14_CLIENT_SECRET` | From turn14.com > Settings & Data > API Settings. Never paste them anywhere else. |
| `TURN14_ENV` | `test` until Turn 14 grants production access, then `production` |
| `TURN14_AUTO` | `1` to run the schedule above; leave `0` until the first full load looks right |

## Steps

1. Add the keys in Render (the shop restarts).
2. Admin > Turn 14 > **Test connection**. Check that our brands show with their Turn 14 brand and approval.
3. **First full load**. Check matching (the Unmatched CSV per brand lists both sides).
4. Run **Daily sync** and **Stock changes** once by hand; then set `TURN14_AUTO=1`.
5. Send Turn 14 (your contact) a summary and screenshots of the admin page, and ask for production access.
6. When approved: `TURN14_ENV=production`. Ordering (quote, then order from quote) is built after that.
