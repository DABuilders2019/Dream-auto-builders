-- Products for brands approved at Turn 14 but not (yet) at SEMA, built from Turn 14's own item data.
-- When SEMA approves the brand later, its load matches the same brand code and part numbers, so the same
-- products (and their web addresses) are upgraded with SEMA's data and fitment.

ALTER TABLE t14_brands ADD COLUMN IF NOT EXISTS load_catalog boolean NOT NULL DEFAULT false;  -- our products come from Turn 14
ALTER TABLE t14_brands ADD COLUMN IF NOT EXISTS catalog_loaded timestamptz;

ALTER TABLE t14_items ADD COLUMN IF NOT EXISTS part_description text;
ALTER TABLE t14_items ADD COLUMN IF NOT EXISTS category text;
ALTER TABLE t14_items ADD COLUMN IF NOT EXISTS subcategory text;
ALTER TABLE t14_items ADD COLUMN IF NOT EXISTS dimensions jsonb;
ALTER TABLE t14_items ADD COLUMN IF NOT EXISTS thumbnail text;
ALTER TABLE t14_items ADD COLUMN IF NOT EXISTS units_per_sku integer;

-- Turn 14's descriptions and files (photos, instructions) per item, only for brands loaded from Turn 14.
CREATE TABLE IF NOT EXISTS t14_item_data (
    item_id       text PRIMARY KEY,
    descriptions  jsonb NOT NULL DEFAULT '[]',
    files         jsonb NOT NULL DEFAULT '[]',
    updated_at    timestamptz NOT NULL DEFAULT now()
);
