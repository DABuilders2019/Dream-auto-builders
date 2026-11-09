
## Free shipping (Admin → UPS → Free shipping)

Saved in the database (app_state key `free_shipping`), so it can be changed without a deploy:

- **Offer free shipping** on/off, **on orders of at least $** (default 149), **free service** (UPS Ground by default).
- **Not if any part weighs over (lb)** (default 50; 0 = no limit). **Lower 48 states only** (default on).
- **Also leave out oversized boxes** (default on): boxes UPS charges extra for (UPS rules from January 2026: longest side over 48 in, second side over 30 in, over 10,368 cubic inches, length + girth over 130 in, or over 50 lb).
- **Most an oversized part pays ($ per box)**: caps what shoppers pay for each oversized box (0 = no cap); you cover the rest.
- Product pages show "Oversized: shipping extra" or "Ships by truck freight" for those parts, and "Free UPS Ground on orders over $X" for the rest.
- The whole order total counts toward the minimum.
- Mixed carts: parts that are left out pay their own UPS price for the free service (UPS is asked for just those boxes); everything else rides free. Shown as "UPS Ground (free except oversized parts)".
- All parts left out: no free shipping. Faster services always keep their full UPS price.
- The cart says why: how much more to add, which parts are left out and what they cost, or that free shipping is lower 48 only.
- Freight parts need a freight quote. If UPS doesn't answer, the standard fallback rate is made free only when no part is left out.
