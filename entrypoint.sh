#!/usr/bin/env bash
set -euo pipefail

export PATH="/opt/kafka/bin:${PATH}"

KAFKA_BOOTSTRAP_SERVER="localhost:9092"
TOPICS=(
  "warszawa-raw-streets"       # OSM / Overpass: raw road network elements
  "warszawa-raw-traffic"       # ZDM traffic measurements
  "warszawa-raw-gddkia"        # GDDKiA historical/helper traffic data
  "warszawa-raw-weather"       # weather context, added to avoid homogeneous sources
  "warszawa-raw-events"        # road works/incidents/traffic restrictions
  "warszawa-processed-edges"   # normalized graph edges with costs
  "warszawa-route-results"     # route-analysis results
)

mkdir -p /tmp/kafka-logs /tmp/zookeeper /app/data/raw /app/data/processed /app/data/results

wait_for_port() {
  local host="$1"
  local port="$2"
  local name="$3"
  echo "Waiting for ${name} on ${host}:${port}..."
  until nc -z "${host}" "${port}"; do
    sleep 1
  done
  echo "${name} is available."
}

echo "Starting ZooKeeper..."
zookeeper-server-start.sh -daemon /opt/kafka/config/zookeeper.properties
wait_for_port localhost 2181 "ZooKeeper"

echo "Starting Kafka broker..."
kafka-server-start.sh -daemon /opt/kafka/config/server.properties
wait_for_port localhost 9092 "Kafka"

echo "Creating Kafka topics..."
for topic in "${TOPICS[@]}"; do
  kafka-topics.sh \
    --bootstrap-server "${KAFKA_BOOTSTRAP_SERVER}" \
    --create \
    --if-not-exists \
    --topic "${topic}" \
    --partitions 1 \
    --replication-factor 1
done

echo "Available Kafka topics:"
kafka-topics.sh --bootstrap-server "${KAFKA_BOOTSTRAP_SERVER}" --list

echo "OpenNavigation infrastructure is ready."
tail -f /dev/null
