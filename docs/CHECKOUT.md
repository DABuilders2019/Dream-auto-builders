# Checkout and orders (step 5)

Payment happens on Stripe's own secure page (Stripe Checkout), so card numbers never touch this
site. The flow:

1. **Cart → Secure checkout.** The order is saved as "waiting for payment" with the prices from the
   database, and the shopper goes to Stripe's page (address, phone, card).
2. **Stripe → webhook.** When the payment goes through, Stripe calls `/stripe/webhook`. The order
   becomes **paid** (or **on hold**, see CARB below), and the confirmation email and the new-order
   alert are sent.
3. **Confirmation page** `/order/DAB-100001`: the shopper lands here after paying. The cart is emptied.
   (If Stripe's webhook is slow, this page asks Stripe directly.)
4. **Admin → Orders** (`/admin/orders`): order the parts from Turn14 or Keystone, then enter the
   carrier and tracking number. The customer gets a shipping email, and **Track Order** shows it.
   Orders can be refunded in full from the same page.

Abandoned payment pages are cancelled automatically when Stripe expires them (24 hours).

## Settings (`config/store.json`)

| Setting | Now | Meaning |
|---|---|---|
| `ship_to_countries` | `["US"]` | Countries Stripe accepts addresses for |
| `free_shipping_over` | `149` | Orders at or over this ship free |
| `flat_shipping` | `14.95` | Shipping below that (placeholder: set the real rate) |
| `automatic_tax` | `false` | Sales tax by Stripe Tax; turn on Stripe Tax in the Stripe dashboard first |
| `carb_restricted_states` | CA, CO, ME, NY | States where non-CARB catalytic converters can't ship |

## Catalytic converters (CARB)

A cart with a converter that isn't CARB-approved asks for the shipping state first and refuses the
restricted states. If the address entered on Stripe's page is in one of them anyway, the order is
paid but put **on hold** with the reason, so it can be refunded instead of shipped.

## Environment variables

| Variable | Where it comes from |
|---|---|
| `STRIPE_SECRET_KEY` | Stripe dashboard > Developers > API keys. `sk_test_...` until launch, then `sk_live_...` |
| `STRIPE_WEBHOOK_SECRET` | Stripe dashboard > Developers > Webhooks > your endpoint > Signing secret (`whsec_...`) |
| `SITE_URL` | Optional: the address used in Stripe and email links (set it once the real domain is live) |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD` | Your email service's SMTP settings |
| `MAIL_FROM` | Sender, e.g. `Dream Auto Builders <orders@yourdomain.com>` |
| `ORDER_NOTIFY_EMAIL` | Where new-order alerts go |

Without the SMTP settings orders still work; the order history notes that no email was sent.

## Testing (test mode)

Card `4242 4242 4242 4242`, any future expiry date, any CVC, any ZIP. Card `4000 0000 0000 0002`
is declined. No money moves in test mode.

## Still to come

- Purchase orders sent to Turn14 / Keystone automatically (needs their API access).
- Live stock from the distributor feeds.

Customer accounts: see [ACCOUNTS.md](ACCOUNTS.md).
