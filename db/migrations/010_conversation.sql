-- Stage 9: who talks to whom (SPEC 7.7). Filled by the worker (analytics/conversation.py), rebuilt on every run.

-- Direct packets of one pair of one-byte key hashes with pauses under 10 minutes.
CREATE TABLE dm_session (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    a                 text        NOT NULL,     -- one-byte hash, a < b
    b                 text        NOT NULL,
    started_at        timestamptz NOT NULL,
    ended_at          timestamptz NOT NULL,
    packets           integer     NOT NULL,
    a_to_b            integer     NOT NULL,
    b_to_a            integer     NOT NULL,
    kinds             text[]      NOT NULL,     -- in order: "a:REQ", "b:RESPONSE"...
    responses         integer     NOT NULL,     -- answers from the other side within 30 s
    median_response_s real,
    computed_at       timestamptz NOT NULL
);
CREATE INDEX dm_session_started_idx ON dm_session (started_at);

-- "source answers target" in a channel: mentions are certain, quick follows are compared with chance.
CREATE TABLE channel_link (
    channel     text        NOT NULL,
    source      text        NOT NULL,
    target      text        NOT NULL,
    mentions    integer     NOT NULL,
    quick       integer     NOT NULL,           -- messages within 2 minutes after a message of target
    expected    real        NOT NULL,           -- quick follows expected if both wrote at random times
    computed_at timestamptz NOT NULL,
    PRIMARY KEY (channel, source, target)
);

-- Names of known nodes whose key starts with a one-byte hash: who a direct packet's end may be.
CREATE FUNCTION hash_names(h text) RETURNS text LANGUAGE sql STABLE AS $$
    SELECT CASE count(*) WHEN 0 THEN h || ' (неизвестен)'
                         WHEN 1 THEN h || ' = ' || max(coalesce(name, left(pubkey, 8)))
                         ELSE h || ' (' || count(*) || ' кандидатов: ' ||
                              string_agg(coalesce(name, left(pubkey, 8)), ', ' ORDER BY last_seen DESC) || ')' END
    FROM node WHERE left(pubkey, 2) = h
$$;

GRANT SELECT ON ALL TABLES IN SCHEMA public TO meshca_read;
