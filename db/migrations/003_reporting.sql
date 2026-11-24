-- Reporting layer for the dashboards: names for codes, views over the decoded layer and a read-only role.

CREATE FUNCTION payload_type_name(t smallint) RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT CASE t WHEN 0 THEN 'REQ' WHEN 1 THEN 'RESPONSE' WHEN 2 THEN 'TXT_MSG' WHEN 3 THEN 'ACK' WHEN 4 THEN 'ADVERT'
        WHEN 5 THEN 'GRP_TXT' WHEN 6 THEN 'GRP_DATA' WHEN 7 THEN 'ANON_REQ' WHEN 8 THEN 'PATH' WHEN 9 THEN 'TRACE'
        WHEN 10 THEN 'MULTIPART' WHEN 11 THEN 'CONTROL' WHEN 15 THEN 'RAW_CUSTOM' ELSE 'TYPE_' || t END
$$;

CREATE FUNCTION route_type_name(t smallint) RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT CASE t WHEN 0 THEN 'TRANSPORT_FLOOD' WHEN 1 THEN 'FLOOD' WHEN 2 THEN 'DIRECT' WHEN 3 THEN 'TRANSPORT_DIRECT'
        ELSE 'ROUTE_' || t END
$$;

CREATE FUNCTION node_role_name(r smallint) RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT CASE r WHEN 1 THEN 'клиент' WHEN 2 THEN 'ретранслятор' WHEN 3 THEN 'сервер комнат' WHEN 4 THEN 'датчик'
        ELSE 'неизвестно' END
$$;

-- One row per path element of a flood reception. from_end = 0 is the repeater the observer heard itself.
CREATE VIEW v_hop AS
SELECT r.id AS reception_id, r.received_at, r.packet_hash, r.prefix_size, r.hop_count,
       h.prefix, h.position::int AS position, (r.hop_count - h.position)::int AS from_end
FROM reception r CROSS JOIN LATERAL unnest(r.path) WITH ORDINALITY AS h(prefix, position)
WHERE r.route_type IN (0, 1);

-- Which node stands behind a path prefix, by the node directory alone: only repeaters and room servers relay.
-- A prefix with exactly one candidate is taken as that node; the probabilistic method comes with stage 4.
CREATE VIEW v_prefix_node AS
SELECT p.prefix, count(n.pubkey)::int AS candidates,
       CASE WHEN count(n.pubkey) = 1 THEN min(n.pubkey) END AS pubkey,
       CASE WHEN count(n.pubkey) = 1 THEN min(n.name) END AS name
FROM (SELECT DISTINCT unnest(path) AS prefix FROM reception WHERE route_type IN (0, 1)) p
LEFT JOIN node n ON n.role IN (2, 3) AND left(n.pubkey, length(p.prefix)) = p.prefix
GROUP BY p.prefix;

-- Packets whose sender is known without guessing: adverts, anonymous requests, decrypted channel messages.
-- The sender name of a channel message comes from its text and is not verified by the protocol.
CREATE VIEW v_sender AS
SELECT a.packet_hash, 'объявление'::text AS kind, a.pubkey, coalesce(n.name, a.name, left(a.pubkey, 8)) AS sender
FROM advert a LEFT JOIN node n ON n.pubkey = a.pubkey WHERE a.signature_ok
UNION ALL
SELECT q.packet_hash, 'анонимный запрос', q.sender_pubkey, coalesce(n.name, left(q.sender_pubkey, 8))
FROM anon_request q LEFT JOIN node n ON n.pubkey = q.sender_pubkey
UNION ALL
SELECT m.packet_hash, 'сообщение канала', NULL, m.sender_name
FROM channel_message m WHERE m.decrypted AND m.sender_name IS NOT NULL;

-- Read access for dashboards; a login role is added to it by the migrate step when a password is configured.
DO $$ BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'meshca_read') THEN
        CREATE ROLE meshca_read NOLOGIN;
    END IF;
END $$;
GRANT USAGE ON SCHEMA public TO meshca_read;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO meshca_read;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO meshca_read;
