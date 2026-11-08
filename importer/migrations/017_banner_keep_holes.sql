-- Per-brand choice from Admin > Logos: keep the white inside the logo's letters (true), clear it (false),
-- or NULL: the standard (config/home.json banner_logo_tweaks, else cleared).
ALTER TABLE brands ADD COLUMN banner_keep_holes boolean;
