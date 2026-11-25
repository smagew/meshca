-- Stage 6: time patterns of every sender known for certain (SPEC 7.5). Filled by the worker (analytics/rhythm.py),
-- rebuilt on every run. One row per sender and kind of packet: adverts of a node follow a timer, its channel
-- messages follow a person, so they are judged apart.

CREATE TABLE node_rhythm (
    subject          text        NOT NULL,      -- public key; the sender name for channel messages
    kind             text        NOT NULL,      -- as in v_sender: объявление, анонимный запрос, сообщение канала
    label            text        NOT NULL,      -- display name
    window_days      integer     NOT NULL,
    events           integer     NOT NULL,      -- distinct packets
    median_gap_s     real,
    burstiness       real,                      -- -1 clock, 0 random, toward 1 bursts
    period_s         real,                      -- best period found, confirmed or not
    period_strength  real,                      -- 0..1
    period_p         real,                      -- p-value on the half of the data the period was not searched on
    period_confirmed boolean     NOT NULL,
    night_share      real,                      -- share of events 00-06 local time
    profile          integer[]   NOT NULL,      -- 168 hours of the week, Monday 00h first, local time
    judged           boolean     NOT NULL,      -- at least 30 events
    method_version   smallint    NOT NULL,
    computed_at      timestamptz NOT NULL,
    PRIMARY KEY (subject, kind)
);

GRANT SELECT ON ALL TABLES IN SCHEMA public TO meshca_read;
