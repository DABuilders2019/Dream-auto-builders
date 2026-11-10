-- Price match requests from the product page: the customer sends a link or a photo of a lower price, we review it
-- on Admin > Price Match, and an approved match is claimed from an emailed link into the cart for 24 hours.
CREATE TABLE IF NOT EXISTS price_matches (
    id            bigserial PRIMARY KEY,
    product_id    bigint REFERENCES products(id) ON DELETE SET NULL,
    our_price     numeric(10,2),
    their_price   numeric(10,2) NOT NULL,
    qty           integer NOT NULL DEFAULT 1,
    url           text,
    photo_key     text,
    email         text NOT NULL,
    account_id    bigint,
    ip            text,
    status        text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'declined')),
    matched_price numeric(10,2),
    reply         text,
    decided_at    timestamptz,
    expires_at    timestamptz,
    token         text UNIQUE,
    used_order_id bigint,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS price_matches_status_idx ON price_matches (status, created_at DESC);
