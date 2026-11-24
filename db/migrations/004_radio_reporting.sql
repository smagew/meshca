-- Radio reporting: LoRa time on air and the counters the observer's radio publishes in its status message.

-- Time on air of one LoRa packet in milliseconds (Semtech formula: explicit header, CRC on).
-- cr is the denominator of the coding rate, 5..8. Preamble as MeshCore sets it: 32 symbols up to SF8, 16 above.
CREATE FUNCTION lora_airtime_ms(len integer, sf integer, bw_khz numeric, cr integer) RETURNS numeric
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT round((
        (CASE WHEN sf <= 8 THEN 32 ELSE 16 END) + 4.25 + 8
        + greatest(ceil((8.0 * len - 4 * sf + 28 + 16)
                        / (4.0 * (sf - CASE WHEN power(2::numeric, sf) / bw_khz > 16 THEN 2 ELSE 0 END))) * cr, 0)
    ) * power(2::numeric, sf) / bw_khz, 1)
$$;

-- Radio settings of the observer, from its latest status message: "frequency MHz,bandwidth kHz,SF,CR".
CREATE VIEW v_radio AS
SELECT split_part(radio, ',', 1)::numeric AS freq_mhz, split_part(radio, ',', 2)::numeric AS bw_khz,
       split_part(radio, ',', 3)::integer AS sf, split_part(radio, ',', 4)::integer AS cr
FROM (SELECT payload->>'radio' AS radio FROM observer_message
      WHERE kind = 'status' AND payload->>'radio' ~ '^[0-9.]+,[0-9.]+,[0-9]+,[0-9]+$'
      ORDER BY id DESC LIMIT 1) latest;

-- Counters of the observer's radio. They grow from its start, so use differences between neighbouring rows.
CREATE VIEW v_observer_stats AS
SELECT ingested_at,
       (payload->'stats'->>'noise_floor')::numeric  AS noise_floor,
       (payload->'stats'->>'battery_mv')::numeric   AS battery_mv,
       (payload->'stats'->>'recv')::numeric         AS recv,
       (payload->'stats'->>'recv_errors')::numeric  AS recv_errors,
       (payload->'stats'->>'rx_air_secs')::numeric  AS rx_air_secs,
       (payload->'stats'->>'tx_air_secs')::numeric  AS tx_air_secs,
       (payload->'stats'->>'uptime_secs')::numeric  AS uptime_secs
FROM observer_message
WHERE kind = 'status' AND NOT retained AND jsonb_typeof(payload->'stats') = 'object';

-- The same with differences to the previous status message; rows after a restart of the radio are left out.
CREATE VIEW v_observer_stats_delta AS
SELECT * FROM (
    SELECT ingested_at, noise_floor, battery_mv,
           uptime_secs - lag(uptime_secs) OVER w  AS d_uptime,
           rx_air_secs - lag(rx_air_secs) OVER w  AS d_rx_air,
           tx_air_secs - lag(tx_air_secs) OVER w  AS d_tx_air,
           recv - lag(recv) OVER w                AS d_recv,
           recv_errors - lag(recv_errors) OVER w  AS d_recv_errors
    FROM v_observer_stats WINDOW w AS (ORDER BY ingested_at)
) d WHERE d_uptime > 0 AND d_rx_air >= 0 AND d_recv >= 0 AND d_recv_errors >= 0;
