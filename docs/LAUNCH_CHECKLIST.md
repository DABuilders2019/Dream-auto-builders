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
- [ ] Order emails and domain: see "Moving day" below.
- [ ] Delete `SITE_PASSWORD`, set `SITE_PREVIEW` to `0`.
- [ ] Check the shop at the new address on desktop and phone.

## Moving day: dreamautobuilders.com to the new site
The domain stays registered at hosting.com (my.hosting.com) and its DNS stays there too: only the two website records
change, so email and everything else keep working. Admin > Launch Check > Domain shows where every record points.
- [ ] Before: Admin > Launch Check > Run all checks > read the Domain section. Screenshot every record in my.hosting.com
      (DNS > dreamautobuilders.com > DNS Records, or Websites > the site > DNS records). If the domain was bought from
      A2 Hosting before April 28, 2025 and the DNS page isn't there, open a ticket at my.hosting.com and ask them to change
      the two records below.
- [ ] Check which records belong to other companies (email, newsletter, verifications); they stay untouched.
- [ ] Render: dream-auto-builders > Settings > Custom Domains > add dreamautobuilders.com (www is added automatically).
- [ ] my.hosting.com DNS: A record for @ = 216.24.57.1 (delete other A and any AAAA records for @); www = CNAME to
      dream-auto-builders.onrender.com (delete other www records). Don't touch MX, mail, webmail, autodiscover or TXT records.
- [ ] Render: Verify, and wait for both domains to show the certificate as issued. Launch Check > Domain shows both on Render.
- [ ] Resend (resend.com, sign up with admin@dreamautobuilders.com): Domains > Add Domain > dreamautobuilders.com
      (North America); add its DNS records; click Verify and wait for "Verified".
- [ ] Resend: API Keys > Create API Key ("Sending access") and copy it (shown once).
- [ ] Render > Environment (edit existing names, don't add duplicates):
      `SMTP_HOST`=smtp.resend.com, `SMTP_PORT`=587, `SMTP_USERNAME`=resend, `SMTP_PASSWORD`=the re_ key,
      `MAIL_FROM`=Dream Auto Builders <admin@dreamautobuilders.com>, `ORDER_NOTIFY_EMAIL`=admin@dreamautobuilders.com
- [ ] Make sure the live site sends from the real domain, not Resend's test sender: Resend > Domains shows
      dreamautobuilders.com as "Verified", `MAIL_FROM` in Render is admin@dreamautobuilders.com (never
      onboarding@resend.dev, which can only email the Resend account owner), and a test email reaches an outside
      address (e.g. a personal Gmail) that isn't the Resend account's own email.
- [ ] Render: set `SITE_URL` to https://dreamautobuilders.com
- [ ] Stripe: Workbench > Webhooks > the new site's destination > Edit destination > URL
      https://dreamautobuilders.com/stripe/webhook (the signing secret stays the same). Then delete the old
      WooCommerce webhook (?wc-api=wc_stripe) once the old store is off.
- [ ] Stripe: Settings > Business > Public details > support email admin@dreamautobuilders.com.
- [ ] After: turn off (or redirect) the old WooCommerce site; keep the old hosting plan if email lives there (Launch
      Check > Domain says), and cancel it only when nothing points to it. Keep the domain on auto-renew at hosting.com.
- [ ] Test: Sign In > Email me a code (arrives?), then a test order (customer email + new-order alert to admin@,
      order Paid in Admin > Orders, Stripe webhook delivery "Succeeded").

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

## Analytics
- [ ] Privacy Policy: say the site uses a session cookie to count visits and clicks anonymously (kept 180 days,
      Do Not Track / Global Privacy Control respected).

## Cart reminder emails ("You left something in your cart")
Off until these are done: checkout works without them, but the reminder emails won't go out.
- [ ] Email (SMTP) settings in Render, so the site can send email at all (also needed for orders and sign-in codes).
- [ ] Stripe: Settings > Payments > Checkout and Payment Links > **View all settings** > accept the terms for
      promotional-email consent ("Promotional emails > Accept terms of service"). The button is greyed out in the
      sandbox: do it in live mode, after the Stripe account is activated, as an Administrator. Until it's accepted, the site
      opens the payment page without the "send me updates" box, and no reminders go out.
- [ ] Stripe webhook (live mode) sends checkout.session.expired, as well as checkout.session.completed,
      async_payment_succeeded and async_payment_failed.
- [ ] Add the business's postal address to the reminder email footer (CAN-SPAM requires it in marketing emails).
- [ ] Privacy Policy: shoppers who tick "send me updates" at checkout may get one cart reminder email.
- [ ] Decide on a promo code for reminders, or none (config/store.json > cart_recovery.allow_promotion_codes; codes made in Stripe).
- [ ] Test it: start a checkout, tick "send me updates", leave the payment page, and wait for it to expire (3 hours,
      config/store.json > cart_recovery.hours). The reminder email should arrive, and Admin > Analytics shows the
      abandoned checkout as "reminder sent".

## Customer accounts
- [ ] SMTP settings must be in Render for sign-in codes and the "account is ready" email.
- [ ] Privacy policy: describe customer accounts (name, email, orders, saved vehicles and builds; deleting an account).

## Shipping (UPS)
- [ ] Set the real ship-from ZIP(s): config/store.json > shipping.from_zip is the supplier warehouse ZIP (11691 is
      only a placeholder), plus brand_from_zip for brands that ship from a different warehouse.
- [ ] Render: `UPS_ENV` = `production` (real prices; the testing server's prices are samples), then check
      Admin > UPS and a test cart.
- [ ] Compare a few UPS quotes with what suppliers actually charge for drop-shipping, and adjust if needed.
- [ ] Decide how freight-only parts (over UPS limits) are quoted and sold.


## Price match

- [ ] `ORDER_NOTIFY_EMAIL` is set (admin@dreamautobuilders.com), so new price match requests reach you.
- [ ] Check the rules line under the price match form matches your actual policy (authorized U.S. sellers, same new part, in stock, no marketplace or auction listings).
- [ ] Send a test request, approve it, and open the emailed link to see the matched price in the cart.
