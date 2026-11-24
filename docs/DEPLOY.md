# meshca: развёртывание и подключение observer'а

Здесь нет адресов, имён машин и паролей: подставьте свои. Обозначения:

- **сервер** — машина с Docker, где работает стек meshca;
- **observer** — машина с сервисом `meshcore-packet-capture`.

## 1. Стек на сервере

Нужны Docker с Compose и отдельный том под данные (не корневой раздел).

```sh
git clone https://github.com/smagew/meshca.git /srv/meshca/app
cd /srv/meshca/app
cp .env.example .env && chmod 600 .env
# заполнить .env: MESHCA_DATA_DIR и три пароля (openssl rand -hex 24)
docker compose up -d --build
docker compose ps          # collector и postgres должны быть healthy
```

Что запускается:

| Контейнер | Назначение | Порт |
|---|---|---|
| `mosquitto` | Брокер проекта: пользователь `observer` только публикует, `collector` только читает | `1884` в локальной сети |
| `postgres` | База `meshca` | `5433`, только с самого сервера |
| `migrate` | Применяет `db/migrations/*.sql` и завершается | — |
| `collector` | Пишет каждое сообщение observer'а в базу без изменений | — |

Проверка без observer'а (тестовое сообщение попадёт в `observer_message`):

```sh
docker compose exec mosquitto mosquitto_pub -h localhost -u observer -P "$(grep ^MQTT_OBSERVER_PASSWORD .env | cut -d= -f2)" \
  -t meshcore/_selftest/x/selftest -m '{"selftest":true}'
docker compose exec postgres psql -U meshca -d meshca -c "select id, ingested_at, kind from observer_message order by id desc limit 3"
```

Обновление: `git pull && docker compose up -d --build`.

## 2. Подключение observer'а

Единственное изменение на observer'е — один новый файл настроек и один перезапуск сервиса.
Существующие файлы не редактируются, отправка в LetsMesh не меняется.

Файл `/etc/meshcore-packet-capture/config.d/50-meshca.toml` (владелец `root:meshcore-capture`, права `640`):

```toml
# meshca: локальная копия всех пакетов. Удалить этот файл и перезапустить сервис = полный откат.
[[broker]]
name = "meshca"
enabled = true
server = "АДРЕС_СЕРВЕРА"
port = 1884
transport = "tcp"
keepalive = 60
qos = 0
retain = false

[broker.tls]
enabled = false

[broker.auth]
method = "password"
username = "observer"
password = "ЗНАЧЕНИЕ MQTT_OBSERVER_PASSWORD ИЗ .env"
```

Применить:

```sh
sudo systemctl restart meshcore-packet-capture
journalctl -u meshcore-packet-capture -n 30 --no-pager   # ждём "Connected to MQTT broker: meshca"
```

Перерыв в захвате — несколько секунд на перезапуск. Декодирование в observer'е не включается: разбор делается из поля `raw`.

## 3. Испытания «observer не пострадал»

Проводятся сразу после подключения. Если любое не проходит — откат (раздел 4).

1. **Обычная работа.** В журнале observer'а строки `Captured packet ... (MQTT: 3/3)`, на сервере растёт `select count(*) from raw_reception`.
2. **Наш брокер недоступен.** На сервере `docker compose stop mosquitto` на 10 минут. Observer обязан продолжать захват и отправку в LetsMesh: в журнале `(MQTT: 2/2)` и предупреждения `meshca client not connected`, сервис не перезапускается. Затем `docker compose start mosquitto`: observer переподключается сам за несколько секунд, снова `3/3`.
3. **Запуск при выключенном брокере.** `docker compose stop mosquitto`, на observer'е `sudo systemctl restart meshcore-packet-capture`. Сервис обязан запуститься и слать в LetsMesh (`MQTT: 2/2`). Затем `docker compose start mosquitto`
   и ещё один `sudo systemctl restart meshcore-packet-capture`: сам observer к нашему брокеру в этом случае не вернётся (раздел 5).
4. **Сверка за сутки.** Число строк `raw_reception` за сутки и число строк `Captured packet` в журнале observer'а за те же сутки расходятся не больше чем на 1%.

Результат испытаний 1–3 на версии observer'а 2.2.1: пройдены. Остановка брокера на 10 минут не вызвала ни перезапуска,
ни потерь в LetsMesh; после запуска при выключенном брокере потребовался ручной перезапуск observer'а.

## 4. Откат

```sh
sudo rm /etc/meshcore-packet-capture/config.d/50-meshca.toml
sudo systemctl restart meshcore-packet-capture
```

После этого observer работает в точности как до подключения. Стек на сервере можно остановить: `docker compose down` (данные остаются в `MESHCA_DATA_DIR`).

## 5. Что важно знать

- Observer публикует с qos 0: пока `collector` или брокер выключены, пакеты в базу не попадают и позже не досылаются (в LetsMesh они при этом уходят).
- **Observer не повторяет попытку к брокеру, который не ответил при его старте.** Если сервис observer'а запустился,
  пока наш брокер был недоступен (например, после отключения электричества observer загрузился раньше сервера),
  пакеты в meshca не пойдут до ручного `sudo systemctl restart meshcore-packet-capture`. Обрыв уже установленного
  соединения observer переживает сам. Признак: в журнале observer'а `(MQTT: 2/2)` вместо `3/3`, в базе не растёт `raw_reception`.
  То же относится к брокерам LetsMesh: если при старте не ответил ни один, observer работает без отправки до перезапуска.
- Пока недоступна только база, `collector` держит сообщения в памяти (до 200 000) и записывает их, когда база вернётся.
- Бэкап базы на этом этапе не делается: отказ диска означает потерю накопленной истории.
- Пароли живут только в `.env` на сервере и в файле настроек observer'а.
