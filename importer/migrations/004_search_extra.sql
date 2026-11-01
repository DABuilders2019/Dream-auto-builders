-- Extra searchable words per product: its category names and key specs (product line, series,
-- tire size, bolt pattern). Lets "all terrain tire" find tires named "Baja Boss A/T".
ALTER TABLE products ADD COLUMN search_extra text;
ALTER TABLE products DROP COLUMN search;
ALTER TABLE products ADD COLUMN search tsvector GENERATED ALWAYS AS (
    setweight(to_tsvector('simple', coalesce(part_number, '')), 'A') ||
    setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
    setweight(to_tsvector('english', coalesce(part_type, '')), 'B') ||
    setweight(to_tsvector('english', coalesce(search_extra, '')), 'B') ||
    setweight(to_tsvector('english', coalesce(marketing_copy, '')), 'D')
) STORED;
CREATE INDEX products_search_idx ON products USING gin (search);
