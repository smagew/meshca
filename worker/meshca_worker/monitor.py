"""Collection health. Two checks, each either "ok" or "down":

observer_link  the observer publishes a status message every five minutes; none for a while means it is not
               connected to our broker (it does not retry a broker that was down when it started).
packets        status messages arrive but no packet does: the radio side is silent.

A change of state is written to monitor_event and sent to what is configured: a Telegram chat
(ALERT_TELEGRAM_BOT_TOKEN, ALERT_TELEGRAM_CHAT_ID) and/or a webhook taking JSON (ALERT_WEBHOOK_URL).
Messages for the owner are in Russian, like the rest of the interface.
"""
from __future__ import annotations

import json
import logging
import urllib.request
from datetime import datetime, timedelta, timezone

import psycopg

from . import store

log = logging.getLogger("meshca.monitor")

TELEGRAM_API = "https://api.telegram.org"


def _minutes(delta: timedelta) -> int:
    return int(delta.total_seconds() // 60)


def evaluate(now: datetime, last_status: datetime | None, last_packet: datetime | None,
             status_gap: timedelta, packet_gap: timedelta,
             link_ok_since: datetime | None = None) -> dict[str, tuple[str, str]]:
    """Return {check: (state, message)}. A check with no data yet is left out: nothing to compare with.

    link_ok_since is when the link last came back: packet silence is counted from there, otherwise every
    recovery after a long outage would start with a false "no packets" alert."""
    result: dict[str, tuple[str, str]] = {}
    link_down = False
    if last_status is not None:
        age = now - last_status
        link_down = age > status_gap
        result["observer_link"] = (
            ("down", f"meshca: observer не присылает данные уже {_minutes(age)} мин. Скорее всего, он не подключён "
                     f"к брокеру meshca. Перезапустите сервис: sudo systemctl restart meshcore-packet-capture")
            if link_down else ("ok", "meshca: observer снова присылает данные."))
    if last_packet is not None and not link_down:
        age = now - max(last_packet, link_ok_since or last_packet)
        result["packets"] = (
            ("down", f"meshca: observer на связи, но пакетов нет уже {_minutes(age)} мин. Проверьте радиомодуль.")
            if age > packet_gap else ("ok", "meshca: пакеты снова поступают."))
    return result


def _post(url: str, body: dict, what: str) -> bool:
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return 200 <= response.status < 300
    except Exception as e:                      # any failure of the receiving side: retried on the next pass
        # The URL may carry a secret (a Telegram bot token), so only the kind of failure is logged.
        log.warning("alert delivery to %s failed: %s %s", what, type(e).__name__, getattr(e, "reason", ""))
        return False


def send(url: str, name: str, state: str, message: str) -> bool:
    """Generic webhook: a JSON POST any automation tool can take."""
    return _post(url, {"check": name, "state": state, "title": "meshca", "message": message}, "webhook")


def send_telegram(token: str, chat_id: str, name: str, state: str, message: str, api: str = TELEGRAM_API) -> bool:
    mark = "\U0001F534" if state == "down" else "\U0001F7E2"
    return _post(f"{api}/bot{token}/sendMessage", {"chat_id": chat_id, "text": f"{mark} {message}"}, "telegram")


def targets_from_env(env) -> list:
    """Delivery functions (name, state, message) -> bool for everything configured."""
    targets = []
    url = env.get("ALERT_WEBHOOK_URL", "").strip()
    token, chat = env.get("ALERT_TELEGRAM_BOT_TOKEN", "").strip(), env.get("ALERT_TELEGRAM_CHAT_ID", "").strip()
    if url:
        targets.append(lambda name, state, message: send(url, name, state, message))
    if token and chat:
        targets.append(lambda name, state, message: send_telegram(token, chat, name, state, message))
    return targets


def run(conn: psycopg.Connection, status_gap: timedelta, packet_gap: timedelta, targets: list,
        now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    with conn.transaction(), conn.cursor() as cur:
        last_status = cur.execute("SELECT max(ingested_at) FROM observer_message WHERE kind = 'status' AND NOT retained").fetchone()[0]
        last_packet = cur.execute("SELECT max(ingested_at) FROM raw_reception").fetchone()[0]
        states = store.get_state(cur, "monitor", {})
        since = states.get("observer_link_ok_since")
        results = evaluate(now, last_status, last_packet, status_gap, packet_gap,
                           datetime.fromisoformat(since) if since else None)
        if "observer_link" in results and results["observer_link"][0] == "ok" and states.get("observer_link") == "down":
            states["observer_link_ok_since"] = now.isoformat()
            results.pop("packets", None)            # judged from the next pass on
        for name, (state, message) in results.items():
            previous = states.get(name)
            states[name] = state
            # The first observation is only remembered, unless it is already a failure.
            if previous == state or (previous is None and state == "ok"):
                continue
            log.log(logging.WARNING if state == "down" else logging.INFO, "%s is %s", name, state)
            cur.execute("INSERT INTO monitor_event (at, name, state, message) VALUES (%s,%s,%s,%s)",
                        (now, name, state, message))
        store.set_state(cur, "monitor", states)
        pending = cur.execute("SELECT id, name, state, message FROM monitor_event "
                              "WHERE NOT notified AND at > %s ORDER BY id", (now - timedelta(hours=2),)).fetchall()

    # Delivery happens outside the transaction; what fails is retried on the next pass.
    if not targets:
        return
    for event_id, name, state, message in pending:
        # An event counts as delivered when at least one target took it.
        if not any([target(name, state, message) for target in targets]):
            break
        with conn.transaction():
            conn.execute("UPDATE monitor_event SET notified = true WHERE id = %s", (event_id,))
