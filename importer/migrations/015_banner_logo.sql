-- The brand logo prepared for the banner (white background removed, dark lettering turned white).
ALTER TABLE brands
    ADD COLUMN banner_logo_key  text,
    ADD COLUMN banner_logo_from text;       -- the logo it was made from, so a new logo makes a new one
