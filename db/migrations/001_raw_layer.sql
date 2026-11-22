-- Raw layer: every MQTT message of the observer exactly as received. Append-only; everything else is derived from it.

CREATE TABLE raw_reception (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ingested_at  timestamptz NOT NULL,          -- collector clock at the moment the message arrived
    topic        text        NOT NULL,
    payload      jsonb,                         -- the message, when it is valid JSON
    payload_text text,                          -- the message as text, when it is not
    CONSTRAINT raw_reception_has_payload CHECK (payload IS NOT NULL OR payload_text IS NOT NULL)
);
CREATE INDEX raw_reception_ingested_at_idx ON raw_reception (ingested_at);

-- Everything that is not a packet: status, neighbours, anything new the observer starts to publish.
CREATE TABLE observer_message (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ingested_at  timestamptz NOT NULL,
    topic        text        NOT NULL,
    kind         text        NOT NULL,          -- last topic segment: status, neighbors, ...
    retained     boolean     NOT NULL DEFAULT false,
    payload      jsonb,
    payload_text text,
    CONSTRAINT observer_message_has_payload CHECK (payload IS NOT NULL OR payload_text IS NOT NULL)
);
CREATE INDEX observer_message_kind_ingested_at_idx ON observer_message (kind, ingested_at);
