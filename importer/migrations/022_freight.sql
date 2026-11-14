-- Freight orders: parts too big for UPS. Checkout places a hold for the parts and saves the card; the owner
-- enters the freight price; the customer approves (parts captured, freight charged) or cancels (hold released).
ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_status_check;
ALTER TABLE orders ADD CONSTRAINT orders_status_check
    CHECK (status IN ('pending', 'paid', 'on_hold', 'shipped', 'cancelled', 'refunded', 'payment_failed',
                      'awaiting_freight', 'freight_quoted'));

ALTER TABLE orders ADD COLUMN IF NOT EXISTS freight              boolean NOT NULL DEFAULT false;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS freight_token        text UNIQUE;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS freight_quote        numeric(10,2);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS freight_note         text;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS freight_quoted_at    timestamptz;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS hold_placed_at       timestamptz;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS freight_reminded_at  timestamptz;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS owner_reminded_at    timestamptz;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS stripe_customer      text;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS freight_payment      text;     -- PaymentIntent of the freight charge

CREATE INDEX IF NOT EXISTS orders_freight_idx ON orders (status) WHERE freight;
