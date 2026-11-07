# Analytics and cart reminders

## Visits and clicks (Admin → Analytics)
First-party and anonymous: a random visitor id is kept in the shopper's session cookie. Stored per event:
time, visitor id, kind, page path, what (product slug, search words or the clicked element's tag), and the
other site that sent the visitor (domain only). No names, emails or IP addresses.

* Page views, product views and searches are recorded by the server (store/tracking.py, `install`).
* Add to cart, checkout started and purchase are recorded where they happen (cart, checkout, Stripe webhook);
  the visitor id travels to Stripe in the checkout metadata so a purchase joins the same visitor.
* Clicks: any element with `data-track="..."` is sent to `/api/t` by site.js. Tagged today: home slides,
  category cards, brand tiles and arrows, header Brands menu, Track Order, My Garage, Build your dream ride,
  installer directions links. Add `data-track="label"` to anything else you want counted.
* Not recorded: Do Not Track / Global Privacy Control, signed-in admins. Events older than 180 days are deleted daily.

## Abandoned checkouts (Stripe)
Checkout sessions are created with `consent_collection.promotions = auto` (Stripe shows a "send me updates"
box) and `after_expiration.recovery.enabled`, and close after `cart_recovery.hours` (3). When one expires
unpaid, the `checkout.session.expired` webhook marks the order abandoned and, if the shopper ticked the box,
emails "You left something in your cart" with Stripe's recovery link (valid 30 days), which reopens the same
checkout. Paying through it revives the original order. Settings: config/store.json → cart_recovery.

Carts that never reach the Stripe page are counted on the Analytics page but can't be emailed: the shopper
hasn't given an email address yet.
