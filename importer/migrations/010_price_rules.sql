-- Every price type a brand's data gives per product (RET, RMP, LST, JBR, ...), so the price rule can be
-- changed on the admin page without reloading; and a markup for brands that only give dealer/jobber prices.
ALTER TABLE products ADD COLUMN IF NOT EXISTS prices jsonb;
ALTER TABLE brands ADD COLUMN IF NOT EXISTS price_markup numeric;   -- percent added to a non-retail price type
