"""
Transformacja surowych danych Overpass API z Kafki do ujednoliconego modelu odcinków dróg.

Wejście:
  Kafka topic: warszawa-raw-streets

Wyjście:
  1) podgląd przetworzonych rekordów w konsoli,
  2) pliki Parquet w /app/data/processed/osm_ways,
  3) rekordy JSON w Kafka topic warszawa-processed-edges.
"""

from pyspark.sql import SparkSession
from pyspark.sql.functions import (
    col,
    current_timestamp,
    explode,
    from_json,
    length,
    lower,
    regexp_extract,
    size,
    struct,
    to_json,
    trim,
    when,
)
from pyspark.sql.types import (
    ArrayType,
    DoubleType,
    LongType,
    MapType,
    StringType,
    StructField,
    StructType,
)

KAFKA_BOOTSTRAP_SERVERS = "localhost:9092"
RAW_TOPIC = "warszawa-raw-streets"
PROCESSED_TOPIC = "warszawa-processed-edges"
OUTPUT_PATH = "/app/data/processed/osm_ways"
CHECKPOINT_PATH = "/app/data/processed/checkpoints/osm_ways_stream"


spark = (
    SparkSession.builder
    .appName("OverpassKafkaTransformations")
    .config("spark.sql.shuffle.partitions", "4")
    .getOrCreate()
)

spark.sparkContext.setLogLevel("WARN")


raw_stream = (
    spark.readStream
    .format("kafka")
    .option("kafka.bootstrap.servers", KAFKA_BOOTSTRAP_SERVERS)
    .option("subscribe", RAW_TOPIC)
    .option("startingOffsets", "earliest")
    .load()
)

json_stream = raw_stream.selectExpr("CAST(value AS STRING) AS json_payload")


element_schema = StructType([
    StructField("type", StringType(), True),
    StructField("id", LongType(), True),
    StructField("lat", DoubleType(), True),
    StructField("lon", DoubleType(), True),
    StructField("nodes", ArrayType(LongType()), True),
    StructField("tags", MapType(StringType(), StringType()), True),
])

overpass_schema = StructType([
    StructField("elements", ArrayType(element_schema), True),
])


parsed_stream = (
    json_stream
    .withColumn("parsed", from_json(col("json_payload"), overpass_schema))
    .select(explode("parsed.elements").alias("element"))
    .select("element.*")
)

ways = parsed_stream.filter(col("type") == "way")


ways_with_tags = (
    ways
    .withColumn("highway", col("tags").getItem("highway"))
    .withColumn("name", col("tags").getItem("name"))
    .withColumn("maxspeed_raw", col("tags").getItem("maxspeed"))
    .withColumn("lanes_raw", col("tags").getItem("lanes"))
    .withColumn("oneway_raw", col("tags").getItem("oneway"))
    .withColumn("bridge", col("tags").getItem("bridge"))
    .withColumn("tunnel", col("tags").getItem("tunnel"))
)

maxspeed_number = regexp_extract(col("maxspeed_raw"), r"(\d+)", 1).cast("double")
lanes_number = regexp_extract(col("lanes_raw"), r"(\d+)", 1).cast("int")


normalized_ways = (
    ways_with_tags
    .withColumn(
        "estimated_speed_kmh",
        when(maxspeed_number.isNotNull(), maxspeed_number)
        .when(col("highway") == "motorway", 100.0)
        .when(col("highway") == "trunk", 80.0)
        .when(col("highway") == "primary", 60.0)
        .when(col("highway") == "secondary", 50.0)
        .when(col("highway") == "tertiary", 40.0)
        .when(col("highway") == "residential", 30.0)
        .otherwise(30.0)
    )
    .withColumn(
        "lanes",
        when(lanes_number.isNotNull(), lanes_number).otherwise(1)
    )
    .withColumn(
        "oneway",
        when(lower(trim(col("oneway_raw"))).isin("yes", "true", "1"), True).otherwise(False)
    )
    .withColumn("node_count", size(col("nodes")))
    .withColumn("has_name", col("name").isNotNull() & (length(trim(col("name"))) > 0))
    .withColumn("has_maxspeed", col("maxspeed_raw").isNotNull())
    .withColumn("has_lanes", col("lanes_raw").isNotNull())
    .withColumn("processed_at", current_timestamp())
    .select(
        "id",
        "highway",
        "name",
        "nodes",
        "node_count",
        "maxspeed_raw",
        "estimated_speed_kmh",
        "lanes_raw",
        "lanes",
        "oneway_raw",
        "oneway",
        "bridge",
        "tunnel",
        "has_name",
        "has_maxspeed",
        "has_lanes",
        "processed_at",
    )
)


def process_batch(batch_df, batch_id: int) -> None:
    if batch_df.isEmpty():
        print(f"Batch {batch_id}: brak nowych danych.")
        return

    batch_df = batch_df.dropDuplicates(["id"])
    batch_df.cache()

    total = batch_df.count()
    print(f"\n===== Batch {batch_id}: przetworzone odcinki OSM: {total} =====")

    print("\nPodgląd znormalizowanych krawędzi:")
    batch_df.select(
        "id",
        "highway",
        "name",
        "estimated_speed_kmh",
        "lanes",
        "oneway",
        "node_count",
    ).show(20, truncate=40)

    print("\nStatystyka według typu drogi:")
    batch_df.groupBy("highway").count().orderBy(col("count").desc()).show(30, truncate=False)

    print("\nKompletność wybranych atrybutów:")
    batch_df.select("has_name", "has_maxspeed", "has_lanes").groupBy(
        "has_name", "has_maxspeed", "has_lanes"
    ).count().orderBy(col("count").desc()).show(20, truncate=False)

    (
        batch_df.write
        .mode("append")
        .parquet(OUTPUT_PATH)
    )

    (
        batch_df
        .select(
            col("id").cast("string").alias("key"),
            to_json(struct(*[col(c) for c in batch_df.columns])).alias("value"),
        )
        .write
        .format("kafka")
        .option("kafka.bootstrap.servers", KAFKA_BOOTSTRAP_SERVERS)
        .option("topic", PROCESSED_TOPIC)
        .save()
    )

    print(f"Zapisano batch {batch_id} do: {OUTPUT_PATH}")
    print(f"Opublikowano batch {batch_id} do topicu: {PROCESSED_TOPIC}")

    batch_df.unpersist()


query = (
    normalized_ways.writeStream
    .foreachBatch(process_batch)
    .outputMode("append")
    .option("checkpointLocation", CHECKPOINT_PATH)
    .start()
)

print("Spark streaming started.")
print(f"Input topic: {RAW_TOPIC}")
print(f"Processed topic: {PROCESSED_TOPIC}")
print(f"Parquet output: {OUTPUT_PATH}")
print("Stop with Ctrl+C when the first batch is processed.\n")

query.awaitTermination()
