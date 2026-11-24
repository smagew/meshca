-- Stage 4, second part: who sent a packet that names its sender by one byte (analytics/origin.py).

CREATE TABLE origin_resolution (
    packet_hash    text     NOT NULL REFERENCES packet (hash) ON DELETE CASCADE,
    rank           smallint NOT NULL,           -- 1 = the most probable candidate
    pubkey         text,                        -- NULL: a node we have no advert of
    probability    real     NOT NULL,
    method         text     NOT NULL,           -- path_fingerprint
    method_version smallint NOT NULL,
    computed_at    timestamptz NOT NULL,
    PRIMARY KEY (packet_hash, rank)
);
CREATE INDEX origin_resolution_pubkey_idx ON origin_resolution (pubkey) WHERE rank = 1;

CREATE TABLE origin_run (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    computed_at    timestamptz NOT NULL DEFAULT now(),
    method_version smallint NOT NULL,
    window_days    integer  NOT NULL,
    nodes          integer  NOT NULL,           -- candidate nodes
    known_packets  integer  NOT NULL,           -- packets with a certain sender the fingerprints were learned from
    packets        integer  NOT NULL,           -- packets resolved
    seconds        real     NOT NULL,
    resolved_share real,                        -- share of them with a known node at 0.7 or more
    evaluation     jsonb    NOT NULL
);

-- REQ, RESPONSE, TXT_MSG, PATH with the best sender candidate; a node is named at probability 0.7 or more.
-- "нет пути от отправителя": only direct-routed copies were heard, their path does not lead back to the sender.
CREATE VIEW v_origin_resolved AS
SELECT d.packet_hash, d.src_hash, d.dst_hash,
       CASE WHEN o.probability >= 0.7 THEN o.pubkey END AS pubkey,
       o.probability,
       CASE WHEN o.packet_hash IS NULL THEN 'нет пути от отправителя'
            WHEN o.probability < 0.7 THEN 'не определён'
            WHEN o.pubkey IS NULL THEN 'узла нет в справочнике'
            ELSE 'определён' END AS status,
       coalesce(CASE WHEN o.probability >= 0.7 AND o.pubkey IS NOT NULL THEN coalesce(n.name, left(o.pubkey, 8)) END,
                '? ' || d.src_hash) AS label
FROM direct_packet d
LEFT JOIN origin_resolution o ON o.packet_hash = d.packet_hash AND o.rank = 1
LEFT JOIN node n ON n.pubkey = o.pubkey;

GRANT SELECT ON ALL TABLES IN SCHEMA public TO meshca_read;
