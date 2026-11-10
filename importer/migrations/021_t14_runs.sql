-- Every Turn 14 run (not just the last of each task), for the "Ready for Turn 14 review" card.
CREATE TABLE IF NOT EXISTS t14_runs (
    id        bigserial PRIMARY KEY,
    task      text NOT NULL,
    started   timestamptz NOT NULL,
    finished  timestamptz,
    status    text NOT NULL,          -- ok | stopped | failed
    error     text,
    requests  integer
);
CREATE INDEX IF NOT EXISTS t14_runs_started_idx ON t14_runs (started);
