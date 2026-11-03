-- Turn 14 Distribution data feed: a local copy of what the Turn 14 API returns for the brands we sell,
-- matched to our products (Turn 14 asks that its API is used as a feed into a local database).

CREATE TABLE t14_brands (
    id          integer PRIMARY KEY,            -- Turn 14 brand id
    name        text NOT NULL,
    aaia        text[] NOT NULL DEFAULT '{}',   -- brand codes, matched to brands.aaia_id
    approved    boolean NOT NULL DEFAULT false, -- at least one price group approved for our account
    restricted  boolean NOT NULL DEFAULT false, -- some price groups not approved
    dropship    boolean,
    brand_id    text REFERENCES brands(id) ON DELETE SET NULL,   -- our brand, when matched
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE t14_items (
    item_id               text PRIMARY KEY,     -- Turn 14 item id ("G" suffix = product group / kit)
    t14_brand_id          integer NOT NULL,
    part_number           text,                 -- Turn 14 part number
    mfr_part_number       text,
    mfr_norm              text,                 -- mfr_part_number, letters and digits only, lower case
    barcode               text,
    alternate_part_number text,
    product_name          text,
    active                boolean NOT NULL DEFAULT true,
    regular_stock         boolean,
    not_carb_approved     boolean,
    carb_eo_number        text,
    ltl_freight_required  boolean,
    air_freight_prohibited boolean,
    clearance_item        boolean,
    prop_65               text,
    epa                   text,
    product_id            bigint REFERENCES products(id) ON DELETE SET NULL,
    match_method          text,                 -- part_number, barcode, alternate
    seen_at               timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX t14_items_product_idx ON t14_items (product_id);
CREATE INDEX t14_items_brand_idx ON t14_items (t14_brand_id);

CREATE TABLE t14_pricing (
    item_id        text PRIMARY KEY,
    purchase_cost  numeric(10,2),
    has_map        boolean,
    can_purchase   boolean,
    map_price      numeric(10,2),
    retail_price   numeric(10,2),
    jobber_price   numeric(10,2),
    pricelists     jsonb NOT NULL DEFAULT '[]',
    updated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE t14_inventory (
    item_id      text PRIMARY KEY,
    by_location  jsonb NOT NULL DEFAULT '{}',   -- {"01": 3, "02": 0, ...}
    total        integer NOT NULL DEFAULT 0,
    mfr_stock    integer,
    mfr_esd      date,
    eta          jsonb,
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE t14_locations (
    id          text PRIMARY KEY,
    name        text,
    city        text,
    state       text,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- Shared between processes: the access token (reused until it expires) and when each sync last ran.
CREATE TABLE t14_state (
    key         text PRIMARY KEY,
    value       jsonb NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- Every request sent to Turn 14, for the usage limits (per hour / per 24 hours) and for debugging.
CREATE TABLE t14_requests (
    id      bigserial PRIMARY KEY,
    at      timestamptz NOT NULL DEFAULT now(),
    method  text NOT NULL,
    path    text NOT NULL,
    status  integer,
    ms      integer,
    task    text
);
CREATE INDEX t14_requests_at_idx ON t14_requests (at);
