import json
import time
import requests
from confluent_kafka import Producer

KAFKA_BOOTSTRAP_SERVERS = 'localhost:9092'
KAFKA_TOPIC = 'warszawa-raw-streets'
STREET = "Warszawa"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

OVERPASS_QUERY = f"""
[out:json][timeout:120];
area["name"="{STREET}"]->.a;
(
  way["highway"~"^(primary|secondary|tertiary)$"](area.a);
);
(._;>;);
out body;
"""

HEADERS = {
    # some dummy user-agent, some german words added, because of server sensitivity
    "User-Agent": "opennav-neu-werk Kontak: opennav@freimail",
    "Accept": "application/json"
}

def delivery_report(err, msg):
    if err is not None:
        print(f"Message delivery error: {err}")
    else:
        print(f"Message delivered to topic: {msg.topic()} [Partition: {msg.partition()}]")

# główna logika
def main():
    conf = {
        'bootstrap.servers': KAFKA_BOOTSTRAP_SERVERS,
        'client.id': 'overpass-python-producer',
        'message.max.bytes': 20971520 # 20 MB
    }
    producer = Producer(conf)

    start_time = time.time()
    
    try:
        response = requests.get(OVERPASS_URL, params={'data': OVERPASS_QUERY}, headers=HEADERS)
        
        if response.status_code == 200:
            raw_data = response.json()
            elements_count = len(raw_data.get('elements', []))
            print(f" Sukces! Pobrano dane w czasie {time.time() - start_time:.2f}s. Liczba elementów: {elements_count}")
            
            json_payload = json.dumps(raw_data)
            
            print(f"2. Wysyłanie danych do Kafki (Temat: {KAFKA_TOPIC})...")
            
            producer.produce(
                topic=KAFKA_TOPIC, 
                key="Warszawa", 
                value=json_payload.encode('utf-8'), 
                callback=delivery_report
            )
            
            print("3. Oczekiwanie na potwierdzenie z klastra Kafki...")
            producer.flush()
            print(" Gotowe! Dane znajdują się w Kafce.")
            
        else:
            print(f"HTTP API Error {response.status_code}")
            print(response.text)
            
    except Exception as e:
        print(f"Unknown error occurred {e}")

if __name__ == "__main__":
    main()