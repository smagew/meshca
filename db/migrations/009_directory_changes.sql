-- Stage 7, the part that needs no history: changes in the node directory and node clocks (SPEC 7.8).

-- Clock of a node: its advert carries the node's own time; the difference to when we first heard the advert is
-- the clock error (plus the flood delay, seconds). Median over the node's adverts, so one odd advert does not count.
CREATE VIEW v_node_clock AS
SELECT a.pubkey, coalesce(n.name, left(a.pubkey, 8)) AS label, n.role, count(*) AS adverts,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM a.advert_time - p.first_seen)) AS offset_s,
       max(p.first_seen) AS last_advert
FROM advert a JOIN packet p ON p.hash = a.packet_hash LEFT JOIN node n ON n.pubkey = a.pubkey
WHERE a.signature_ok AND a.advert_time IS NOT NULL
GROUP BY a.pubkey, n.name, n.role;

-- Everything that changed in the directory, one row per event.
CREATE VIEW v_node_event AS
SELECT n.first_seen AS at, n.pubkey, coalesce(n.name, left(n.pubkey, 8)) AS label, 'новый узел'::text AS event,
       NULL::text AS old, node_role_name(n.role) AS new
FROM node n
UNION ALL
SELECT h.changed_at, h.pubkey, coalesce(n.name, left(h.pubkey, 8)),
       CASE h.field WHEN 'name' THEN 'смена имени' WHEN 'role' THEN 'смена роли' ELSE 'смена координат' END,
       h.old, h.new
FROM node_history h JOIN node n ON n.pubkey = h.pubkey
UNION ALL
-- The same name under another key: a reflashed device, a second device of one owner, or someone posing as it.
SELECT b.first_seen, b.pubkey, b.name, 'то же имя, другой ключ', left(a.pubkey, 8), left(b.pubkey, 8)
FROM node a JOIN node b ON a.name = b.name AND a.pubkey <> b.pubkey AND a.first_seen < b.first_seen
WHERE a.name IS NOT NULL AND a.name <> '';

GRANT SELECT ON ALL TABLES IN SCHEMA public TO meshca_read;
