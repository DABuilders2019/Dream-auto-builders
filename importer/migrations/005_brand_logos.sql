-- Brand logos from the SEMA PIES logo asset (AssetType LGO), downloaded by the photos step.
ALTER TABLE brands ADD COLUMN IF NOT EXISTS logo_url text;   -- link in the latest PIES file
ALTER TABLE brands ADD COLUMN IF NOT EXISTS logo_src text;   -- link the stored logo was made from
ALTER TABLE brands ADD COLUMN IF NOT EXISTS logo_key text;   -- stored file (same storage as product photos)
