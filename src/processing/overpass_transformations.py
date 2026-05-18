from pyspark.sql import SparkSession
from pyspark.sql.functions import col, from_json, explode
from pyspark.sql.types import StructType, StructField, StringType, LongType, DoubleType, ArrayType

spark = SparkSession.builder \
    .appName("OverpassKafkaConsumer") \
    .config("spark.jars.packages", "org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.1") \
    .getOrCreate()

raw_stream = spark.readStream \
    .format("kafka") \
    .option("kafka.bootstrap.servers", "localhost:9092") \
    .option("subscribe", "warszawa-raw-streets") \
    .option("startingOffsets", "earliest") \
    .load()

# Rzutowanie wartości na String
json_stream = raw_stream.selectExpr("CAST(value AS STRING) as json_payload")

element_schema = StructType([
    StructField("type", StringType(), True),
    StructField("id", LongType(), True),
    StructField("lat", DoubleType(), True),
    StructField("lon", DoubleType(), True),
    StructField("nodes", ArrayType(LongType()), True), # dla linii (ways)
    StructField("tags", StringType(), True)            # uproszczone do Stringa na potrzeby demo
])

overpass_schema = StructType([
    StructField("elements", ArrayType(element_schema), True)
])

parsed_stream = json_stream \
    .withColumn("parsed", from_json(col("json_payload"), overpass_schema)) \
    .select(explode("parsed.elements").alias("element")) \
    .select("element.*")

nodes_df = parsed_stream.filter(col("type") == "node").select("id", "lat", "lon")
ways_df = parsed_stream.filter(col("type") == "way").select("id", "nodes", "tags")

query = ways_df.writeStream \
    .outputMode("append") \
    .format("console") \
    .start()

query.awaitTermination()