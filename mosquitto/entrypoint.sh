#!/bin/sh
# Builds the password file from the environment at every start, so no secret lives in the repository or in a volume.
set -eu
: "${MQTT_OBSERVER_PASSWORD:?MQTT_OBSERVER_PASSWORD is not set}"
: "${MQTT_COLLECTOR_PASSWORD:?MQTT_COLLECTOR_PASSWORD is not set}"

PASSWD=/mosquitto/config/passwd
ACL=/mosquitto/config/acl
rm -f "$PASSWD"
mosquitto_passwd -b -c "$PASSWD" observer "$MQTT_OBSERVER_PASSWORD"
mosquitto_passwd -b "$PASSWD" collector "$MQTT_COLLECTOR_PASSWORD"
cp /meshca/acl "$ACL"
chown mosquitto:mosquitto "$PASSWD" "$ACL"
chmod 600 "$PASSWD" "$ACL"

exec /usr/sbin/mosquitto -c /meshca/mosquitto.conf
