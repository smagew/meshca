-- Kinds of packets carried by each link, for colouring the link graph: adverts, channel messages, direct packets
-- (requests, answers, private messages, paths, acknowledgements, anonymous requests). Filled by the topology job.
ALTER TABLE edge_daily
    ADD COLUMN adverts integer NOT NULL DEFAULT 0,
    ADD COLUMN channel integer NOT NULL DEFAULT 0,
    ADD COLUMN direct  integer NOT NULL DEFAULT 0;
