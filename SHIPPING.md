
## Free shipping (Admin → UPS → Free shipping)

Saved in the database (app_state key `free_shipping`), so it can be changed without a deploy:

- **Offer free shipping** on/off, **on orders of at least $** (default 149), **free service** (UPS Ground by default).
- **Not if any part weighs over (lb)** (default 50; 0 = no limit). **Lower 48 states only** (default on).
- When the cart qualifies, that service shows as "Free UPS Ground" at $0 and is listed first; faster services keep their UPS price.
- Otherwise the cart says why: how much more to add, which part is too heavy, or that free shipping is lower 48 only.
- Freight parts never ship free. If UPS doesn't answer, the standard fallback rate is the one made free.
