-- Nearby installers from Google Maps: which shop a customer picked and how the install went.
-- Google's terms allow keeping a shop's place ID indefinitely, but not its name, address or other details:
-- those are looked up from Google again whenever they're shown.
CREATE TABLE install_feedback (
    order_id      bigint PRIMARY KEY REFERENCES orders(id) ON DELETE CASCADE,
    token         text NOT NULL UNIQUE,          -- link in emails: lets the customer pick a shop and answer the survey
    place_id      text,                          -- the shop the customer picked (Google place ID)
    picked_at     timestamptz,
    other_shop    text,                          -- a shop they named that wasn't in the list (their own words)
    survey_sent_at timestamptz,
    answered_at   timestamptz,
    installed     text CHECK (installed IN ('yes', 'not_yet', 'myself', 'no')),
    rating        smallint CHECK (rating BETWEEN 1 AND 5),
    recommend     boolean,
    comment       text
);
CREATE INDEX install_feedback_place ON install_feedback (place_id) WHERE place_id IS NOT NULL;
