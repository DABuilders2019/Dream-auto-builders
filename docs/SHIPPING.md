# Shipping prices (UPS)

Checkout charges the exact UPS price. The shopper enters their ZIP in the cart and sees each UPS service with its
price; they pick one on Stripe's payment page. Settings: config/store.json > "shipping" (carrier "ups").

- Parts ship from suppliers: prices are from `from_zip` (the supplier warehouse), or `brand_from_zip` per brand.
  Several warehouses in one cart: each is priced separately and added up.
- Each unit is its own box, using the catalog's shipping weight and box size (default 10 lb, 20x12x8 when missing).
- Too big for UPS (over 150 lb, longer than 108 in, or more than 165 in length + girth): the cart asks for a
  freight quote and checkout is blocked for that cart.
- UPS not answering: the `fallback` prices are offered and the order notes say so.
- If the address entered on Stripe's page has a different ZIP than the one priced, the order notes
  "Check shipping: ..." in Admin > Orders.
- Quotes are kept for 30 minutes, so the cart and payment page match.
- `carrier: "flat"` switches back to free_shipping_over / flat_shipping.
- Admin > UPS checks the UPS keys and shows sample prices.
