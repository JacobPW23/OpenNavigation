FROM debian:bookworm-slim

ENV KAFKA_VERSION=3.7.0
ENV SCALA_VERSION=2.13
ENV SPARK_VERSION=3.5.1
ENV HADOOP_VERSION=3

ENV JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64
ENV KAFKA_HOME=/opt/kafka
ENV SPARK_HOME=/opt/spark
ENV PATH=$PATH:$JAVA_HOME/bin:$KAFKA_HOME/bin:$SPARK_HOME/bin
ENV PYTHONPATH=/app:$SPARK_HOME/python:$SPARK_HOME/python/lib/py4j-0.10.9.7-src.zip
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    curl \
    netcat-openbsd \
    openjdk-17-jre-headless \
    procps \
    python3 \
    python3-pip \
    unzip \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3 /usr/bin/python

WORKDIR /opt

RUN set -eux; \
    curl -fL --retry 5 --retry-delay 5 --connect-timeout 30 \
      -o /tmp/kafka.tgz \
      https://archive.apache.org/dist/kafka/${KAFKA_VERSION}/kafka_${SCALA_VERSION}-${KAFKA_VERSION}.tgz; \
    tar -xzf /tmp/kafka.tgz -C /opt; \
    mv /opt/kafka_${SCALA_VERSION}-${KAFKA_VERSION} ${KAFKA_HOME}; \
    rm /tmp/kafka.tgz; \
    curl -fL --retry 5 --retry-delay 5 --connect-timeout 30 \
      -o /tmp/spark.tgz \
      https://archive.apache.org/dist/spark/spark-${SPARK_VERSION}/spark-${SPARK_VERSION}-bin-hadoop${HADOOP_VERSION}.tgz; \
    tar -xzf /tmp/spark.tgz -C /opt; \
    mv /opt/spark-${SPARK_VERSION}-bin-hadoop${HADOOP_VERSION} ${SPARK_HOME}; \
    rm /tmp/spark.tgz

WORKDIR /app

# System dependencies for GeoPandas/Fiona/OSMnx
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    python3-dev \
    pkg-config \
    gdal-bin \
    libgdal-dev \
    libgeos-dev \
    libproj-dev \
    proj-bin \
    proj-data \
    libspatialindex-dev \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sfn "$(dirname "$(dirname "$(readlink -f "$(which java)")")")" /usr/lib/jvm/default-java

ENV JAVA_HOME=/usr/lib/jvm/default-java
ENV PATH=$JAVA_HOME/bin:$PATH

COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt

# Install Playwright browsers and dependencies
RUN playwright install --with-deps

COPY server.properties /opt/kafka/config/server.properties
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
