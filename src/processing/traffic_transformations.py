"""
Transformacja danych ruchu drogowego z Kafki do Parquet.

Wejście:
  Kafka topic: warszawa-raw-traffic

Wyjście:
  Parquet: /app/data/processed/traffic_measurements

Cel:
  Przygotowanie punktowych danych o natężeniu ruchu do późniejszego wzbogacenia
  krawędzi grafu drogowego.
"""

from pyspark.sql import SparkSession
from pyspark.sql.functions import (
    col,
    current_timestamp,
    from_json,
    lit,
    to_timestamp,
    when,
)
from pyspark.sql.types import (
    DoubleType,
    IntegerType,
    StringType,
    StructField,
    StructType,
)


KAFKA_BOOTSTRAP_SERVERS = "localhost:9092"
RAW_TOPIC = "warszawa-raw-traffic"

OUTPUT_PATH = "/app/data/processed/traffic_measurements"
CHECKPOINT_PATH = "/app/data/processed/checkpoints/traffic_measurements_stream"


spark = (
    SparkSession.builder
    .appName("TrafficKafkaTransformations")
    .config("spark.sql.shuffle.partitions", "4")
    .getOrCreate()
)

spark.sparkContext.setLogLevel("WARN")


metadata_schema = StructType([
    StructField("ingested_at_utc", StringType(), True),
    StructField("loader", StringType(), True),
])

traffic_schema = StructType([
    StructField("source", StringType(), True),
    StructField("station_id", StringType(), True),
    StructField("station_name", StringType(), True),
    StructField("road_name", StringType(), True),
    StructField("direction", StringType(), True),
    StructField("lat", DoubleType(), True),
    StructField("lon", DoubleType(), True),
    StructField("measurement_time", StringType(), True),
    StructField("period_minutes", IntegerType(), True),
    StructField("vehicle_count", IntegerType(), True),
    StructField("avg_speed_kmh", DoubleType(), True),
    StructField("_metadata", metadata_schema, True),
])


raw_stream = (
    spark.readStream
    .format("kafka")
    .option("kafka.bootstrap.servers", KAFKA_BOOTSTRAP_SERVERS)
    .option("subscribe", RAW_TOPIC)
    .option("startingOffsets", "earliest")
    .load()
)

json_stream = raw_stream.selectExpr("CAST(value AS STRING) AS json_payload")

traffic_rows = (
    json_stream
    .withColumn("data", from_json(col("json_payload"), traffic_schema))
    .select(
        col("data.source").alias("source"),
        col("data.station_id").alias("station_id"),
        col("data.station_name").alias("station_name"),
        col("data.road_name").alias("road_name"),
        col("data.direction").alias("direction"),
        col("data.lat").alias("lat"),
        col("data.lon").alias("lon"),
        col("data.measurement_time").alias("measurement_time_raw"),
        to_timestamp(col("data.measurement_time")).alias("measurement_time"),
        col("data.period_minutes").alias("period_minutes"),
        col("data.vehicle_count").alias("vehicle_count"),
        col("data.avg_speed_kmh").alias("avg_speed_kmh"),
        col("data._metadata.ingested_at_utc").alias("ingested_at_utc"),
    )
    .filter(col("station_id").isNotNull())
    .withColumn(
        "traffic_factor",
        when(col("avg_speed_kmh").isNotNull() & (col("avg_speed_kmh") < 20), lit(1.50))
        .when(col("avg_speed_kmh").isNotNull() & (col("avg_speed_kmh") < 30), lit(1.30))
        .when(col("avg_speed_kmh").isNotNull() & (col("avg_speed_kmh") < 40), lit(1.15))
        .when(col("vehicle_count") >= 2000, lit(1.40))
        .when(col("vehicle_count") >= 1500, lit(1.25))
        .when(col("vehicle_count") >= 1000, lit(1.15))
        .otherwise(lit(1.00))
    )
    .withColumn(
        "congestion_level",
        when(col("traffic_factor") >= 1.40, lit("high"))
        .when(col("traffic_factor") >= 1.20, lit("medium"))
        .otherwise(lit("low"))
    )
    .withColumn("processed_at", current_timestamp())
)


def process_batch(batch_df, batch_id: int) -> None:
    if batch_df.isEmpty():
        print(f"Batch {batch_id}: brak nowych danych ruchu.")
        return

    batch_df = batch_df.dropDuplicates(["station_id", "measurement_time", "road_name", "direction"])
    batch_df.cache()

    total = batch_df.count()
    print(f"\n===== Batch {batch_id}: przetworzone rekordy ruchu: {total} =====")

    print("\nPodgląd danych ruchu:")
    batch_df.select(
        "station_id",
        "road_name",
        "direction",
        "measurement_time",
        "vehicle_count",
        "avg_speed_kmh",
        "traffic_factor",
        "congestion_level",
    ).show(50, truncate=False)

    print("\nRozkład congestion_level:")
    batch_df.groupBy("congestion_level").count().orderBy("congestion_level").show(truncate=False)

    (
        batch_df.write
        .mode("append")
        .parquet(OUTPUT_PATH)
    )

    print(f"Zapisano batch {batch_id} do: {OUTPUT_PATH}")

    batch_df.unpersist()


query = (
    traffic_rows.writeStream
    .foreachBatch(process_batch)
    .outputMode("append")
    .option("checkpointLocation", CHECKPOINT_PATH)
    .start()
)

print("Spark streaming started.")
print(f"Input topic: {RAW_TOPIC}")
print(f"Parquet output: {OUTPUT_PATH}")
print("Stop with Ctrl+C when the first batch is processed.\n")

query.awaitTermination()
