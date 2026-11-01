-- Orders paid through Stripe Checkout.
CREATE SEQUENCE IF NOT EXISTS order_number_seq START 100001;

CREATE TABLE orders (
    id                bigserial PRIMARY KEY,
    number            text NOT NULL UNIQUE DEFAULT 'DAB-' || nextval('order_number_seq'),
    status            text NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'paid', 'on_hold', 'shipped', 'cancelled', 'refunded', 'payment_failed')),
    hold_reason       text,
    stripe_session_id text UNIQUE,
    payment_intent    text,
    email             text,
    name              text,
    phone             text,
    ship_address      jsonb,
    vehicle           text,                       -- the vehicle the shopper had selected
    subtotal          numeric(10,2) NOT NULL,
    shipping          numeric(10,2),
    tax               numeric(10,2),
    total             numeric(10,2),
    currency          text NOT NULL DEFAULT 'usd',
    carrier           text,
    tracking_number   text,
    events            jsonb NOT NULL DEFAULT '[]',  -- [{at, text}] history shown on the admin page
    created_at        timestamptz NOT NULL DEFAULT now(),
    paid_at           timestamptz,
    shipped_at        timestamptz,
    updated_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX orders_status_idx ON orders (status, created_at DESC);
CREATE INDEX orders_email_idx ON orders (lower(email));

CREATE TABLE order_items (
    id          bigserial PRIMARY KEY,
    order_id    bigint NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id  bigint REFERENCES products(id) ON DELETE SET NULL,
    slug        text,
    brand_name  text NOT NULL,
    part_number text NOT NULL,
    title       text NOT NULL,
    qty         integer NOT NULL CHECK (qty > 0),
    unit_price  numeric(10,2) NOT NULL,
    emissions   text
);
CREATE INDEX order_items_order_idx ON order_items (order_id);
