FROM debian:bookworm-slim

ENV KAFKA_VERSION=3.7.0
ENV SCALA_VERSION=2.13
ENV SPARK_VERSION=3.5.1
ENV HADOOP_VERSION=3

ENV JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64
ENV KAFKA_HOME=/opt/kafka
ENV SPARK_HOME=/opt/spark

ENV PATH=$PATH:$JAVA_HOME/bin:$KAFKA_HOME/bin:$SPARK_HOME/bin
ENV PYTHONPATH=$SPARK_HOME/python:$SPARK_HOME/python/lib/py4j-0.10.9.7-src.zip

RUN apt-get update && apt-get install -y --no-install-recommends \
    openjdk-17-jre-headless \
    python3 \
    python3-pip \
    curl \
    procps \
    unzip \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3 /usr/bin/python

WORKDIR /opt

RUN curl -sS https://archive.apache.org/dist/kafka/${KAFKA_VERSION}/kafka_${SCALA_VERSION}-${KAFKA_VERSION}.tgz | tar -xzf - \
    && mv kafka_${SCALA_VERSION}-${KAFKA_VERSION} ${KAFKA_HOME} \
    && curl -sS https://archive.apache.org/dist/spark/spark-${SPARK_VERSION}/spark-${SPARK_VERSION}-bin-hadoop${HADOOP_VERSION}.tgz | tar -xzf - \
    && mv spark-${SPARK_VERSION}-bin-hadoop${HADOOP_VERSION} ${SPARK_HOME}

RUN pip3 install --no-cache-dir --break-system-packages \
    pyspark==${SPARK_VERSION} \
    kafka-python==2.0.2 \
    requests \
    confluent-kafka
WORKDIR /app
COPY entrypoint.sh /entrypoint.sh
RUN chmod u+x /entrypoint.sh
COPY server.properties /opt/kafka/config
#COPY src /app/
ENTRYPOINT ["/entrypoint.sh"]
