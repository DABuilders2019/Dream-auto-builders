-- Search support.
--  * pg_trgm for typo tolerance ("levling" -> "leveling") and fuzzy category/brand matching.
--  * pn_norm: part number with punctuation removed, so "69-3485", "693485" and "69 3485" all match.
--  * main_image: the photo shown on product cards (best part photo), kept up to date by the importer.
--  * search_lexicon: every word used in product names, part types, categories and brands; typos in a
--    search are corrected to the closest word in this list.

CREATE EXTENSION IF NOT EXISTS pg_trgm;

ALTER TABLE products ADD COLUMN pn_norm text GENERATED ALWAYS AS (lower(regexp_replace(part_number, '[^A-Za-z0-9]', '', 'g'))) STORED;
CREATE INDEX products_pn_norm_idx ON products (pn_norm text_pattern_ops);

ALTER TABLE products ADD COLUMN main_image text;

CREATE INDEX categories_name_trgm_idx ON categories USING gin (lower(name) gin_trgm_ops);

CREATE MATERIALIZED VIEW search_lexicon AS
    SELECT word, sum(ndoc)::int AS ndoc FROM (
        SELECT word, ndoc FROM ts_stat($$
            SELECT to_tsvector('simple', title || ' ' || coalesce(part_type, '')) FROM products WHERE status = 'active'
        $$)
        UNION ALL
        SELECT word, 1000 FROM ts_stat($$ SELECT to_tsvector('simple', name) FROM categories $$)
        UNION ALL
        SELECT word, 1000 FROM ts_stat($$ SELECT to_tsvector('simple', name) FROM brands $$)
    ) w
    WHERE word ~ '^[a-z][a-z0-9]{2,}$'
    GROUP BY word;
CREATE UNIQUE INDEX search_lexicon_word_idx ON search_lexicon (word text_pattern_ops);
CREATE INDEX search_lexicon_trgm_idx ON search_lexicon USING gin (word gin_trgm_ops);
