-- Small settings the app keeps for itself (for example, which version of the category rules products were sorted with).
CREATE TABLE IF NOT EXISTS app_state (
    key        text PRIMARY KEY,
    value      text,
    updated_at timestamptz NOT NULL DEFAULT now()
);
