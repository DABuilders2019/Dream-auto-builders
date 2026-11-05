-- SEMA Data API connection: the brands SEMA has approved for us, which of them we want on the site,
-- and a log of our requests (SEMA limits calls per minute and per subscription term).

CREATE TABLE sema_state (
    key        text PRIMARY KEY,
    value      jsonb,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE sema_brands (
    aaia            text PRIMARY KEY,           -- SEMA / Auto Care brand code, e.g. BKMQ
    name            text NOT NULL,              -- brand name at SEMA
    dataset_id      integer,
    sema_updated    text,                       -- SEMA's LastUpdated for the brand's data
    approved        boolean NOT NULL DEFAULT true,   -- false once it drops off our approved list
    wanted          boolean NOT NULL DEFAULT false,  -- on our list of brands to show
    wish_name       text,                       -- the name on our list that picked this brand
    brand_id        text REFERENCES brands(id) ON DELETE SET NULL,
    status          text,                       -- loaded / failed / loading
    loaded_updated  text,                       -- sema_updated at our last good load
    last_loaded     timestamptz,
    last_error      text,
    parts           integer,
    fitted_parts    integer,
    est_parts       integer,                    -- counted by Test connection, before loading
    est_calls       integer,                    -- API calls a full load would take
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE sema_requests (
    id       bigserial PRIMARY KEY,
    at       timestamptz NOT NULL DEFAULT now(),
    method   text NOT NULL,
    path     text NOT NULL,                     -- endpoint only: never the query string (it carries the login)
    status   integer,
    ms       integer,
    task     text
);
CREATE INDEX sema_requests_at ON sema_requests (at);
