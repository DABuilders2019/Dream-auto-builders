-- Brand page banners: a cut-out product photo and a short summary per brand.
ALTER TABLE brands
    ADD COLUMN banner_key        text,          -- storage key of the cut-out PNG
    ADD COLUMN banner_product_id bigint,        -- the part whose photo it is (NULL for an uploaded picture)
    ADD COLUMN banner_src        text,          -- 'auto' (chosen by the site), 'pick' (chosen on the admin page), 'upload'
    ADD COLUMN banner_boxed      boolean NOT NULL DEFAULT false,   -- no clean white background to remove
    ADD COLUMN banner_version    integer,
    ADD COLUMN banner_at         timestamptz,
    ADD COLUMN banner_blurb      text;          -- summary written on the admin page (else config/brand_blurbs.json)
