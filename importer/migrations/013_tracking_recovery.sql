-- Visits and clicks (first-party, anonymous), and abandoned Stripe checkouts.

CREATE TABLE site_events (
    id       bigserial PRIMARY KEY,
    at       timestamptz NOT NULL DEFAULT now(),
    visitor  text NOT NULL,                     -- random id kept in the shopper's session cookie
    kind     text NOT NULL,                     -- page_view, product_view, search, click, cart_add, checkout, purchase
    path     text,
    target   text,                              -- what: product slug, search words, the clicked element's tag, ...
    referrer text                               -- other site that sent the visitor (domain only)
);
CREATE INDEX site_events_at ON site_events (at);
CREATE INDEX site_events_kind_at ON site_events (kind, at);

ALTER TABLE orders ADD COLUMN IF NOT EXISTS abandoned_at timestamptz;     -- the Stripe page expired unpaid
ALTER TABLE orders ADD COLUMN IF NOT EXISTS visitor text;

CREATE TABLE checkout_recovery (
    order_id         bigint PRIMARY KEY REFERENCES orders(id) ON DELETE CASCADE,
    email            text,
    name             text,
    consent          text,                    -- Stripe's promotions consent: opt_in / opt_out / null
    recovery_url     text,                    -- Stripe link that reopens the same checkout (valid 30 days)
    recovery_expires timestamptz,
    emailed_at       timestamptz,
    recovered_at     timestamptz
);
