-- Stage 4: which node stands behind a path prefix. Filled by the worker (analytics/resolve.py), rebuilt on every run.

-- A path as one text key, so that results can be stored once per distinct path and joined to receptions.
CREATE FUNCTION path_key(path text[]) RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT array_to_string(path, ',')
$$;
ALTER TABLE reception ADD COLUMN path_key text GENERATED ALWAYS AS (path_key(path)) STORED;
CREATE INDEX reception_path_key_idx ON reception (path_key);

CREATE TABLE path_resolution (
    path_key       text     NOT NULL,
    position       smallint NOT NULL,           -- 1 = first repeater after the sender
    rank           smallint NOT NULL,           -- 1 = the most probable candidate
    prefix         text     NOT NULL,
    pubkey         text,                        -- NULL: a node we have no advert of
    unknown_prefix text,                        -- for such a node: the longest prefix it was seen with
    probability    real     NOT NULL,
    method_version smallint NOT NULL,
    computed_at    timestamptz NOT NULL,
    PRIMARY KEY (path_key, position, rank)
);
CREATE INDEX path_resolution_pubkey_idx ON path_resolution (pubkey) WHERE rank = 1;

-- One row per run: how much was resolved and how accurate the method is on hidden long prefixes.
CREATE TABLE resolution_run (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    computed_at    timestamptz NOT NULL DEFAULT now(),
    method_version smallint NOT NULL,
    window_days    integer  NOT NULL,
    nodes          integer  NOT NULL,           -- known repeaters used as candidates
    paths          integer  NOT NULL,           -- distinct paths
    packets        integer  NOT NULL,
    iterations     integer  NOT NULL,
    seconds        real     NOT NULL,
    resolved_share real,                        -- share of hops (by packets) with a known node at 0.7 or more
    evaluation     jsonb    NOT NULL            -- see analytics/resolve.py: Evaluation.as_dict
);

-- One row per path element of a flood reception with the best candidate. A node is named only at
-- probability 0.7 or more; below that the hop keeps its prefix.
CREATE VIEW v_hop_resolved AS
SELECT r.id AS reception_id, r.received_at, r.packet_hash, r.rssi, r.snr,
       h.position::int AS position, (r.hop_count - h.position)::int AS from_end, h.prefix,
       CASE WHEN pr.probability >= 0.7 THEN pr.pubkey END AS pubkey,
       pr.probability,
       CASE WHEN pr.path_key IS NULL THEN 'ещё не рассчитано'
            WHEN pr.pubkey IS NULL AND pr.probability >= 0.7 THEN 'узла нет в справочнике'
            WHEN pr.probability >= 0.7 THEN 'определён'
            ELSE 'не определён' END AS status,
       coalesce(CASE WHEN pr.probability >= 0.7 THEN coalesce(n.name, left(pr.pubkey, 8), '? ' || pr.unknown_prefix) END,
                '? ' || h.prefix) AS label
FROM reception r
CROSS JOIN LATERAL unnest(r.path) WITH ORDINALITY AS h(prefix, position)
LEFT JOIN path_resolution pr ON pr.path_key = r.path_key AND pr.position = h.position AND pr.rank = 1
LEFT JOIN node n ON n.pubkey = pr.pubkey
WHERE r.route_type IN (0, 1);

GRANT SELECT ON ALL TABLES IN SCHEMA public TO meshca_read;
