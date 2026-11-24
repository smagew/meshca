#!/usr/bin/env python3
"""Generate grafana/dashboards/*.json. The JSON files are committed; edit this script and run it, not the JSON.

    python3 grafana/build_dashboards.py
"""
from __future__ import annotations

import json
from pathlib import Path

DS = {"type": "grafana-postgresql-datasource", "uid": "meshca"}
OUT = Path(__file__).parent / "dashboards"
T = "$__timeFilter"
FLOOD = "route_type IN (0, 1)"


class Board:
    def __init__(self, uid: str, title: str, description: str) -> None:
        self.uid, self.title, self.description = uid, title, description
        self.panels: list[dict] = []
        self.x = self.y = self.row_h = 0

    def _place(self, w: int, h: int) -> dict:
        if self.x + w > 24:
            self.x, self.y, self.row_h = 0, self.y + self.row_h, 0
        pos = {"x": self.x, "y": self.y, "w": w, "h": h}
        self.x += w
        self.row_h = max(self.row_h, h)
        return pos

    def add(self, kind: str, title: str, sql: str, w: int, h: int, fmt: str = "table", description: str = "",
            defaults: dict | None = None, options: dict | None = None, overrides: list | None = None) -> None:
        self.panels.append({
            "id": len(self.panels) + 1, "type": kind, "title": title, "description": description,
            "datasource": DS, "gridPos": self._place(w, h),
            "targets": [{"refId": "A", "datasource": DS, "format": fmt, "rawQuery": True,
                         "rawSql": " ".join(sql.split())}],
            "fieldConfig": {"defaults": defaults or {}, "overrides": overrides or []},
            "options": options or {},
        })

    def stat(self, title: str, sql: str, unit: str = "none", description: str = "", thresholds: list | None = None) -> None:
        steps = thresholds or [{"color": "text", "value": None}]
        self.add("stat", title, sql, 4, 4, description=description,
                 defaults={"unit": unit, "thresholds": {"mode": "absolute", "steps": steps}},
                 options={"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                          "colorMode": "value", "graphMode": "none", "textMode": "value"})

    def bars_over_time(self, title: str, sql: str, w: int = 24, h: int = 8, description: str = "") -> None:
        self.add("timeseries", title, sql, w, h, fmt="time_series", description=description,
                 defaults={"custom": {"drawStyle": "bars", "fillOpacity": 80, "lineWidth": 0,
                                      "stacking": {"mode": "normal", "group": "A"}}, "min": 0},
                 options={"legend": {"displayMode": "table", "placement": "right", "calcs": ["sum"]},
                          "tooltip": {"mode": "multi", "sort": "desc"}})

    def lines(self, title: str, sql: str, unit: str, w: int = 12, h: int = 8, description: str = "") -> None:
        self.add("timeseries", title, sql, w, h, fmt="time_series", description=description,
                 defaults={"unit": unit, "custom": {"drawStyle": "line", "lineWidth": 1, "showPoints": "auto",
                                                    "spanNulls": False}},
                 options={"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi"}})

    def table(self, title: str, sql: str, w: int = 24, h: int = 10, description: str = "") -> None:
        self.add("table", title, sql, w, h, description=description,
                 defaults={"custom": {"filterable": True}}, options={"cellHeight": "sm", "showHeader": True})

    def bars(self, title: str, sql: str, w: int = 12, h: int = 8, description: str = "") -> None:
        self.add("barchart", title, sql, w, h, description=description, defaults={"min": 0},
                 options={"legend": {"showLegend": False}, "xTickLabelRotation": 0, "showValue": "auto"})

    def pie(self, title: str, sql: str, w: int = 8, h: int = 8, description: str = "") -> None:
        self.add("piechart", title, sql, w, h, description=description,
                 options={"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": True},
                          "pieType": "donut", "legend": {"displayMode": "table", "placement": "right",
                                                         "values": ["value", "percent"]}})

    def save(self) -> None:
        links = [{"type": "link", "title": t, "url": f"/d/{u}", "keepTime": True, "icon": "dashboard"}
                 for u, t in BOARDS if u != self.uid]
        doc = {"uid": self.uid, "title": self.title, "description": self.description, "tags": ["meshca"],
               "timezone": "browser", "editable": False, "graphTooltip": 1, "schemaVersion": 39, "version": 1,
               "refresh": "1m", "time": {"from": "now-24h", "to": "now"}, "links": links, "panels": self.panels}
        (OUT / f"{self.uid.removeprefix('meshca-')}.json").write_text(
            json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


BOARDS = [("meshca-overview", "meshca · Обзор"), ("meshca-nodes", "meshca · Узлы"),
          ("meshca-relays", "meshca · Ретрансляторы"), ("meshca-channels", "meshca · Каналы")]

ONE_POINT = "Всё, что здесь показано, видно с одной точки: это пакеты, которые услышал наш observer."


def overview() -> Board:
    b = Board(*BOARDS[0], ONE_POINT)
    b.stat("Приёмов", f"SELECT count(*) FROM reception WHERE {T}(received_at)",
           description="Сколько раз observer принял пакет. Один пакет обычно приходит в нескольких копиях.")
    b.stat("Уникальных пакетов", f"SELECT count(DISTINCT packet_hash) FROM reception WHERE {T}(received_at)")
    b.stat("Узлов объявилось",
           f"SELECT count(DISTINCT a.pubkey) FROM advert a JOIN packet p ON p.hash = a.packet_hash "
           f"WHERE a.signature_ok AND {T}(p.first_seen)",
           description="Узлы, чьё объявление с верной подписью принято за выбранный период.")
    b.stat("Узлов в справочнике", "SELECT count(*) FROM node", description="За всё время сбора.")
    b.stat("Последний пакет", "SELECT extract(epoch FROM now() - max(ingested_at)) FROM raw_reception", unit="s",
           description="Сколько прошло с последнего принятого пакета. Больше 30 минут — повод для оповещения.",
           thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 900}, {"color": "red", "value": 1800}])
    b.stat("Ошибок разбора", "SELECT count(*) FROM decode_error",
           description="Сырые записи, которые не удалось разобрать. Причины — в таблице decode_error.",
           thresholds=[{"color": "green", "value": None}, {"color": "red", "value": 1}])
    b.bars_over_time("Приёмы по типам пакетов", f"""
        SELECT $__timeGroupAlias(r.received_at, $__interval), payload_type_name(p.payload_type) AS metric, count(*) AS value
        FROM reception r JOIN packet p ON p.hash = r.packet_hash WHERE {T}(r.received_at) GROUP BY 1, 2 ORDER BY 1""")
    b.pie("Уникальные пакеты по типам", f"""
        SELECT payload_type_name(payload_type) AS metric, count(*) AS value FROM packet
        WHERE {T}(first_seen) GROUP BY 1 ORDER BY 2 DESC""")
    b.bars("Сколько копий пакета мы слышим", f"""
        SELECT copies::text AS "копий", count(*) AS "пакетов"
        FROM (SELECT count(*) AS copies FROM reception WHERE {T}(received_at) GROUP BY packet_hash) x
        GROUP BY copies ORDER BY copies""", w=8,
        description="Каждая копия пришла от другого соседнего ретранслятора или повторно.")
    b.pie("Тип маршрута", f"""
        SELECT route_type_name(route_type) AS metric, count(*) AS value FROM reception
        WHERE {T}(received_at) GROUP BY 1 ORDER BY 2 DESC""")
    b.lines("Уровень сигнала соседей (RSSI)", f"""
        SELECT $__timeGroupAlias(received_at, $__interval), coalesce(last_hop, 'напрямую') AS metric, avg(rssi) AS value
        FROM reception WHERE {T}(received_at) AND {FLOOD} GROUP BY 1, 2 ORDER BY 1""", "dBm",
        description="По последнему узлу пути: это ретранслятор, которого observer слышит сам.")
    b.lines("Отношение сигнал/шум соседей (SNR)", f"""
        SELECT $__timeGroupAlias(received_at, $__interval), coalesce(last_hop, 'напрямую') AS metric, avg(snr) AS value
        FROM reception WHERE {T}(received_at) AND {FLOOD} GROUP BY 1, 2 ORDER BY 1""", "dB")
    b.table("События контроля сбора", """
        SELECT at AS "время", CASE name WHEN 'observer_link' THEN 'связь с observer''ом' ELSE 'пакеты' END AS "проверка",
               CASE state WHEN 'ok' THEN 'норма' ELSE 'сбой' END AS "состояние", message AS "сообщение",
               notified AS "отправлено"
        FROM monitor_event ORDER BY id DESC LIMIT 50""", h=7)
    return b


def nodes() -> Board:
    b = Board(*BOARDS[1], ONE_POINT + " Отправитель известен точно только у объявлений, анонимных запросов и "
              "расшифрованных сообщений каналов; имя в сообщении канала протоколом не проверяется.")
    b.table("Кто сколько отправил", f"""
        SELECT s.sender AS "отправитель",
               count(*) FILTER (WHERE s.kind = 'объявление') AS "объявления",
               count(*) FILTER (WHERE s.kind = 'сообщение канала') AS "сообщения каналов",
               count(*) FILTER (WHERE s.kind = 'анонимный запрос') AS "анонимные запросы",
               count(*) AS "всего",
               round(extract(epoch FROM max(p.first_seen) - min(p.first_seen)) / 60 / nullif(count(*) - 1, 0)) AS "средний интервал, мин",
               min(p.first_seen) AS "первый", max(p.first_seen) AS "последний"
        FROM v_sender s JOIN packet p ON p.hash = s.packet_hash WHERE {T}(p.first_seen)
        GROUP BY 1 ORDER BY "всего" DESC LIMIT 300""", h=12,
        description="Уникальные пакеты за выбранный период. Личные сообщения сюда не входят: у них виден только "
                    "однобайтовый хеш отправителя (таблица ниже).")
    b.bars_over_time("Пакеты самых активных отправителей", f"""
        SELECT $__timeGroupAlias(p.first_seen, $__interval), s.sender AS metric, count(*) AS value
        FROM v_sender s JOIN packet p ON p.hash = s.packet_hash
        WHERE {T}(p.first_seen) AND s.sender IN (
            SELECT s2.sender FROM v_sender s2 JOIN packet p2 ON p2.hash = s2.packet_hash
            WHERE {T}(p2.first_seen) GROUP BY 1 ORDER BY count(*) DESC LIMIT 10)
        GROUP BY 1, 2 ORDER BY 1""")
    b.bars("Активность по часам суток", f"""
        SELECT lpad(extract(hour FROM first_seen)::text, 2, '0') AS "час", count(*) AS "пакетов"
        FROM packet WHERE {T}(first_seen) GROUP BY 1 ORDER BY 1""",
        description="Все уникальные пакеты. Часовой пояс задаётся настройкой MESHCA_TIMEZONE.")
    b.bars("Объявления по ролям узлов", f"""
        SELECT node_role_name(a.role) AS "роль", count(DISTINCT a.pubkey) AS "узлов"
        FROM advert a JOIN packet p ON p.hash = a.packet_hash WHERE a.signature_ok AND {T}(p.first_seen)
        GROUP BY 1 ORDER BY 2 DESC""")
    b.table("Справочник узлов", """
        SELECT name AS "имя", node_role_name(role) AS "роль", lat AS "широта", lon AS "долгота",
               advert_count AS "объявлений", first_seen AS "впервые", last_seen AS "последний раз",
               left(pubkey, 12) AS "ключ"
        FROM node ORDER BY last_seen DESC LIMIT 1000""", h=12,
        description="За всё время сбора, только по объявлениям с верной подписью.")
    b.table("Личные пакеты по хешу отправителя", f"""
        SELECT d.src_hash AS "хеш отправителя", payload_type_name(p.payload_type) AS "тип", count(*) AS "пакетов",
               (SELECT count(*) FROM node n WHERE left(n.pubkey, 2) = d.src_hash) AS "узлов с таким хешем",
               (SELECT string_agg(n.name, ', ' ORDER BY n.name) FROM node n WHERE left(n.pubkey, 2) = d.src_hash) AS "кто это может быть"
        FROM direct_packet d JOIN packet p ON p.hash = d.packet_hash WHERE {T}(p.first_seen)
        GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 200""", w=14,
        description="REQ, RESPONSE, TXT_MSG, PATH: виден только первый байт ключа отправителя, поэтому кандидатов "
                    "может быть несколько.")
    b.table("Изменения узлов", """
        SELECT h.changed_at AS "когда", n.name AS "узел",
               CASE h.field WHEN 'name' THEN 'имя' WHEN 'role' THEN 'роль' ELSE 'координаты' END AS "что",
               h.old AS "было", h.new AS "стало"
        FROM node_history h JOIN node n ON n.pubkey = h.pubkey ORDER BY h.changed_at DESC LIMIT 200""", w=10)
    return b


def relays() -> Board:
    b = Board(*BOARDS[2], ONE_POINT + " Узел за префиксом назван, только если в справочнике ровно один ретранслятор "
              "с таким началом ключа; иначе показано число кандидатов.")
    flood_packets = (f"(SELECT count(DISTINCT packet_hash) FROM reception "
                     f"WHERE {FLOOD} AND hop_count > 0 AND {T}(received_at))")
    b.table("Какие узлы чаще всего пересылают пакеты", f"""
        SELECT coalesce(pn.name, '—') AS "узел", h.prefix AS "префикс", pn.candidates AS "кандидатов",
               count(DISTINCT h.packet_hash) AS "пакетов",
               round(100.0 * count(DISTINCT h.packet_hash) / nullif({flood_packets}, 0), 1) AS "доля, %",
               count(DISTINCT h.packet_hash) FILTER (WHERE h.position = 1) AS "первым в пути",
               round(avg(h.from_end), 1) AS "шагов до нас"
        FROM v_hop h LEFT JOIN v_prefix_node pn ON pn.prefix = h.prefix WHERE {T}(h.received_at)
        GROUP BY 1, 2, 3 ORDER BY "пакетов" DESC LIMIT 200""", h=12,
        description="Доля — от всех пакетов, пришедших через ретрансляторы. «Первым в пути» — узел стоял сразу после "
                    "отправителя. «Шагов до нас»: 0 — прямой сосед observer'а. Один узел может встречаться дважды: "
                    "с однобайтовым и двухбайтовым префиксом.")
    b.bars_over_time("Пакеты через главные ретрансляторы", f"""
        SELECT $__timeGroupAlias(h.received_at, $__interval), coalesce(pn.name, h.prefix) AS metric,
               count(DISTINCT h.packet_hash) AS value
        FROM v_hop h LEFT JOIN v_prefix_node pn ON pn.prefix = h.prefix
        WHERE {T}(h.received_at) AND h.prefix IN (
            SELECT prefix FROM v_hop WHERE {T}(received_at) GROUP BY 1 ORDER BY count(DISTINCT packet_hash) DESC LIMIT 8)
        GROUP BY 1, 2 ORDER BY 1""")
    b.table("Прямые соседи: кого слышит observer", f"""
        SELECT coalesce(pn.name, '—') AS "узел", coalesce(r.last_hop, 'сам отправитель') AS "префикс",
               count(*) AS "приёмов", count(DISTINCT r.packet_hash) AS "пакетов",
               round(avg(r.rssi)) AS "RSSI, дБм", round(avg(r.snr)::numeric, 1) AS "SNR, дБ"
        FROM reception r LEFT JOIN v_prefix_node pn ON pn.prefix = r.last_hop
        WHERE {T}(r.received_at) AND r.{FLOOD} GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 50""", w=14, h=8)
    b.bars("Длина пути", f"""
        SELECT hop_count::text AS "ретрансляторов в пути", count(*) AS "приёмов" FROM reception
        WHERE {T}(received_at) AND {FLOOD} GROUP BY hop_count ORDER BY hop_count""", w=10)
    b.pie("Размер префикса в пути", f"""
        SELECT prefix_size || ' байт' AS metric, count(*) AS value FROM reception
        WHERE {T}(received_at) AND hop_count > 0 GROUP BY 1 ORDER BY 1""",
        description="Чем длиннее префикс, тем увереннее определяется узел.")
    b.table("Самые частые пути", f"""
        SELECT array_to_string(path, ' → ') AS "путь", hop_count AS "длина", count(DISTINCT packet_hash) AS "пакетов"
        FROM reception WHERE {T}(received_at) AND {FLOOD} AND hop_count > 0
        GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 100""", w=16, h=8,
        description="Слева — ближе к отправителю, справа — наш прямой сосед.")
    return b


def channels() -> Board:
    name = "coalesce(m.channel, 'неизвестный ' || m.channel_hash)"
    b = Board(*BOARDS[3], ONE_POINT + " Расшифровываются Public и хештег-каналы с известным именем. Тексты хранятся "
              "ограниченное время, потом остаются отправитель, время, канал и длина.")
    b.bars_over_time("Сообщения по каналам", f"""
        SELECT $__timeGroupAlias(p.first_seen, $__interval), {name} AS metric, count(*) AS value
        FROM channel_message m JOIN packet p ON p.hash = m.packet_hash WHERE {T}(p.first_seen)
        GROUP BY 1, 2 ORDER BY 1""")
    b.table("Каналы", f"""
        SELECT {name} AS "канал", CASE m.payload_type WHEN 5 THEN 'текст' ELSE 'данные' END AS "вид",
               m.decrypted AS "расшифрован", count(*) AS "сообщений", count(DISTINCT m.sender_name) AS "отправителей",
               round(avg(m.text_len)) AS "средняя длина", max(p.first_seen) AS "последнее"
        FROM channel_message m JOIN packet p ON p.hash = m.packet_hash WHERE {T}(p.first_seen)
        GROUP BY 1, 2, 3 ORDER BY 4 DESC""", w=14, h=9)
    b.table("Кто пишет в каналы", f"""
        SELECT m.sender_name AS "отправитель", m.channel AS "канал", count(*) AS "сообщений"
        FROM channel_message m JOIN packet p ON p.hash = m.packet_hash
        WHERE {T}(p.first_seen) AND m.decrypted AND m.sender_name IS NOT NULL
        GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 200""", w=10, h=9)
    b.table("Последние сообщения", f"""
        SELECT p.first_seen AS "время", m.channel AS "канал", m.sender_name AS "отправитель",
               CASE WHEN m.text_removed THEN '(текст удалён по сроку хранения)' ELSE m.text END AS "текст",
               p.reception_count AS "копий"
        FROM channel_message m JOIN packet p ON p.hash = m.packet_hash
        WHERE {T}(p.first_seen) AND m.decrypted AND m.payload_type = 5 ORDER BY p.first_seen DESC LIMIT 300""", h=12)
    b.table("Метки регионов (scope)", f"""
        SELECT r.transport_codes AS "коды транспорта", count(*) AS "приёмов", count(DISTINCT r.packet_hash) AS "пакетов",
               string_agg(DISTINCT payload_type_name(p.payload_type), ', ') AS "типы"
        FROM reception r JOIN packet p ON p.hash = r.packet_hash
        WHERE {T}(r.received_at) AND r.transport_codes IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 50""", w=14, h=8,
        description="Пакеты, отправленные с ограничением по региону сети. Какому региону соответствует код, пока "
                    "не определяется.")
    b.table("Известные каналы", """
        SELECT name AS "канал", hash AS "хеш",
               CASE source WHEN 'builtin' THEN 'встроенный' WHEN 'config' THEN 'из настроек' ELSE 'из упоминания' END AS "откуда",
               added_at AS "добавлен"
        FROM channel ORDER BY added_at DESC, name LIMIT 500""", w=10, h=8)
    return b


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for build in (overview, nodes, relays, channels):
        build().save()
    print("written:", ", ".join(sorted(p.name for p in OUT.glob("*.json"))))
