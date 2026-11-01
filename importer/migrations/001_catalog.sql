-- Dream Auto Builders catalog schema.
-- Products and fitment come from SEMA Data (PIES 7.0 product data, ACES 4.0 fitment,
-- and the Admark Vehicle Assignment sheet that spells vehicles out in words).

CREATE TABLE brands (
    id              text PRIMARY KEY,               -- our slug, e.g. 'readylift'
    name            text NOT NULL,                  -- display name, e.g. 'ReadyLift'
    aaia_id         text UNIQUE,                    -- SEMA / Auto Care brand code, e.g. 'BKMQ'
    display_price   text[] NOT NULL DEFAULT ARRAY['RET','RMP','LST'],  -- PIES price types to show, in order of preference
    active          boolean NOT NULL DEFAULT true,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE categories (
    id          text PRIMARY KEY,                   -- e.g. 'suspension', 'lift-kits'
    parent_id   text REFERENCES categories(id),
    name        text NOT NULL,
    sort        int NOT NULL DEFAULT 0
);

CREATE TABLE products (
    id                  bigserial PRIMARY KEY,
    brand_id            text NOT NULL REFERENCES brands(id),
    part_number         text NOT NULL,
    slug                text NOT NULL UNIQUE,       -- URL id, e.g. 'readylift-69-3485'
    status              text NOT NULL DEFAULT 'active' CHECK (status IN ('active','hidden','discontinued')),
    title               text NOT NULL,              -- full display title incl. brand
    short_name          text NOT NULL,
    part_type           text,                       -- PIES DEF description, e.g. 'Suspension Lift Kit'
    part_terminology_id int,                        -- PCdb part type id
    category_id         text REFERENCES categories(id),
    price               numeric(10,2),              -- price shown to shoppers (chosen by brands.display_price)
    price_type          text,                       -- which PIES price type 'price' came from
    map_price           numeric(10,2),              -- minimum advertised price (PIES RMP), never sell below
    upc                 text,
    weight_lb           numeric(8,2),
    package_dims        text,
    country_of_origin   text,
    marketing_copy      text,
    features            jsonb NOT NULL DEFAULT '[]',
    specs               jsonb NOT NULL DEFAULT '{}', -- curated specs shown on the product page
    attributes          jsonb NOT NULL DEFAULT '{}', -- every PIES product attribute, raw
    descriptions        jsonb NOT NULL DEFAULT '{}', -- every PIES description by code, raw
    prop65_warning      text,
    carb_eo             text,                        -- California Executive Order number, if any
    emissions           text CHECK (emissions IN ('carb_legal','federal_only')),  -- set for emissions parts
    fitment_status      text NOT NULL DEFAULT 'missing' CHECK (fitment_status IN ('vehicle_specific','universal','missing')),
                                                 -- missing = no vehicle data from the brand; such parts are hidden when a shopper filters by vehicle
    source_file_date    date,
    first_seen_at       timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    search              tsvector GENERATED ALWAYS AS (
                            setweight(to_tsvector('simple', coalesce(part_number,'')), 'A') ||
                            setweight(to_tsvector('english', coalesce(title,'')), 'A') ||
                            setweight(to_tsvector('english', coalesce(part_type,'')), 'B') ||
                            setweight(to_tsvector('english', coalesce(marketing_copy,'')), 'D')
                        ) STORED,
    UNIQUE (brand_id, part_number)
);
CREATE INDEX products_category_idx ON products (category_id) WHERE status = 'active';
CREATE INDEX products_brand_idx ON products (brand_id) WHERE status = 'active';
CREATE INDEX products_search_idx ON products USING gin (search);

CREATE TABLE product_prices (
    product_id  bigint NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    price_type  text NOT NULL,                      -- PIES: RET retail, RMP MAP, JBR jobber, LST list ...
    amount      numeric(10,2) NOT NULL,
    currency    text NOT NULL DEFAULT 'USD',
    PRIMARY KEY (product_id, price_type)
);

CREATE TABLE product_documents (
    product_id  bigint NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    kind        text NOT NULL,                      -- 'install' for installation instructions
    url         text NOT NULL,
    PRIMARY KEY (product_id, url)
);

-- Photos. Rows are created from the PIES file; the photo step downloads, checks and stores them.
CREATE TABLE product_images (
    id          bigserial PRIMARY KEY,
    product_id  bigint NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    asset_type  text,                               -- PIES asset type: P04 primary, P01 additional ...
    file_name   text,
    source_url  text,
    sort        int NOT NULL DEFAULT 0,
    status      text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','ok','blocked','failed','skipped')),
    kind        text CHECK (kind IN ('part','vehicle','packaging')),  -- vehicle/packaging shots are shown after part photos
    md5         text,                               -- of the original bytes
    storage_key text,                               -- where the resized copy lives
    width       int,
    height      int,
    note        text,
    checked_at  timestamptz,
    UNIQUE NULLS NOT DISTINCT (product_id, source_url, file_name)
);
CREATE INDEX product_images_status_idx ON product_images (status);
CREATE INDEX product_images_md5_idx ON product_images (md5);

-- Photo review results, applied whenever a photo is checked. key is the md5 of the original
-- file bytes, or the source URL when the review was done on a link.
CREATE TABLE image_reviews (
    key         text PRIMARY KEY,
    key_type    text NOT NULL CHECK (key_type IN ('md5','url')),
    verdict     text NOT NULL CHECK (verdict IN ('blocked','vehicle','packaging')),   -- blocked = never show; vehicle = truck/install shot; packaging = box shot
    reason      text,
    added_at    timestamptz NOT NULL DEFAULT now()
);

-- Vehicles as shoppers pick them (Year / Make / Model).
CREATE TABLE vehicles (
    id      serial PRIMARY KEY,
    year    smallint NOT NULL,
    make    text NOT NULL,
    model   text NOT NULL,
    UNIQUE (year, make, model)
);
CREATE INDEX vehicles_make_model_idx ON vehicles (make, model);

-- Which product fits which vehicle, from the Admark Vehicle Assignment sheet (one row per sheet row).
CREATE TABLE fitment (
    product_id      bigint NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    vehicle_id      int NOT NULL REFERENCES vehicles(id),
    submodel        text,
    engine          text,
    body            text,
    transmission    text,
    bed             text,
    wheelbase       text,
    position        text,
    notes           text,
    mfr_label       text
);
CREATE INDEX fitment_vehicle_idx ON fitment (vehicle_id);
CREATE INDEX fitment_product_idx ON fitment (product_id);

-- Raw ACES applications, kept with every ID so engine / drive type can be decoded once the
-- VCdb vehicle database is available.
CREATE TABLE aces_applications (
    product_id      bigint NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    app_id          text,
    base_vehicle_id int,
    year_from       smallint,
    year_to         smallint,
    make_id         int,
    model_id        int,
    vcdb            jsonb NOT NULL DEFAULT '{}',    -- every other VCdb attribute id (SubModel, DriveType, EngineBase ...)
    qualifiers      text[],                         -- Qdb qualifier texts, e.g. 'with Dual Rear Wheels (DRW)'
    notes           text[],
    qty             text,
    position_id     int,
    part_type_id    int,
    mfr_label       text
);
CREATE INDEX aces_applications_product_idx ON aces_applications (product_id);

CREATE TABLE import_runs (
    id          bigserial PRIMARY KEY,
    brand_id    text REFERENCES brands(id),
    step        text NOT NULL,                      -- 'catalog' or 'photos'
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    status      text NOT NULL DEFAULT 'running' CHECK (status IN ('running','ok','failed')),
    stats       jsonb NOT NULL DEFAULT '{}',
    error       text
);

CREATE TABLE schema_migrations (
    version text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);
