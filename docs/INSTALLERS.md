# Nearby installers (Google Maps)

Orders with parts that usually need a shop (suspension, exhaust, brakes, tires, wheels, intake, engine,
drivetrain, exterior, lighting, performance; set in `config/store.json` → `installers.categories`) show
"Need it installed? Shops near you" on the order confirmation page and in the confirmation email:
up to 5 repair shops near the shipping address, found with Google's Places API.

* Every list carries the disclaimer from `config/store.json` → `installers.disclaimer` and Google's
  "Google Maps" attribution.
* "I'll use this shop" saves the shop's Google place ID with the order (`install_feedback`). Google allows
  keeping place IDs; names and addresses are looked up again whenever they're shown and never stored.
* `survey_days` (10) after an order is marked shipped, the customer gets "How did the install go?" with a
  link to `/install/<token>`: installed or not, which shop (or another one in their own words), 1–5 stars,
  would they recommend it, and a comment. Needs SMTP email set up.
* Admin → Installers ranks the shops customers picked by installs, rating and recommendations.

## Setup

1. Google Cloud console (console.cloud.google.com): create a project and turn on billing.
2. APIs & Services → Library → enable **Places API (New)**.
3. APIs & Services → Credentials → Create credentials → API key. Edit the key:
   API restrictions → Restrict key → Places API (New) only.
4. Render → the web service → Environment → add `GOOGLE_MAPS_API_KEY` with the key.

## Cost

One Text Search call each time a list is shown: the order page, the confirmation email and the install page.
Because the list shows each shop's Google star rating, the call is in Google's Enterprise tier: 1,000 free a
month, then about $35 per 1,000 (about 350-500 install-type orders a month before any charge). Removing
places.rating and places.userRatingCount from FIELDS in store/installers.py drops it back to the Pro tier
(5,000 free, then about $32 per 1,000). Shop distances come from the bundled zipcodes package (no API call). The admin Installers page makes one Place Details call per shop listed.

## Google's terms

The site's Terms of Use and Privacy Policy must mention Google's Terms of Service and Privacy Policy
(see LAUNCH_CHECKLIST.md).
