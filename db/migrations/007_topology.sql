-- Stage 5: topology as seen from the observer. Filled by the worker (analytics/topology.py), rebuilt on every run.
-- A node is named by its public key, by "?prefix" when we have no advert of it, or "@observer".

-- "to_node heard from_node": neighbouring repeaters of flood paths, per day. Only hops resolved with confidence
-- keep a key; the rest stay as "?prefix".
CREATE TABLE edge_daily (
    day       date    NOT NULL,
    from_node text    NOT NULL,
    to_node   text    NOT NULL,
    packets   integer NOT NULL,
    PRIMARY KEY (day, from_node, to_node)
);

-- Routes of one sender (senders known for certain: adverts, anonymous requests, channel messages).
CREATE TABLE sender_route (
    sender       text    PRIMARY KEY,           -- node name, or the start of the key when it has none
    packets      integer NOT NULL,              -- receptions of the sender's packets
    routes       integer NOT NULL,              -- distinct routes to the observer
    entropy      real    NOT NULL,              -- bits; 0 = always the same route
    direct_share real    NOT NULL,              -- share of receptions heard from the sender itself
    critical     text[]  NOT NULL,              -- repeaters on every route
    judged       boolean NOT NULL,              -- enough packets to draw conclusions
    computed_at  timestamptz NOT NULL
);

CREATE TABLE critical_node (
    node        text    PRIMARY KEY,
    senders_cut integer NOT NULL,               -- senders that would stop reaching the observer without it
    senders     text[]  NOT NULL,
    computed_at timestamptz NOT NULL
);

-- The hop view gets the node id used by the topology tables (new column at the end, the rest is unchanged).
CREATE OR REPLACE VIEW v_hop_resolved AS
SELECT r.id AS reception_id, r.received_at, r.packet_hash, r.rssi, r.snr,
       h.position::int AS position, (r.hop_count - h.position)::int AS from_end, h.prefix,
       CASE WHEN pr.probability >= 0.7 THEN pr.pubkey END AS pubkey,
       pr.probability,
       CASE WHEN pr.path_key IS NULL THEN 'ещё не рассчитано'
            WHEN pr.pubkey IS NULL AND pr.probability >= 0.7 THEN 'узла нет в справочнике'
            WHEN pr.probability >= 0.7 THEN 'определён'
            ELSE 'не определён' END AS status,
       coalesce(CASE WHEN pr.probability >= 0.7 THEN coalesce(n.name, left(pr.pubkey, 8), '? ' || pr.unknown_prefix) END,
                '? ' || h.prefix) AS label,
       coalesce(CASE WHEN pr.probability >= 0.7 THEN coalesce(pr.pubkey, '?' || pr.unknown_prefix) END,
                '?' || h.prefix) AS node_id
FROM reception r
CROSS JOIN LATERAL unnest(r.path) WITH ORDINALITY AS h(prefix, position)
LEFT JOIN path_resolution pr ON pr.path_key = r.path_key AND pr.position = h.position AND pr.rank = 1
LEFT JOIN node n ON n.pubkey = pr.pubkey
WHERE r.route_type IN (0, 1);

-- Display name of a node id.
CREATE FUNCTION node_label(id text) RETURNS text LANGUAGE sql STABLE AS $$
    SELECT CASE WHEN id = '@observer' THEN 'observer'
                WHEN left(id, 1) = '?' THEN '? ' || substr(id, 2)
                ELSE coalesce((SELECT name FROM node WHERE pubkey = id), left(id, 8)) END
$$;

GRANT SELECT ON ALL TABLES IN SCHEMA public TO meshca_read;
