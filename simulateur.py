import random
import json
import time
from datetime import datetime
import paho.mqtt.client as mqtt

# ─────────────────────────────────────────────────────────────
# Configuration MQTT
# ─────────────────────────────────────────────────────────────
BROKER = "localhost"
PORT   = 1883
BASE_TOPIC = "fastpark/sensors"

# ─────────────────────────────────────────────────────────────
# Universités et places (identique à app.py)
# ─────────────────────────────────────────────────────────────
UNIVERSITIES = [
    "Université Hassan II - FST",
    "Université Hassan II - FSJES",
    "Université Hassan II - FLS",
    "Université Hassan II - FMPC",
    "ENCG Casablanca",
    "EMI Casablanca",
    "ENSA Casablanca",
    "Mundiapolis",
    "UIC",
    "ISCAE",
    "ESITH",
    "EMSI",
    "INPT",
    "Université Privée de Marrakech (Casa)"
]

SPOTS = ["A1", "A2", "A3"]

client = mqtt.Client()
client.connect(BROKER, PORT)

print(f"✅ Simulateur simple démarré")
print(f"📡 Broker : {BROKER}:{PORT}")
print(f"📭 Topic  : {BASE_TOPIC}/<university>/<spot>")
print(f"🏫 {len(UNIVERSITIES)} universités × {len(SPOTS)} places\n")

try:
    while True:
        # Choisir une université et une place au hasard
        uni   = random.choice(UNIVERSITIES)
        spot  = random.choice(SPOTS)
        status = random.choice(["occupied", "free"])

        payload = {
            "spot_id":     spot,
            "university":  uni,          # ✅ Champ obligatoire pour app.py
            "status":      status,
            "confidence":  round(random.uniform(0.85, 0.99), 2),
            "battery":     random.randint(20, 100),
            "temperature": round(random.uniform(18, 35), 1),
            "timestamp":   datetime.now().isoformat(),
            "source":      "simulator_simple"
        }

        topic = f"{BASE_TOPIC}/{uni.replace(' ', '_')}/{spot}"
        client.publish(topic, json.dumps(payload))
        print(f"📤 [{uni[:25]}] {spot} → {status}")

        time.sleep(3)

except KeyboardInterrupt:
    print("\n🛑 Simulateur arrêté")
    client.disconnect()
