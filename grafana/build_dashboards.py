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
    def __init__(self, uid: str, title: str, description: str, time_from: str = "now-24h") -> None:
        self.uid, self.title, self.description, self.time_from = uid, title, description, time_from
        self.variables: list[dict] = []
        self.panels: list[dict] = []
        self.x = self.y = self.row_h = 0

    def _place(self, w: int, h: int) -> dict:
        if self.x + w > 24:
            self.x, self.y, self.row_h = 0, self.y + self.row_h, 0
        pos = {"x": self.x, "y": self.y, "w": w, "h": h}
        self.x += w
        self.row_h = max(self.row_h, h)
        return pos

    def variable(self, name: str, label: str, query: str, multi: bool = True) -> None:
        """A filter at the top of the dashboard. The query returns __text and __value; "all" is the default."""
        self.variables.append({
            "name": name, "label": label, "type": "query", "datasource": DS, "query": " ".join(query.split()),
            "refresh": 2, "sort": 0, "multi": multi, "includeAll": True,
            "current": {"selected": True, "text": ["All"] if multi else "All", "value": ["$__all"] if multi else "$__all"},
        })

    def add(self, kind: str, title: str, sql: str | list[str], w: int, h: int, fmt: str = "table",
            description: str = "", defaults: dict | None = None, options: dict | None = None,
            overrides: list | None = None) -> None:
        queries = [sql] if isinstance(sql, str) else sql
        self.panels.append({
            "id": len(self.panels) + 1, "type": kind, "title": title, "description": description,
            "datasource": DS, "gridPos": self._place(w, h),
            "targets": [{"refId": "ABCDEF"[i], "datasource": DS, "format": fmt, "rawQuery": True,
                         "rawSql": " ".join(q.split())} for i, q in enumerate(queries)],
            "fieldConfig": {"defaults": defaults or {}, "overrides": overrides or []},
            "options": options or {},
        })

    def stat(self, title: str, sql: str, unit: str = "none", description: str = "", thresholds: list | None = None,
             text: bool = False) -> None:
        steps = thresholds or [{"color": "text", "value": None}]
        self.add("stat", title, sql, 4, 4, description=description,
                 defaults={"unit": unit, "thresholds": {"mode": "absolute", "steps": steps}},
                 options={"reduceOptions": {"calcs": ["lastNotNull"], "fields": "/.*/" if text else "", "values": False},
                          "colorMode": "value", "graphMode": "none", "textMode": "value"})

    def bars_over_time(self, title: str, sql: str, w: int = 24, h: int = 8, description: str = "") -> None:
        self.add("timeseries", title, sql, w, h, fmt="time_series", description=description,
                 defaults={"custom": {"drawStyle": "bars", "fillOpacity": 80, "lineWidth": 0,
                                      "stacking": {"mode": "normal", "group": "A"}}, "min": 0},
                 options={"legend": {"displayMode": "table", "placement": "right", "calcs": ["sum"]},
                          "tooltip": {"mode": "multi", "sort": "desc"}})

    def lines(self, title: str, sql: str | list[str], unit: str, w: int = 12, h: int = 8, description: str = "") -> None:
        self.add("timeseries", title, sql, w, h, fmt="time_series", description=description,
                 defaults={"unit": unit, "custom": {"drawStyle": "line", "lineWidth": 1, "showPoints": "auto",
                                                    "spanNulls": False}},
                 options={"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi"}})

    def table(self, title: str, sql: str, w: int = 24, h: int = 10, description: str = "") -> None:
        self.add("table", title, sql, w, h, description=description,
                 defaults={"custom": {"filterable": True}}, options={"cellHeight": "sm", "showHeader": True})

    def heat_table(self, title: str, sql: str, label_column: str, w: int = 24, h: int = 9, description: str = "") -> None:
        """A table whose numeric cells are shaded by value: one scale for the whole table."""
        self.add("table", title, sql, w, h, description=description,
                 defaults={"min": 0, "color": {"mode": "thresholds"},
                           "thresholds": {"mode": "percentage", "steps": [
                               {"color": "transparent", "value": None}, {"color": "#1f4e79", "value": 0.0001},
                               {"color": "#2a7ab9", "value": 20}, {"color": "#7b5ea7", "value": 40},
                               {"color": "#c0508a", "value": 60}, {"color": "#e0752d", "value": 80}]},
                           "custom": {"align": "center", "width": 52, "cellOptions": {"type": "color-background", "mode": "basic"}}},
                 overrides=[{"matcher": {"id": "byName", "options": label_column},
                             "properties": [{"id": "custom.cellOptions", "value": {"type": "auto"}},
                                            {"id": "custom.align", "value": "left"},
                                            {"id": "custom.width", "value": 90}]}],
                 options={"cellHeight": "sm", "showHeader": True})

    def geomap(self, title: str, layers: list[tuple[str, str, str]], w: int = 24, h: int = 18,
               description: str = "") -> None:
        """One marker layer per (name, colour, query): the layer list of the map then works as the legend."""
        self.add("geomap", title, [sql for _, _, sql in layers], w, h, description=description,
                 options={"view": {"id": "fit", "allLayers": True, "padding": 40, "maxZoom": 13},
                          "basemap": {"type": "default", "name": "Карта"},
                          "controls": {"showZoom": True, "mouseWheelZoom": True, "showScale": True,
                                       "showLayercontrol": True},
                          "tooltip": {"mode": "details"},
                          "layers": [{"type": "markers", "name": name, "tooltip": True,
                                      "filterData": {"id": "byRefId", "options": "ABCDEF"[i]},
                                      "location": {"mode": "coords", "latitude": "lat", "longitude": "lon"},
                                      "config": {"showLegend": False, "style": {
                                          "color": {"fixed": color}, "opacity": 0.85, "size": {"fixed": 7},
                                          "symbol": {"mode": "fixed", "fixed": "img/icons/marker/circle.svg"},
                                          "text": {"mode": "field", "field": "имя", "fixed": ""},
                                          "textConfig": {"fontSize": 11, "offsetX": 0, "offsetY": -12,
                                                         "textAlign": "center", "textBaseline": "bottom"}}}}
                                     for i, (name, color, _) in enumerate(layers)]})

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
               "refresh": "1m", "time": {"from": self.time_from, "to": "now"}, "links": links,
               "templating": {"list": self.variables}, "panels": self.panels}
        (OUT / f"{self.uid.removeprefix('meshca-')}.json").write_text(
            json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


BOARDS = [("meshca-overview", "meshca · Обзор"), ("meshca-nodes", "meshca · Узлы"),
          ("meshca-relays", "meshca · Ретрансляторы"), ("meshca-channels", "meshca · Каналы"),
          ("meshca-radio", "meshca · Радио"), ("meshca-rhythm", "meshca · Ритм"), ("meshca-map", "meshca · Карта")]

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
    b.variable("role", "Роль", """
        SELECT * FROM (VALUES ('клиент', 1), ('ретранслятор', 2), ('сервер комнат', 3), ('датчик', 4),
                              ('неизвестно', 0)) AS v(__text, __value)""")
    b.variable("kind", "Вид пакета", """
        SELECT k AS __text, k AS __value FROM (VALUES ('объявление'), ('сообщение канала'), ('анонимный запрос')) AS v(k)""")
    b.table("Кто сколько отправил", f"""
        SELECT s.sender AS "отправитель",
               count(*) FILTER (WHERE s.kind = 'объявление') AS "объявления",
               count(*) FILTER (WHERE s.kind = 'сообщение канала') AS "сообщения каналов",
               count(*) FILTER (WHERE s.kind = 'анонимный запрос') AS "анонимные запросы",
               count(*) AS "всего",
               round(extract(epoch FROM max(p.first_seen) - min(p.first_seen)) / 60 / nullif(count(*) - 1, 0)) AS "средний интервал, мин",
               min(p.first_seen) AS "первый", max(p.first_seen) AS "последний"
        FROM v_sender s JOIN packet p ON p.hash = s.packet_hash LEFT JOIN node n ON n.pubkey = s.pubkey
        WHERE {T}(p.first_seen) AND coalesce(n.role, 0) IN (${{role:csv}}) AND s.kind IN (${{kind:sqlstring}})
        GROUP BY 1 ORDER BY "всего" DESC LIMIT 300""", h=12,
        description="Уникальные пакеты за выбранный период, с учётом фильтров вверху. Роль известна только у узлов "
                    "из справочника: у отправителя сообщения канала видно одно имя, его роль «неизвестно». "
                    "Личные сообщения сюда не входят: у них виден только однобайтовый хеш отправителя (таблица ниже).")
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
        FROM node WHERE coalesce(role, 0) IN (${role:csv}) ORDER BY last_seen DESC LIMIT 1000""", h=12,
        description="За всё время сбора, только по объявлениям с верной подписью.")
    last_run = "(SELECT * FROM origin_run ORDER BY id DESC LIMIT 1) r"
    b.table("Кто отправляет личные пакеты (оценка)", f"""
        SELECT o.label AS "отправитель", min(o.status) AS "определение",
               count(*) FILTER (WHERE p.payload_type = 2) AS "сообщения", count(*) FILTER (WHERE p.payload_type = 0) AS "запросы",
               count(*) FILTER (WHERE p.payload_type = 1) AS "ответы", count(*) FILTER (WHERE p.payload_type = 8) AS "пути",
               count(*) AS "всего", round(avg(o.probability)::numeric, 2) AS "вероятность"
        FROM v_origin_resolved o JOIN packet p ON p.hash = o.packet_hash WHERE {T}(p.first_seen)
        GROUP BY 1 ORDER BY "всего" DESC LIMIT 300""", w=16, h=10,
        description="У личных пакетов (TXT_MSG, REQ, RESPONSE, PATH) виден только первый байт ключа отправителя. Узел "
                    "определяется по «отпечатку пути»: через какие ретрансляторы обычно входят в сеть его объявления. "
                    "Имя показано при вероятности от 0,7. Строки с «?» — байт отправителя, узел не определён. "
                    "«Нет пути от отправителя» — пакет шёл прямым маршрутом, по нему отправителя не найти.")
    b.stat("Определено отправителей", f"SELECT 100 * resolved_share FROM {last_run}", unit="percent",
           description="Доля личных пакетов, у которых отправитель назван с вероятностью от 0,7.",
           thresholds=[{"color": "red", "value": None}, {"color": "orange", "value": 40}, {"color": "green", "value": 70}])
    b.stat("Точность на проверке", f"SELECT 100 * (evaluation->>'accuracy')::numeric FROM {last_run}", unit="percent",
           description="Проверка: у части пакетов с точно известным отправителем (объявления, анонимные запросы) он "
                       "скрывается, остаётся один байт, и метод должен найти его заново. Учитываются только случаи, "
                       "когда под байт подходят два и более известных узла.",
           thresholds=[{"color": "red", "value": None}, {"color": "orange", "value": 70}, {"color": "green", "value": 90}])
    b.stat("Точность уверенных ответов", f"SELECT 100 * (evaluation->>'confident_accuracy')::numeric FROM {last_run}",
           unit="percent", description="То же среди ответов с вероятностью от 0,7: именно они показаны именами.",
           thresholds=[{"color": "red", "value": None}, {"color": "orange", "value": 80}, {"color": "green", "value": 95}])
    b.stat("Случаев в проверке", f"SELECT (evaluation->>'packets')::int FROM {last_run}",
           description="При малом числе цифрам точности верить рано.")
    b.table("Изменения узлов", """
        SELECT h.changed_at AS "когда", n.name AS "узел",
               CASE h.field WHEN 'name' THEN 'имя' WHEN 'role' THEN 'роль' ELSE 'координаты' END AS "что",
               h.old AS "было", h.new AS "стало"
        FROM node_history h JOIN node n ON n.pubkey = h.pubkey ORDER BY h.changed_at DESC LIMIT 200""", w=24, h=8)
    return b


def relays() -> Board:
    b = Board(*BOARDS[2], ONE_POINT + " Узел за префиксом определяется расчётом: по тому, сколько трафика несёт кандидат, "
              "кто стоит рядом с ним в путях и правдоподобно ли расстояние между ними. Имя показано при вероятности "
              "от 0,7; иначе остаётся префикс со знаком вопроса. Расчёт идёт раз в час.")
    flood_packets = (f"(SELECT count(DISTINCT packet_hash) FROM reception "
                     f"WHERE {FLOOD} AND hop_count > 0 AND {T}(received_at))")
    last_run = "(SELECT * FROM resolution_run ORDER BY id DESC LIMIT 1) r"
    b.stat("Шагов пути с определённым узлом", f"SELECT 100 * resolved_share FROM {last_run}", unit="percent",
           description="Доля шагов в путях (с учётом числа пакетов), где узел назван с вероятностью от 0,7.",
           thresholds=[{"color": "red", "value": None}, {"color": "orange", "value": 50}, {"color": "green", "value": 80}])
    b.stat("Точность на проверке", f"SELECT 100 * (evaluation->>'accuracy')::numeric FROM {last_run}", unit="percent",
           description="Проверка: у части путей с двухбайтовыми префиксами (где узел известен точно) префиксы обрезаются "
                       "до одного байта, и метод должен найти узел заново. Это доля верных ответов в трудных случаях, "
                       "когда под однобайтовый префикс подходит несколько известных узлов. Оценка занижена: обрезка "
                       "убирает часть сведений, которые у настоящих коротких путей есть.",
           thresholds=[{"color": "red", "value": None}, {"color": "orange", "value": 70}, {"color": "green", "value": 90}])
    b.stat("Точность уверенных ответов", f"SELECT 100 * (evaluation->>'confident_accuracy')::numeric FROM {last_run}",
           unit="percent", description="То же, но только среди ответов с вероятностью от 0,7: именно они показаны именами.",
           thresholds=[{"color": "red", "value": None}, {"color": "orange", "value": 80}, {"color": "green", "value": 95}])
    b.stat("Простое правило", f"SELECT 100 * (evaluation->>'baseline_accuracy')::numeric FROM {last_run}", unit="percent",
           description="Для сравнения: точность правила «взять самого загруженного кандидата», без соседей и расстояний.")
    b.stat("Случаев в проверке", f"SELECT (evaluation->>'hops')::int FROM {last_run}",
           description="На скольких скрытых шагах измерена точность. При малом числе цифрам выше верить рано.")
    b.stat("Последний расчёт", f"SELECT extract(epoch FROM now() - computed_at) FROM {last_run}", unit="s")
    b.table("Какие узлы чаще всего пересылают пакеты", f"""
        SELECT h.label AS "узел", min(h.status) AS "определение", string_agg(DISTINCT h.prefix, ' ' ORDER BY h.prefix) AS "префиксы",
               count(DISTINCT h.packet_hash) AS "пакетов",
               round(100.0 * count(DISTINCT h.packet_hash) / nullif({flood_packets}, 0), 1) AS "доля, %",
               count(DISTINCT h.packet_hash) FILTER (WHERE h.position = 1) AS "первым в пути",
               round(avg(h.from_end), 1) AS "шагов до нас", round(avg(h.probability)::numeric, 2) AS "вероятность"
        FROM v_hop_resolved h WHERE {T}(h.received_at)
        GROUP BY 1 ORDER BY "пакетов" DESC LIMIT 300""", h=12,
        description="Доля — от всех пакетов, пришедших через ретрансляторы. «Первым в пути» — узел стоял сразу после "
                    "отправителя. «Шагов до нас»: 0 — прямой сосед observer'а. Строки с «?» — префиксы, за которыми "
                    "узел не определён или его нет в справочнике.")
    b.bars_over_time("Пакеты через главные ретрансляторы", f"""
        SELECT $__timeGroupAlias(h.received_at, $__interval), h.label AS metric, count(DISTINCT h.packet_hash) AS value
        FROM v_hop_resolved h
        WHERE {T}(h.received_at) AND h.label IN (
            SELECT label FROM v_hop_resolved WHERE {T}(received_at) GROUP BY 1 ORDER BY count(DISTINCT packet_hash) DESC LIMIT 8)
        GROUP BY 1, 2 ORDER BY 1""")
    b.table("Прямые соседи: кого слышит observer", f"""
        SELECT h.label AS "узел", string_agg(DISTINCT h.prefix, ' ' ORDER BY h.prefix) AS "префиксы",
               count(*) AS "приёмов", count(DISTINCT h.packet_hash) AS "пакетов",
               round(avg(h.rssi)) AS "RSSI, дБм", round(avg(h.snr)::numeric, 1) AS "SNR, дБ"
        FROM v_hop_resolved h WHERE {T}(h.received_at) AND h.from_end = 0
        GROUP BY 1 ORDER BY 3 DESC LIMIT 50""", w=14, h=8)
    b.bars("Длина пути", f"""
        SELECT hop_count::text AS "ретрансляторов в пути", count(*) AS "приёмов" FROM reception
        WHERE {T}(received_at) AND {FLOOD} GROUP BY hop_count ORDER BY hop_count""", w=10)
    b.table("Кто кого слышит", f"""
        SELECT a.label AS "от узла", b.label AS "к узлу", count(DISTINCT a.packet_hash) AS "пакетов"
        FROM v_hop_resolved a JOIN v_hop_resolved b ON b.reception_id = a.reception_id AND b.position = a.position + 1
        WHERE {T}(a.received_at) GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 200""", w=12, h=10,
        description="Соседние узлы в путях: правый узел принял пакет от левого и передал дальше. Основа будущего графа сети.")
    b.table("Самые частые пути", f"""
        SELECT string_agg(h.label, ' → ' ORDER BY h.position) AS "путь", count(*) AS "длина", max(x.packets) AS "пакетов"
        FROM (SELECT path_key, min(id) AS id, count(DISTINCT packet_hash) AS packets FROM reception
              WHERE {T}(received_at) AND {FLOOD} AND hop_count > 0 GROUP BY path_key ORDER BY 3 DESC LIMIT 100) x
        JOIN v_hop_resolved h ON h.reception_id = x.id GROUP BY x.path_key ORDER BY 3 DESC""", w=12, h=10,
        description="Слева — ближе к отправителю, справа — наш прямой сосед.")
    b.pie("Размер префикса в пути", f"""
        SELECT prefix_size || ' байт' AS metric, count(*) AS value FROM reception
        WHERE {T}(received_at) AND hop_count > 0 GROUP BY 1 ORDER BY 1""",
        description="Чем длиннее префикс, тем увереннее определяется узел.")
    b.pie("Как определены шаги путей", f"""
        SELECT status AS metric, count(*) AS value FROM v_hop_resolved WHERE {T}(received_at) GROUP BY 1 ORDER BY 2 DESC""")
    b.lines("Точность определения по расчётам", f"""
        SELECT computed_at AS time, 'точность на проверке' AS metric, 100 * (evaluation->>'accuracy')::numeric AS value
        FROM resolution_run WHERE {T}(computed_at) AND evaluation->>'accuracy' IS NOT NULL
        UNION ALL
        SELECT computed_at, 'простое правило', 100 * (evaluation->>'baseline_accuracy')::numeric
        FROM resolution_run WHERE {T}(computed_at) AND evaluation->>'baseline_accuracy' IS NOT NULL
        UNION ALL
        SELECT computed_at, 'доля определённых шагов', 100 * resolved_share
        FROM resolution_run WHERE {T}(computed_at) AND resolved_share IS NOT NULL ORDER BY 1""", "percent", w=8)
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


def radio() -> Board:
    b = Board(*BOARDS[4], ONE_POINT + " Счётчики радио присылает сам observer раз в пять минут.")
    delta = "v_observer_stats_delta"
    b.stat("Занятость эфира", f"SELECT 100.0 * sum(d_rx_air) / nullif(sum(d_uptime), 0) FROM {delta} WHERE {T}(ingested_at)",
           unit="percent", description="Доля времени, когда радио observer'а принимало сигнал, по его собственному счётчику.",
           thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 10}, {"color": "red", "value": 25}])
    b.stat("Принято с ошибкой",
           f"SELECT 100.0 * sum(d_recv_errors) / nullif(sum(d_recv + d_recv_errors), 0) FROM {delta} WHERE {T}(ingested_at)",
           unit="percent", description="Доля пакетов, которые радио начало принимать, но не смогло прочитать: "
                                       "слабый сигнал или наложение передач. Такие пакеты в сбор не попадают.",
           thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 5}, {"color": "red", "value": 15}])
    b.stat("Уровень шума", "SELECT noise_floor FROM v_observer_stats ORDER BY ingested_at DESC LIMIT 1", unit="dBm")
    b.stat("Средний пакет в эфире", f"""
        SELECT avg(lora_airtime_ms(r.packet_len, v.sf, v.bw_khz, v.cr)) FROM reception r CROSS JOIN v_radio v
        WHERE {T}(r.received_at)""", unit="ms",
        description="Расчётная длительность передачи одного пакета при настройках радио observer'а.")
    b.stat("Настройки радио", "SELECT freq_mhz || ' МГц · ' || bw_khz || ' кГц · SF' || sf || ' · CR 4/' || cr FROM v_radio", text=True)
    b.stat("Питание", "SELECT battery_mv / 1000 FROM v_observer_stats ORDER BY ingested_at DESC LIMIT 1", unit="volt")
    b.lines("Занятость эфира", [f"""
        SELECT $__timeGroupAlias(ingested_at, $__interval), 'по счётчику радио' AS metric,
               100.0 * sum(d_rx_air) / nullif(sum(d_uptime), 0) AS value
        FROM {delta} WHERE {T}(ingested_at) GROUP BY 1 ORDER BY 1""", f"""
        SELECT $__timeGroupAlias(r.received_at, $__interval), 'по разобранным пакетам' AS metric,
               100.0 * sum(lora_airtime_ms(r.packet_len, v.sf, v.bw_khz, v.cr)) / $__interval_ms AS value
        FROM reception r CROSS JOIN v_radio v WHERE {T}(r.received_at) GROUP BY 1 ORDER BY 1"""], "percent",
        description="Счётчик радио учитывает всё, что оно слышало, включая непрочитанное. Вторая линия — расчёт по "
                    "принятым пакетам; разница между ними — передачи, которые прочитать не удалось.")
    b.lines("Принято с ошибкой", f"""
        SELECT $__timeGroupAlias(ingested_at, $__interval), 'доля ошибок' AS metric,
               100.0 * sum(d_recv_errors) / nullif(sum(d_recv + d_recv_errors), 0) AS value
        FROM {delta} WHERE {T}(ingested_at) GROUP BY 1 ORDER BY 1""", "percent")
    b.lines("Уровень шума", f"""
        SELECT $__timeGroupAlias(ingested_at, $__interval), 'шум' AS metric, avg(noise_floor) AS value
        FROM v_observer_stats WHERE {T}(ingested_at) GROUP BY 1 ORDER BY 1""", "dBm")
    b.lines("Уровень сигнала соседей (RSSI)", f"""
        SELECT $__timeGroupAlias(received_at, $__interval), label AS metric, avg(rssi) AS value
        FROM v_hop_resolved WHERE {T}(received_at) AND from_end = 0 GROUP BY 1, 2 ORDER BY 1""", "dBm")
    b.table("Сигнал по соседям", f"""
        SELECT label AS "узел", string_agg(DISTINCT prefix, ' ' ORDER BY prefix) AS "префиксы", count(*) AS "приёмов",
               min(rssi) AS "RSSI мин", round(avg(rssi)) AS "RSSI средн", max(rssi) AS "RSSI макс",
               round(min(snr)::numeric, 1) AS "SNR мин", round(avg(snr)::numeric, 1) AS "SNR средн",
               round(max(snr)::numeric, 1) AS "SNR макс"
        FROM v_hop_resolved WHERE {T}(received_at) AND from_end = 0 GROUP BY 1 ORDER BY 3 DESC LIMIT 50""", h=8)
    b.bars_over_time("Эфирное время по типам пакетов, секунд", f"""
        SELECT $__timeGroupAlias(r.received_at, $__interval), payload_type_name(p.payload_type) AS metric,
               sum(lora_airtime_ms(r.packet_len, v.sf, v.bw_khz, v.cr)) / 1000 AS value
        FROM reception r JOIN packet p ON p.hash = r.packet_hash CROSS JOIN v_radio v
        WHERE {T}(r.received_at) GROUP BY 1, 2 ORDER BY 1""",
        description="Какие типы пакетов занимают эфир вокруг observer'а.")
    return b


def rhythm() -> Board:
    b = Board(*BOARDS[5], ONE_POINT + " Часы и дни недели считаются в часовом поясе из настройки MESHCA_TIMEZONE.",
              time_from="now-7d")
    b.variable("ptype", "Тип пакета", """
        SELECT payload_type_name(t::smallint) AS __text, t AS __value
        FROM unnest(ARRAY[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 15]) AS t""")
    b.variable("sender", "Отправитель", "SELECT DISTINCT sender AS __text, sender AS __value FROM v_sender ORDER BY 1")
    days = "(ARRAY['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'])[extract(isodow FROM {t})::int]"
    hours = ", ".join(f"nullif(count(*) FILTER (WHERE extract(hour FROM {{t}}) = {h}), 0) AS \"{h:02d}\"" for h in range(24))

    def by_day_and_hour(source: str, t: str) -> str:
        return (f"SELECT {days.format(t=t)} AS \"день\", {hours.format(t=t)} {source} "
                f"GROUP BY extract(isodow FROM {t}) ORDER BY extract(isodow FROM {t})")

    b.heat_table("Вся сеть: пакеты по дням недели и часам",
                 by_day_and_hour(f"FROM packet p WHERE {T}(p.first_seen) AND p.payload_type IN (${{ptype:csv}})", "p.first_seen"),
                 "день", description="Уникальные пакеты выбранных типов. Столбцы — часы суток.")
    b.heat_table("Выбранные отправители: пакеты по дням недели и часам",
                 by_day_and_hour(f"FROM v_sender s JOIN packet p ON p.hash = s.packet_hash WHERE {T}(p.first_seen) "
                                 f"AND p.payload_type IN (${{ptype:csv}}) AND s.sender IN (${{sender:sqlstring}})",
                                 "p.first_seen"),
                 "день", description="Только пакеты с известным отправителем: объявления, анонимные запросы, сообщения каналов.")
    b.bars("По дням недели", f"""
        SELECT {days.format(t="first_seen")} AS "день", count(*) AS "пакетов" FROM packet
        WHERE {T}(first_seen) AND payload_type IN (${{ptype:csv}})
        GROUP BY extract(isodow FROM first_seen) ORDER BY extract(isodow FROM first_seen)""")
    b.bars("По часам суток", f"""
        SELECT lpad(extract(hour FROM first_seen)::text, 2, '0') AS "час", count(*) AS "пакетов" FROM packet
        WHERE {T}(first_seen) AND payload_type IN (${{ptype:csv}}) GROUP BY 1 ORDER BY 1""")
    b.table("Насколько регулярно узлы объявляют о себе", f"""
        SELECT coalesce(n.name, left(x.pubkey, 8)) AS "узел", node_role_name(n.role) AS "роль", count(*) + 1 AS "объявлений",
               round(percentile_cont(0.5) WITHIN GROUP (ORDER BY x.gap) / 60) AS "медианный интервал, мин",
               round((stddev_samp(x.gap) / nullif(avg(x.gap), 0))::numeric, 2) AS "разброс интервала"
        FROM (SELECT a.pubkey, extract(epoch FROM p.first_seen - lag(p.first_seen) OVER (PARTITION BY a.pubkey ORDER BY p.first_seen)) AS gap
              FROM advert a JOIN packet p ON p.hash = a.packet_hash WHERE a.signature_ok AND {T}(p.first_seen)) x
        LEFT JOIN node n ON n.pubkey = x.pubkey WHERE x.gap IS NOT NULL
        GROUP BY x.pubkey, n.name, n.role HAVING count(*) >= 2 ORDER BY 5 NULLS LAST LIMIT 300""", h=12,
        description="Разброс — отношение стандартного отклонения интервала к среднему. Около 0: узел объявляет о себе "
                    "по расписанию. Около 1 и больше: нерегулярно, например вручную или при перемещении. "
                    "Пропущенные нами объявления увеличивают разброс.")
    return b


def node_map() -> Board:
    b = Board(*BOARDS[6], "Узлы, которые сообщили свои координаты в объявлении. Подложку карты браузер загружает "
              "из интернета.", time_from="now-7d")
    roles = [("ретрансляторы", "green", "= 2"), ("клиенты", "blue", "= 1"), ("серверы комнат", "purple", "= 3"),
             ("датчики и прочие", "orange", "NOT IN (1, 2, 3)")]
    b.geomap("Узлы с координатами", [(name, color, f"""
        SELECT name AS "имя", lat, lon, advert_count AS "объявлений", last_seen AS "последний раз"
        FROM node WHERE lat IS NOT NULL AND {T}(last_seen) AND coalesce(role, 0) {condition}""")
        for name, color, condition in roles],
        description="Узлы, объявлявшие о себе за выбранный период. Зелёные — ретрансляторы, синие — клиенты, "
                    "фиолетовые — серверы комнат, оранжевые — датчики и прочие.")
    b.table("Узлы без координат", f"""
        SELECT name AS "имя", node_role_name(role) AS "роль", advert_count AS "объявлений", last_seen AS "последний раз"
        FROM node WHERE lat IS NULL AND {T}(last_seen) ORDER BY last_seen DESC LIMIT 500""", h=8)
    return b


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for build in (overview, nodes, relays, channels, radio, rhythm, node_map):
        build().save()
    print("written:", ", ".join(sorted(p.name for p in OUT.glob("*.json"))))
