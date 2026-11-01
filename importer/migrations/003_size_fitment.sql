-- Tires and wheels fit by size (tire size, bolt pattern) rather than by year/make/model.
ALTER TABLE products DROP CONSTRAINT products_fitment_status_check;
ALTER TABLE products ADD CONSTRAINT products_fitment_status_check
    CHECK (fitment_status IN ('vehicle_specific', 'universal', 'by_size', 'missing'));

-- Tire size with punctuation removed ("LT285/70R17" -> "28570r17", "35X12.50R20LT" -> "351250r20"),
-- so a shopper can search by size however they type it.
ALTER TABLE products ADD COLUMN size_norm text;
CREATE INDEX products_size_norm_idx ON products (size_norm text_pattern_ops) WHERE size_norm IS NOT NULL;
