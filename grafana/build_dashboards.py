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
            overrides: list | None = None, ref_ids: list[str] | None = None) -> None:
        queries = [sql] if isinstance(sql, str) else sql
        ref_ids = ref_ids or list("ABCDEF")
        self.panels.append({
            "id": len(self.panels) + 1, "type": kind, "title": title, "description": description,
            "datasource": DS, "gridPos": self._place(w, h),
            "targets": [{"refId": ref_ids[i], "datasource": DS, "format": fmt, "rawQuery": True,
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
          ("meshca-radio", "meshca · Радио"), ("meshca-rhythm", "meshca · Ритм"), ("meshca-map", "meshca · Карта"),
          ("meshca-topology", "meshca · Топология"), ("meshca-changes", "meshca · Изменения"),
          ("meshca-talk", "meshca · Общение")]

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
    b.table("Ритм отправителей: период, рваность, ночная активность", """
        SELECT label AS "отправитель", kind AS "что шлёт", events AS "пакетов",
               round((median_gap_s / 60)::numeric) AS "медианный интервал, мин",
               CASE WHEN period_confirmed THEN round((period_s / 60)::numeric) END AS "период, мин",
               round(period_strength::numeric, 2) AS "сила периода",
               round(burstiness::numeric, 2) AS "рваность",
               round((100 * night_share)::numeric) AS "ночью, %",
               CASE WHEN NOT judged THEN 'мало данных' WHEN period_confirmed THEN 'по расписанию'
                    WHEN burstiness > 0.3 THEN 'всплесками' ELSE 'без явного ритма' END AS "вывод"
        FROM node_rhythm ORDER BY judged DESC, events DESC LIMIT 500""", h=12,
        description="Считается раз в 6 часов за последние 30 дней, только по отправителям, известным точно. "
                    "Период указан, если он найден на первой половине данных и подтвердился на второй. "
                    "Рваность: −1 — как часы, 0 — случайно, ближе к 1 — пачками с долгими паузами. "
                    "Ночь — с 0 до 6 часов. Выводы делаются от 30 пакетов; раньше — «мало данных».")
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


# Link graph (node graph panel). Colours of the kinds of packets, shared by edges and the rings around nodes.
KIND_COLOURS = {"объявления": "#5794F2", "каналы": "#73BF69", "адресные": "#FF9830"}
GEO_SCALE = 60          # pixels per km ** 0.55 from the observer


def graph_queries(edges_sql: str, geo: bool) -> list[str]:
    """Two frames for the node graph: nodes and edges. With geo, nodes are pinned to their coordinates."""
    base = f"""
        WITH e AS ({edges_sql}),
        ids AS (SELECT from_node AS id FROM e UNION SELECT to_node FROM e),
        own AS (SELECT i.id, n.lat, n.lon, n.role FROM ids i LEFT JOIN node n ON n.pubkey = i.id),
        nb AS (SELECT o.id, avg(x.lat) AS lat, avg(x.lon) AS lon
               FROM own o JOIN e ON o.id IN (e.from_node, e.to_node)
               JOIN own x ON x.id = CASE WHEN e.from_node = o.id THEN e.to_node ELSE e.from_node END
               WHERE x.lat IS NOT NULL AND o.lat IS NULL GROUP BY o.id),
        pos AS (SELECT o.id, o.role, o.lat IS NULL AS guessed,
                       coalesce(o.lat, nb.lat + (('x' || substr(md5(o.id), 1, 4))::bit(16)::int % 100 - 50) / 8000.0) AS lat,
                       coalesce(o.lon, nb.lon + (('x' || substr(md5(o.id), 5, 4))::bit(16)::int % 100 - 50) / 5000.0) AS lon
                FROM own o LEFT JOIN nb USING (id)),
        c AS (SELECT coalesce(max(lat) FILTER (WHERE id = '@observer'), avg(lat)) AS lat0,
                     coalesce(max(lon) FILTER (WHERE id = '@observer'), avg(lon)) AS lon0 FROM pos),
        flow AS (SELECT id, sum(p) AS p, sum(a) AS a, sum(cc) AS cc, sum(d) AS d FROM (
                     SELECT from_node AS id, p, a, c AS cc, d FROM e
                     UNION ALL SELECT to_node, p, a, c, d FROM e WHERE to_node = '@observer') f GROUP BY id),
        out AS (SELECT from_node AS id, sum(p) AS relayed FROM e GROUP BY 1)"""
    k = GEO_SCALE
    # Distances from the observer are compressed (radius ** 0.55): the dense area around it opens up, far nodes come
    # closer, directions stay true, north is up. Every node needs a place: those with no coordinates and no placed
    # neighbour go into a row under the drawing.
    fixed = (f""",
               coalesce(round((CASE WHEN pos.id = '@observer' THEN 0 ELSE g.px * g.dx / nullif(g.r, 0) END)::numeric),
                        -{k} * 12 + 90 * (row_number() OVER (PARTITION BY g.r IS NULL ORDER BY pos.id) - 1)) AS fixedx,
               coalesce(round((CASE WHEN pos.id = '@observer' THEN 0 ELSE -g.px * g.dy / nullif(g.r, 0) END)::numeric),
                        {k} * 14) AS fixedy""" if geo else "")
    geo_join = f"""
        LEFT JOIN LATERAL (SELECT d.dx, d.dy, sqrt(d.dx * d.dx + d.dy * d.dy) AS r,
                                  40 + {k} * power(sqrt(d.dx * d.dx + d.dy * d.dy), 0.55) AS px
                           FROM (SELECT (pos.lon - c.lon0) * 111.32 * cos(radians(c.lat0)) AS dx,
                                        (pos.lat - c.lat0) * 110.57 AS dy) d) g ON true""" if geo else ""
    nodes = base + f"""
        SELECT pos.id, node_label(pos.id) AS title,
               CASE WHEN pos.id = '@observer' THEN 'наш observer' WHEN left(pos.id, 1) = '?' THEN 'не определён'
                    ELSE coalesce(node_role_name(pos.role), 'нет в справочнике') END AS subtitle,
               coalesce(out.relayed, flow.p, 0)::text AS mainstat,
               CASE WHEN cn.node IS NOT NULL THEN 'критичный: отрежет ' || cn.senders_cut END AS secondarystat,
               (10 + least(30, 1.2 * sqrt(coalesce(out.relayed, flow.p, 0))))::int AS noderadius,
               cn.node IS NOT NULL AS highlighted,
               CASE WHEN pos.id = '@observer' THEN '#B877D9' WHEN flow.p IS NULL THEN '#8E8E8E'
                    WHEN GREATEST(flow.a, flow.cc, flow.d) = flow.a THEN '{KIND_COLOURS["объявления"]}'
                    WHEN GREATEST(flow.a, flow.cc, flow.d) = flow.cc THEN '{KIND_COLOURS["каналы"]}'
                    ELSE '{KIND_COLOURS["адресные"]}' END AS color,
               flow.a AS "detail__объявлений передал", flow.cc AS "detail__сообщений каналов передал",
               flow.d AS "detail__адресных пакетов передал",
               CASE WHEN pos.guessed AND pos.lat IS NOT NULL THEN 'примерное, среди соседей'
                    WHEN pos.lat IS NULL THEN 'неизвестно' ELSE 'из объявления' END AS "detail__место",
               cn.senders AS "detail__кого отрежет"{fixed}
        FROM pos CROSS JOIN c LEFT JOIN out USING (id) LEFT JOIN flow USING (id)
        LEFT JOIN critical_node cn ON cn.node = pos.id{geo_join}"""
    edges = f"""
        WITH e AS ({edges_sql})
        SELECT from_node || '>' || to_node AS id, from_node AS source, to_node AS target, p::text AS mainstat,
               'объявления ' || round(100.0 * a / nullif(p, 0)) || '% · каналы ' || round(100.0 * c / nullif(p, 0))
                   || '% · адресные ' || round(100.0 * d / nullif(p, 0)) || '%' AS secondarystat,
               least(12, 1 + 1.4 * ln(greatest(p, 1)))::int AS thickness,
               CASE GREATEST(a, c, d) WHEN a THEN '{KIND_COLOURS["объявления"]}' WHEN c THEN '{KIND_COLOURS["каналы"]}'
                    ELSE '{KIND_COLOURS["адресные"]}' END AS color,
               CASE WHEN left(from_node, 1) = '?' OR left(to_node, 1) = '?' THEN '6 4' END AS strokedasharray,
               a AS "detail__объявлений", c AS "detail__сообщений каналов", d AS "detail__адресных пакетов"
        FROM e"""
    return [nodes, edges]


def topology() -> Board:
    b = Board(*BOARDS[7], ONE_POINT + " Связь «A → B» значит: B принял пакет от A и передал дальше. Все маршруты "
              "ведут к нашему observer'у, поэтому это не вся сеть, а её часть, обращённая к нам. Выводы о критичных "
              "узлах верны настолько, насколько верно определены узлы за префиксами (панель «Ретрансляторы»).",
              time_from="now-7d")
    edges = f"""(SELECT from_node, to_node, sum(packets) AS packets FROM edge_daily
                 WHERE day >= $__timeFrom()::date AND day <= $__timeTo()::date GROUP BY 1, 2) e"""
    edges_kinds = """SELECT from_node, to_node, sum(packets) AS p, sum(adverts) AS a, sum(channel) AS c, sum(direct) + sum(service) AS d
                     FROM edge_daily WHERE day >= $__timeFrom()::date AND day <= $__timeTo()::date
                     GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 200"""
    graph_legend = ("Толщина линии — сколько пакетов прошло по связи, стрелка — куда. Цвет линии — чего больше "
                    "всего везёт связь: синий — объявления, зелёный — сообщения каналов, оранжевый — адресные пакеты (одному узлу: личные сообщения, запросы и ответы). "
                    "Пунктир — один из концов не определён по префиксу (показан как «? префикс»). Размер кружка — "
                    "сколько пакетов узел передал дальше, цвет кольца — каких пакетов он передаёт больше всего, в тех же "
                    "цветах; фиолетовый — наш observer. Красный кружок с надписью «критичный» — без этого узла какие-то "
                    "отправители перестанут до нас доходить. Нажмите на узел или связь — покажутся подробности. "
                    "Показаны 200 самых загруженных связей выбранного периода.")
    b.add("nodeGraph", "Граф связей: по координатам", graph_queries(edges_kinds, geo=True), 24, 22,
          ref_ids=["nodes", "edges"],
          description="Наш observer в центре, узлы — в своих направлениях от него по координатам из объявлений, "
                      "север сверху. Расстояния сжаты: дальние узлы нарисованы ближе, чем на самом деле, чтобы "
                      "раздвинуть плотные места рядом с нами. Узел без координат поставлен среди своих соседей "
                      "(в подсказке — «место примерное»); если и соседей нет — в ряд внизу. Колёсиком мыши можно "
                      "приблизить. Очень длинная связь — повод проверить, верно ли определён узел. "
                      + graph_legend)
    b.add("nodeGraph", "Граф связей: свободная раскладка", graph_queries(edges_kinds.replace("LIMIT 200", "LIMIT 100"), geo=False), 24, 18,
          ref_ids=["nodes", "edges"],
          description="100 самых загруженных связей, расставленные так, чтобы не налезали друг на друга. Дальние "
                      "узлы Grafana сворачивает в метки по краям: нажатие на метку разворачивает их. "
                      + graph_legend.replace("Показаны 200 самых загруженных связей выбранного периода.", ""))
    b.table("Критичные узлы", """
        SELECT node_label(node) AS "узел", senders_cut AS "отправителей отрежет",
               array_to_string(senders, ', ') AS "кто перестанет до нас доходить"
        FROM critical_node ORDER BY senders_cut DESC, node LIMIT 200""", w=14, h=10,
        description="Ретрансляторы, которые стоят на всех наблюдавшихся маршрутах отправителя к нашему observer'у. "
                    "Учитываются отправители с не менее чем 10 приёмами. Прямые соседи observer'а "
                    "попадают сюда закономерно: всё приходит к нам через них.")
    b.table("Отправители с единственным маршрутом", """
        SELECT sender AS "отправитель", packets AS "приёмов",
               (SELECT string_agg(node_label(c), ' → ') FROM unnest(critical) AS c) AS "маршрут держится на"
        FROM sender_route WHERE judged AND routes = 1 AND direct_share < 1 ORDER BY packets DESC LIMIT 200""", w=10, h=10,
        description="Все принятые пакеты этих отправителей пришли одним и тем же путём.")
    b.table("Разнообразие маршрутов отправителей", """
        SELECT sender AS "отправитель", packets AS "приёмов", routes AS "маршрутов", round(entropy::numeric, 2) AS "энтропия, бит",
               round(100 * direct_share::numeric) AS "слышим напрямую, %", cardinality(critical) AS "критичных узлов",
               CASE WHEN judged THEN 'да' ELSE 'мало данных' END AS "оценка надёжна"
        FROM sender_route ORDER BY packets DESC LIMIT 500""", w=24, h=12,
        description="Энтропия 0 — всегда один маршрут; чем больше, тем равномернее пакеты распределены по разным путям.")
    b.table("Самые загруженные связи", f"""
        SELECT node_label(from_node) AS "от узла", node_label(to_node) AS "к узлу", packets AS "пакетов"
        FROM {edges} ORDER BY packets DESC LIMIT 300""", w=24, h=10)
    return b


def changes() -> Board:
    b = Board(*BOARDS[8], "Что меняется в справочнике узлов: новые узлы, смена имени, роли или координат, одно имя "
              "у разных ключей, сбитые часы. Справочник строится только из объявлений с верной подписью. В первые "
              "недели сбора почти каждый узел «новый»: мы его просто ещё не слышали.", time_from="now-7d")
    events = f"FROM v_node_event WHERE {T}(at)"
    b.stat("Новых узлов", f"SELECT count(*) {events} AND event = 'новый узел'")
    b.stat("Смен имени, роли, координат", f"SELECT count(*) {events} AND event LIKE 'смена%'")
    b.stat("Одно имя у разных ключей", f"SELECT count(*) {events} AND event = 'то же имя, другой ключ'")
    b.stat("Часы сбиты больше чем на 10 минут", "SELECT count(*) FROM v_node_clock WHERE abs(offset_s) > 600",
           description="По всем узлам за всё время, не только за выбранный период.")
    b.table("События справочника", f"""
        SELECT at AS "когда", label AS "узел", event AS "что", old AS "было", new AS "стало", left(pubkey, 10) AS "ключ"
        {events} ORDER BY (event = 'новый узел'), at DESC LIMIT 500""", h=12,
        description="Сначала изменения, потом новые узлы. «То же имя, другой ключ»: устройство перепрошили, у владельца "
                    "второе устройство или кто-то назвался чужим именем — в колонках «было» и «стало» начала ключей.")
    b.table("Часы узлов", """
        SELECT label AS "узел", node_role_name(role) AS "роль", adverts AS "объявлений",
               CASE WHEN abs(offset_s) < 120 THEN 'верно'
                    WHEN abs(offset_s) < 3600 THEN round((offset_s / 60)::numeric) || ' мин'
                    WHEN abs(offset_s) < 172800 THEN round((offset_s / 3600)::numeric, 1) || ' ч'
                    ELSE round((offset_s / 86400)::numeric) || ' дн' END AS "часы узла",
               last_advert AS "последнее объявление"
        FROM v_node_clock ORDER BY abs(offset_s) DESC LIMIT 500""", h=12,
        description="Разница между временем, которое узел пишет в своём объявлении, и временем, когда мы его услышали "
                    "(медиана по объявлениям узла). Плюс — часы узла спешат, минус — отстают. Сбитые часы не мешают "
                    "сети, но путают время в сообщениях этого узла.")
    return b


def talk() -> Board:
    b = Board(*BOARDS[9], ONE_POINT + " Личные пакеты (запросы, ответы, личные сообщения, пути) несут только по одному "
              "байту ключа отправителя и получателя, поэтому собеседники — пары этих байтов; рядом показано, какие "
              "известные узлы под байт подходят. Сессия — пакеты одной пары с паузами меньше 10 минут. Расчёт раз в "
              "час за последние 30 дней.", time_from="now-7d")
    sess = f"FROM dm_session WHERE {T}(started_at)"
    b.stat("Сессий личных пакетов", f"SELECT count(*) {sess}")
    b.stat("Пар собеседников", f"SELECT count(DISTINCT (a, b)) {sess}")
    b.stat("Время ответа, медиана", f"SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY median_response_s) {sess}",
           unit="s", description="Ответ — пакет с другой стороны не позже 30 секунд после пакета первой.")
    b.stat("Упоминаний в каналах", "SELECT coalesce(sum(mentions), 0) FROM channel_link",
           description="Обращения вида @[имя] в расшифрованных сообщениях, за 30 дней.")
    b.stat("Сессий с ответом", f"SELECT round(100.0 * count(*) FILTER (WHERE responses > 0) / nullif(count(*), 0)) {sess}",
           unit="percent")
    b.stat("Пакетов в сессии, медиана", f"SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY packets) {sess}")
    b.table("Кто с кем: пары личных пакетов", f"""
        SELECT hash_names(a) AS "сторона A", hash_names(b) AS "сторона B", count(*) AS "сессий",
               sum(packets) AS "пакетов", sum(a_to_b) AS "A → B", sum(b_to_a) AS "B → A",
               round(percentile_cont(0.5) WITHIN GROUP (ORDER BY median_response_s)::numeric, 1) AS "ответ, с",
               max(ended_at) AS "последний раз"
        {sess} GROUP BY a, b ORDER BY 4 DESC LIMIT 300""", h=12,
        description="Если под байт подходит несколько известных узлов, кто именно из них — не определено. "
                    "«Неизвестен» — ни одного объявления с таким началом ключа мы не слышали.")
    b.table("Последние сессии", f"""
        SELECT started_at AS "начало", round(extract(epoch FROM ended_at - started_at)) AS "длилась, с",
               hash_names(a) AS "A", hash_names(b) AS "B", packets AS "пакетов",
               left(array_to_string(kinds, ' → '), 200) AS "ход"
        {sess} ORDER BY started_at DESC LIMIT 300""", h=10,
        description="«a:REQ → b:RESPONSE» — A спросил, B ответил. REQ — запрос (вход, статус, телеметрия), "
                    "RESPONSE — ответ, TXT_MSG — личное сообщение, PATH — сообщённый обратный путь.")
    b.table("Что за чем следует в сессиях", f"""
        WITH step AS (
            SELECT s.id, k.kind, k.n FROM dm_session s, unnest(s.kinds) WITH ORDINALITY AS k(kind, n)
            WHERE {T}(s.started_at)
        ), pair AS (
            SELECT substr(x.kind, 3) AS from_kind, CASE WHEN left(x.kind, 1) = left(y.kind, 1) THEN 'та же сторона'
                   ELSE 'другая сторона' END AS who, substr(y.kind, 3) AS to_kind
            FROM step x JOIN step y ON y.id = x.id AND y.n = x.n + 1
        )
        SELECT from_kind AS "после", who AS "кто", to_kind AS "идёт", count(*) AS "раз",
               round(100.0 * count(*) / sum(count(*)) OVER (PARTITION BY from_kind)) AS "% от «после»"
        FROM pair GROUP BY 1, 2, 3 ORDER BY 1, 4 DESC""", w=12, h=10)
    b.table("Каналы: кто кому отвечает", """
        SELECT channel AS "канал", source AS "кто", target AS "кому", mentions AS "упоминаний",
               quick AS "быстрых ответов", round(expected::numeric, 2) AS "случайно ожидалось",
               round((quick / nullif(expected, 0))::numeric, 1) AS "во сколько раз чаще случая"
        FROM channel_link ORDER BY mentions + quick DESC LIMIT 500""", w=12, h=10,
        description="Упоминание @[имя] — точный признак обращения. Быстрый ответ — сообщение в течение 2 минут после "
                    "сообщения другого; он бывает и случайным, поэтому рядом — сколько таких совпадений ожидалось бы "
                    "при случайных временах. Отправитель взят из текста сообщения и протоколом не проверяется.")
    return b


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for build in (overview, nodes, relays, channels, radio, rhythm, node_map, topology, changes, talk):
        build().save()
    print("written:", ", ".join(sorted(p.name for p in OUT.glob("*.json"))))
