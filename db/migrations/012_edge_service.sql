-- "Direct" packets split in two: private messages with their acknowledgements stay in `direct`; requests to repeaters
-- and rooms, their answers, anonymous requests and returned paths go to `service`. The topology job refills both.
ALTER TABLE edge_daily ADD COLUMN service integer NOT NULL DEFAULT 0;
COMMENT ON COLUMN edge_daily.direct IS 'private messages (TXT_MSG) and acknowledgements (ACK)';
COMMENT ON COLUMN edge_daily.service IS 'REQ, RESPONSE, ANON_REQ, PATH';
