"""
Transformacja danych pogodowych z Kafki do tabeli godzinowej.

Wejście:
  Kafka topic: warszawa-raw-weather

Wyjście:
  Parquet: /app/data/processed/weather_observations

Cel:
  Przygotowanie danych pogodowych jako dodatkowego, niejednorodnego źródła danych
  do późniejszej korekty kosztu przejazdu.
"""

from pyspark.sql import SparkSession
from pyspark.sql.functions import (
    arrays_zip,
    col,
    current_timestamp,
    explode,
    from_json,
    lit,
    to_timestamp,
    when,
)
from pyspark.sql.types import (
    ArrayType,
    DoubleType,
    LongType,
    StringType,
    StructField,
    StructType,
)


KAFKA_BOOTSTRAP_SERVERS = "localhost:9092"
RAW_TOPIC = "warszawa-raw-weather"

OUTPUT_PATH = "/app/data/processed/weather_observations"
CHECKPOINT_PATH = "/app/data/processed/checkpoints/weather_observations_stream"


spark = (
    SparkSession.builder
    .appName("WeatherKafkaTransformations")
    .config("spark.sql.shuffle.partitions", "4")
    .getOrCreate()
)

spark.sparkContext.setLogLevel("WARN")


hourly_schema = StructType([
    StructField("time", ArrayType(StringType()), True),
    StructField("temperature_2m", ArrayType(DoubleType()), True),
    StructField("precipitation", ArrayType(DoubleType()), True),
    StructField("rain", ArrayType(DoubleType()), True),
    StructField("snowfall", ArrayType(DoubleType()), True),
    StructField("wind_speed_10m", ArrayType(DoubleType()), True),
    StructField("visibility", ArrayType(DoubleType()), True),
    StructField("weather_code", ArrayType(LongType()), True),
])

metadata_schema = StructType([
    StructField("source", StringType(), True),
    StructField("city", StringType(), True),
    StructField("lat", DoubleType(), True),
    StructField("lon", DoubleType(), True),
    StructField("ingested_at_utc", StringType(), True),
])

weather_schema = StructType([
    StructField("latitude", DoubleType(), True),
    StructField("longitude", DoubleType(), True),
    StructField("timezone", StringType(), True),
    StructField("hourly", hourly_schema, True),
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

parsed = json_stream.withColumn("data", from_json(col("json_payload"), weather_schema))

# Smoke-testowe wiadomości nie mają pola hourly. Takie rekordy pomijamy.
weather_rows = (
    parsed
    .filter(col("data.hourly.time").isNotNull())
    .select(
        col("data._metadata.source").alias("source"),
        col("data._metadata.city").alias("city"),
        col("data._metadata.lat").alias("lat"),
        col("data._metadata.lon").alias("lon"),
        col("data._metadata.ingested_at_utc").alias("ingested_at_utc"),
        arrays_zip(
            col("data.hourly.time").alias("time"),
            col("data.hourly.temperature_2m").alias("temperature_2m"),
            col("data.hourly.precipitation").alias("precipitation"),
            col("data.hourly.rain").alias("rain"),
            col("data.hourly.snowfall").alias("snowfall"),
            col("data.hourly.wind_speed_10m").alias("wind_speed_10m"),
            col("data.hourly.visibility").alias("visibility"),
            col("data.hourly.weather_code").alias("weather_code"),
        ).alias("hourly_rows"),
    )
    .select(
        "source",
        "city",
        "lat",
        "lon",
        "ingested_at_utc",
        explode("hourly_rows").alias("row"),
    )
    .select(
        "source",
        "city",
        "lat",
        "lon",
        "ingested_at_utc",
        col("row.time").alias("time_raw"),
        to_timestamp(col("row.time")).alias("time"),
        col("row.temperature_2m").alias("temperature_2m"),
        col("row.precipitation").alias("precipitation_mm"),
        col("row.rain").alias("rain_mm"),
        col("row.snowfall").alias("snowfall_cm"),
        col("row.wind_speed_10m").alias("wind_speed_10m_kmh"),
        col("row.visibility").alias("visibility_m"),
        col("row.weather_code").alias("weather_code"),
    )
    .withColumn(
        "weather_factor",
        when(col("snowfall_cm") > 0, lit(1.20))
        .when(col("precipitation_mm") > 0, lit(1.10))
        .when(col("wind_speed_10m_kmh") >= 50, lit(1.10))
        .otherwise(lit(1.00))
    )
    .withColumn("processed_at", current_timestamp())
)


def process_batch(batch_df, batch_id: int) -> None:
    if batch_df.isEmpty():
        print(f"Batch {batch_id}: brak nowych danych pogodowych.")
        return

    batch_df = batch_df.dropDuplicates(["time"])
    batch_df.cache()

    total = batch_df.count()
    print(f"\n===== Batch {batch_id}: przetworzone rekordy pogodowe: {total} =====")

    print("\nPodgląd danych pogodowych:")
    batch_df.select(
        "time",
        "temperature_2m",
        "precipitation_mm",
        "snowfall_cm",
        "wind_speed_10m_kmh",
        "weather_factor",
    ).show(30, truncate=False)

    print("\nRozkład weather_factor:")
    batch_df.groupBy("weather_factor").count().orderBy("weather_factor").show(truncate=False)

    (
        batch_df.write
        .mode("append")
        .parquet(OUTPUT_PATH)
    )

    print(f"Zapisano batch {batch_id} do: {OUTPUT_PATH}")

    batch_df.unpersist()


query = (
    weather_rows.writeStream
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
