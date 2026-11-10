# Price match

**Customers:** every product page has a "Found it for less? We'll price match." box under the price. It's closed until
clicked. Inside: three steps, then a link to the other store's listing **or** a photo/screenshot, their price, quantity
and email. Checks: a real web link (not our own site) or an image up to 10 MB; their price must be lower than ours;
quantity 1-10; at most 6 requests an hour per visitor and 10 a day per email; a hidden field stops form-filling bots.
Photos are re-saved as plain JPEGs in `DATA_DIR/price_match` and only shown on the admin page.

**Emails:** the customer gets a "we got your request" email; `ORDER_NOTIFY_EMAIL` gets an alert with a link to the
admin page.

**Admin > Price Match:** Waiting / Approved / Declined. Each request shows our price, their price, the difference,
quantity, the link (opens in a new tab) and the photo.
* **Approve and email:** set the matched price (each), optionally add a note. The customer gets a link that puts the
  part in their cart at that price, for up to the quantity they asked about, for **24 hours**.
* **Decline and email:** optionally give a reason.
If email isn't set up, the approved request shows the cart link to send yourself.

**Cart and checkout:** the matched price is applied wherever the cart is priced (`pricematch.apply` in
`web.cart_summary`), so the cart, header total and Stripe all charge it; the regular price shows crossed out with
"Price matched · until <time ET>". Extra units can't be added at the matched price. The order's history notes the
match, and once the order is paid the match is used up (`price_matches.used_order_id`). After 24 hours the cart goes
back to the regular price and the link says it expired.
