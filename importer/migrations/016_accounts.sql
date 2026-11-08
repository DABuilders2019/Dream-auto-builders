-- Customer accounts: sign in with a password or a code emailed to them; orders, garage and builds kept on the account.
CREATE TABLE accounts (
    id                 bigserial PRIMARY KEY,
    email              text NOT NULL UNIQUE,            -- stored lower-case
    name               text,
    password_hash      text,                            -- NULL: signs in with emailed codes only
    email_verified     boolean NOT NULL DEFAULT false,  -- proved by signing in with a code (shows past orders by email)
    stripe_customer_id text,
    garage             jsonb NOT NULL DEFAULT '[]',     -- vehicle ids, newest last
    active_vehicle     integer,
    builds             jsonb NOT NULL DEFAULT '[]',
    source             text NOT NULL DEFAULT 'signup',  -- 'signup' or 'checkout' (saved at checkout)
    created_at         timestamptz NOT NULL DEFAULT now(),
    last_login_at      timestamptz
);

CREATE TABLE account_codes (
    id          bigserial PRIMARY KEY,
    email       text NOT NULL,
    code_hash   text NOT NULL,
    expires_at  timestamptz NOT NULL,
    attempts    integer NOT NULL DEFAULT 0,
    used_at     timestamptz,
    ip          text,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX account_codes_email ON account_codes (email, created_at DESC);

ALTER TABLE orders ADD COLUMN account_id bigint REFERENCES accounts(id) ON DELETE SET NULL;
CREATE INDEX orders_account ON orders (account_id);
