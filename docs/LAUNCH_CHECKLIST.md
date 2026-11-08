# Before the site goes live

Items to settle before switching the test site to the public store. Go through this list together
before launch day.

## To discuss first

- [ ] **Our own vehicle builder (decide before launch).** Option discussed on Nov 1, 2026 and put on
      hold: build a "see it on your truck" builder inside the site (vehicle picker, parts layered onto
      the vehicle as they're picked, lift height, wheels and tires, build list, add build to cart).
      The software can be built here; the vehicle and part images have to be licensed, drawn or
      supplied by the brands. Suggested start: a one-truck proof of concept (2022 Ram 1500, side view,
      ReadyLift lift heights, Mickey Thompson wheels and tires). Compare with keeping Interactive
      Garage, or with iConfigurator Elite / 3DTuning.
- [ ] **Interactive Garage:** logo without the white box and a red Search button (asked of them),
      their "My Garage" link renamed, and the cart connection with Dane (spec: docs/INTERACTIVE_GARAGE.md).
      Builds sent from the builder should land in My Garage > Saved builds.

## Decisions

- [ ] Shipping rate under $149 (placeholder $14.95) and countries shipped to (US only now).
- [ ] Sales tax: which states to collect in; turn on Stripe Tax if needed.
- [ ] Confirm the states that can't receive non-CARB catalytic converters (CA, CO, ME, NY now).
- [ ] Pricing for the 50 Mickey Thompson parts that only have a jobber (JBR) price (hidden now).

## Content

- [ ] Wording for About, Contact, Privacy policy, Terms of sale, Shipping & returns and Fitment guarantee.
- [ ] Home page builder slide with a picture of a built-up truck (optional).
- [ ] Clean up ReadyLift product names that are shorthand in their SEMA data.
- [ ] Missing photos: 11 ReadyLift small parts, 63 Mickey Thompson parts (ask the brands).

## Switching on

- [ ] Stripe live keys (`sk_live_...`) and a live-mode webhook endpoint; a real test order and refund.
- [ ] Order emails set up (SMTP) and tested.
- [ ] Domain: add it in Render (Custom Domains), set `SITE_URL`.
- [ ] Delete `SITE_PASSWORD`, set `SITE_PREVIEW` to `0`.
- [ ] Check the shop at the new address on desktop and phone.

## Turn 14

- [ ] Turn 14 approval of the testing-server integration, then production access (`TURN14_ENV=production`).
- [ ] `TURN14_AUTO=1` once the first full load and matching look right.
- [ ] Sales tax certificates filed with Turn 14 for the states we ship to (needed for their orders).

## Soon after launch

- [ ] Automatic nightly SEMA imports (scheduled FTP delivery).
- [ ] Sending orders to Turn 14 automatically (quote, then order from quote), and Keystone.
- [ ] Photos to Cloudflare R2 once the catalog grows; database size check.
- [ ] Customer accounts (order history, garage and builds on every device).

## Nearby installers (Google Maps)
- [ ] Terms of Use and Privacy Policy pages mention Google's Terms of Service (https://policies.google.com/terms) and
      Privacy Policy (https://policies.google.com/privacy), as Google requires for the shop lists (docs/INSTALLERS.md)
- [ ] Attorney review of the installer disclaimer wording (config/store.json → installers.disclaimer)
- [ ] Set a monthly budget alert on the Google Cloud billing account
- [ ] Contact Google Maps Platform support (console.cloud.google.com → Support, or the Maps Platform sales/contact form)
      to confirm the shop list meets their terms: Places API (New) Text Search with name/address/map link only,
      shown without a map with the "Google Maps" text attribution, only place IDs stored, shown in order emails

## Analytics and cart reminders
- [ ] Privacy Policy: say the site uses a session cookie to count visits and clicks anonymously (kept 180 days,
      Do Not Track / Global Privacy Control respected) and that shoppers who opt in at checkout may get a cart reminder email
- [ ] Stripe dashboard → Developers → Webhooks: the endpoint must send checkout.session.expired (as well as
      checkout.session.completed, async_payment_succeeded and async_payment_failed) for cart reminders to work
- [ ] Add the business's postal address to the cart reminder email footer (CAN-SPAM requires it in marketing emails)
- [ ] Decide on a promo code for cart reminders (config/store.json → cart_recovery.allow_promotion_codes, codes made in Stripe)
- [ ] Customer accounts: SMTP settings must be in Render for sign-in codes and the "account is ready" email.
- [ ] Privacy policy: describe customer accounts (name, email, orders, saved vehicles and builds; deleting an account).
- [ ] Stripe live mode: Settings > Checkout, accept the terms for promotional-email consent (cart reminders). Done in the sandbox first.
