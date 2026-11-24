-- Decoded layer: everything here is derived from raw_reception by the worker and can be rebuilt from it.
-- Keys, hashes and path prefixes are lowercase hex text; the packet hash keeps the observer's uppercase form.

CREATE TABLE reception (
    id              bigint      PRIMARY KEY REFERENCES raw_reception (id),
    received_at     timestamptz NOT NULL,       -- observer clock; collector clock when the observer gave none
    observer_id     text        NOT NULL,       -- public key of the observer
    packet_hash     text        NOT NULL,
    route_type      smallint    NOT NULL,       -- 0 TRANSPORT_FLOOD, 1 FLOOD, 2 DIRECT, 3 TRANSPORT_DIRECT
    snr             real,
    rssi            integer,
    packet_len      integer     NOT NULL,
    path            text[]      NOT NULL,       -- flood: repeaters passed so far; direct: hops still to go
    hop_count       smallint    NOT NULL,
    prefix_size     smallint    NOT NULL,       -- bytes per path element: 1, 2 or 3
    last_hop        text,                       -- flood only: the repeater the observer heard; NULL = heard the sender itself
    transport_codes text,
    hash_ok         boolean,                    -- our hash equals the observer's; NULL when the payload was redacted
    decoder_version smallint    NOT NULL
);
CREATE INDEX reception_received_at_idx ON reception (received_at);
CREATE INDEX reception_packet_hash_idx ON reception (packet_hash);
CREATE INDEX reception_last_hop_idx ON reception (last_hop, received_at);

CREATE TABLE packet (
    hash            text        PRIMARY KEY,
    first_seen      timestamptz NOT NULL,
    last_seen       timestamptz NOT NULL,
    payload_type    smallint    NOT NULL,
    payload_len     integer     NOT NULL,
    payload         bytea,                      -- NULL after the text retention period for decrypted channel messages
    reception_count integer     NOT NULL DEFAULT 1
);
CREATE INDEX packet_first_seen_idx ON packet (first_seen);
CREATE INDEX packet_type_first_seen_idx ON packet (payload_type, first_seen);

CREATE TABLE advert (
    packet_hash  text        PRIMARY KEY REFERENCES packet (hash) ON DELETE CASCADE,
    pubkey       text        NOT NULL,
    advert_time  timestamptz,                   -- clock of the node, often wrong
    role         smallint,                      -- 1 chat, 2 repeater, 3 room server, 4 sensor
    lat          double precision,
    lon          double precision,
    name         text,
    signature_ok boolean     NOT NULL
);
CREATE INDEX advert_pubkey_idx ON advert (pubkey);

-- Node directory, built only from adverts with a valid signature.
CREATE TABLE node (
    pubkey       text        PRIMARY KEY,
    name         text,
    role         smallint,
    lat          double precision,
    lon          double precision,
    first_seen   timestamptz NOT NULL,
    last_seen    timestamptz NOT NULL,
    advert_count integer     NOT NULL DEFAULT 1
);

CREATE TABLE node_history (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pubkey     text        NOT NULL REFERENCES node (pubkey) ON DELETE CASCADE,
    changed_at timestamptz NOT NULL,
    field      text        NOT NULL,            -- name, role, position
    old        text,
    new        text
);
CREATE INDEX node_history_pubkey_idx ON node_history (pubkey, changed_at);

-- Channels whose key is known: Public, hashtag channels from the settings and those mentioned in decrypted texts.
CREATE TABLE channel (
    name     text        PRIMARY KEY,
    key      text        NOT NULL,
    hash     text        NOT NULL,              -- first byte of SHA-256(key), what a packet carries
    source   text        NOT NULL,              -- builtin, config, mention
    added_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE channel_message (
    packet_hash    text        PRIMARY KEY REFERENCES packet (hash) ON DELETE CASCADE,
    payload_type   smallint    NOT NULL,        -- 5 text, 6 data
    channel_hash   text        NOT NULL,
    channel        text,                        -- NULL while no known key fits
    decrypted      boolean     NOT NULL DEFAULT false,
    sender_name    text,                        -- taken from the text, not verified by the protocol
    text           text,                        -- removed after the retention period
    text_len       integer,
    msg_time       timestamptz,                 -- clock of the sender
    data_type      integer,                     -- GRP_DATA only
    ciphertext_len integer     NOT NULL,
    text_removed   boolean     NOT NULL DEFAULT false
);
CREATE INDEX channel_message_channel_hash_idx ON channel_message (channel_hash) WHERE NOT decrypted;
CREATE INDEX channel_message_sender_idx ON channel_message (sender_name);

-- REQ, RESPONSE, TXT_MSG, PATH: only one-byte hashes of both ends are visible.
CREATE TABLE direct_packet (
    packet_hash text PRIMARY KEY REFERENCES packet (hash) ON DELETE CASCADE,
    dst_hash    text NOT NULL,
    src_hash    text NOT NULL
);

CREATE TABLE anon_request (
    packet_hash   text PRIMARY KEY REFERENCES packet (hash) ON DELETE CASCADE,
    dst_hash      text NOT NULL,
    sender_pubkey text NOT NULL
);
CREATE INDEX anon_request_sender_idx ON anon_request (sender_pubkey);

-- A raw row is either in reception or here, never lost silently.
CREATE TABLE decode_error (
    raw_id          bigint   PRIMARY KEY REFERENCES raw_reception (id),
    error           text     NOT NULL,
    decoder_version smallint NOT NULL
);

CREATE TABLE worker_state (
    key   text  PRIMARY KEY,
    value jsonb NOT NULL
);

-- Collection health: one row per change of state (ok / down) of a check.
CREATE TABLE monitor_event (
    id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    at       timestamptz NOT NULL DEFAULT now(),
    name     text        NOT NULL,              -- observer_link, packets
    state    text        NOT NULL,              -- ok, down
    message  text        NOT NULL,
    notified boolean     NOT NULL DEFAULT false
);
