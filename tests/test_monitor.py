import json
import sys
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "worker"))

from meshca_worker import monitor  # noqa: E402

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)
STATUS_GAP, PACKET_GAP = timedelta(minutes=15), timedelta(minutes=30)


def check(status_age_min, packet_age_min):
    ago = lambda m: None if m is None else NOW - timedelta(minutes=m)  # noqa: E731
    return {k: v[0] for k, v in monitor.evaluate(NOW, ago(status_age_min), ago(packet_age_min), STATUS_GAP, PACKET_GAP).items()}


def test_everything_arrives():
    assert check(3, 1) == {"observer_link": "ok", "packets": "ok"}


def test_observer_not_connected_hides_the_packet_check():
    # No status for 20 minutes: the link is the problem, a second alert about packets would only add noise.
    assert check(20, 20) == {"observer_link": "down"}
    assert check(20, 500) == {"observer_link": "down"}


def test_connected_but_radio_silent():
    assert check(3, 31) == {"observer_link": "ok", "packets": "down"}
    assert check(3, 29) == {"observer_link": "ok", "packets": "ok"}


def test_no_data_yet_means_no_verdict():
    assert check(None, None) == {}
    assert check(3, None) == {"observer_link": "ok"}


def test_down_message_tells_what_to_do():
    state, message = monitor.evaluate(NOW, NOW - timedelta(minutes=42), NOW, STATUS_GAP, PACKET_GAP)["observer_link"]
    assert state == "down" and "42 мин" in message and "systemctl restart meshcore-packet-capture" in message


def test_send_posts_json_and_reports_failure():
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(204 if self.path in ("/ok", "/botTOKEN/sendMessage") else 500)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        assert monitor.send(base + "/ok", "packets", "down", "текст")
        assert received == [{"check": "packets", "state": "down", "title": "meshca", "message": "текст"}]
        assert not monitor.send(base + "/fail", "packets", "ok", "x")
        assert monitor.send_telegram("TOKEN", "42", "packets", "down", "текст", api=base)
        assert not monitor.send_telegram("WRONG", "42", "packets", "down", "текст", api=base)
        assert received[-1]["chat_id"] == "42" and received[-1]["text"].endswith("текст")
    finally:
        server.shutdown()
    assert not monitor.send("http://127.0.0.1:1/none", "packets", "ok", "x")


def test_packet_silence_is_counted_from_link_recovery():
    recovered = NOW - timedelta(minutes=5)
    result = monitor.evaluate(NOW, NOW - timedelta(minutes=1), NOW - timedelta(hours=3), STATUS_GAP, PACKET_GAP, recovered)
    assert result["packets"][0] == "ok"
    late = monitor.evaluate(NOW, NOW - timedelta(minutes=1), NOW - timedelta(hours=3), STATUS_GAP, PACKET_GAP,
                            NOW - timedelta(minutes=31))
    assert late["packets"][0] == "down"


def test_targets_from_env():
    assert monitor.targets_from_env({}) == []
    assert len(monitor.targets_from_env({"ALERT_WEBHOOK_URL": "http://x/"})) == 1
    assert monitor.targets_from_env({"ALERT_TELEGRAM_BOT_TOKEN": "t"}) == [], "a token without a chat is not enough"
    assert len(monitor.targets_from_env({"ALERT_TELEGRAM_BOT_TOKEN": "t", "ALERT_TELEGRAM_CHAT_ID": "1",
                                         "ALERT_WEBHOOK_URL": "http://x/"})) == 2
