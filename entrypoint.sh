#! /bin/sh
export PATH=/opt/kafka/bin:$PATH;
KAFKA_CLUSTER_ID="$(kafka-storage.sh random-uuid)";
kafka-storage.sh format -t $KAFKA_CLUSTER_ID -c /opt/kafka/config/server.properties;
zookeeper-server-start.sh -daemon /opt/kafka/config/zookeeper.properties;
sleep 5;
kafka-server-start.sh -daemon /opt/kafka/config/server.properties;
sleep 3;
kafka-topics.sh --create --topic warszawa-raw-streets --bootstrap-server localhost:9092

while true;
do
sleep 2;
done