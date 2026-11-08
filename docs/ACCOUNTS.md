# Customer accounts

Accounts are optional: guests can still check out.

## Signing in
- **Password**, or **"Email me a code"**: a 6-digit code, good for 10 minutes and 5 tries; at most 3 per email per
  15 minutes and 12 per IP address an hour. The code is also how someone gets back in after forgetting a password
  (then they set a new one on the account page without the old one, for 15 minutes).
- Passwords are hashed (scrypt). Failed password sign-ins: 8 per email / 30 per IP address per 15 minutes.
- Asking for a code for an email with no account looks the same as for one with an account (nothing is sent), so
  the form can't be used to find out who has an account.
- **Codes need the store's email (SMTP settings).** Until those are set, the code button is greyed out and new
  accounts must choose a password.

## Saving an account at checkout
The cart has a "Save my name and email to create an account" box (unticked by default) just above Secure checkout.
If it's ticked, after the payment goes through an account is made from the name and email entered on Stripe's page
(no password) and the shopper is emailed how to sign in with a code. If an account with that email already exists,
the order is added to it.

## What an account shows
- Orders placed while signed in, plus earlier orders with the same email once the email is confirmed (signing in
  with a code confirms it); a password sign-up isn't shown older orders until then, so nobody can sign up with
  someone else's email to see their orders.
- Garage vehicles and saved builds, kept on the account and brought back on any device.
- Signed-in checkouts use a Stripe customer for the account, so Stripe's page fills in their address and offers
  to save their card for next time. (Nothing to switch on in Stripe for this.)
- Name and password changes, sign out, and **Delete my account** (orders are kept for records, unlinked).
