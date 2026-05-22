import random
import json
import time
from datetime import datetime
import paho.mqtt.client as mqtt

# ─────────────────────────────────────────────────────────────
# Configuration MQTT
# ─────────────────────────────────────────────────────────────
BROKER     = "localhost"
PORT       = 1883
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

# ─────────────────────────────────────────────────────────────
# État persistant des capteurs
# ─────────────────────────────────────────────────────────────
states = {}
for uni in UNIVERSITIES:
    for spot in SPOTS:
        states[f"{uni}|{spot}"] = {
            "status":      random.choice(["free", "occupied"]),
            "battery":     random.randint(60, 100),
            "temperature": round(random.uniform(18, 28), 1),
            "confidence":  round(random.uniform(0.85, 0.99), 2),
        }

def generate_data(uni, spot):
    key     = f"{uni}|{spot}"
    current = states[key]

    # 20% de chance de changer de statut
    if random.random() < 0.20:
        current["status"] = "occupied" if current["status"] == "free" else "free"

    # Batterie diminue lentement (0-2% par cycle)
    current["battery"] = max(0, current["battery"] - random.randint(0, 2))
    if current["battery"] < 5 and random.random() < 0.15:
        current["battery"] = random.randint(70, 100)

    # Température varie légèrement
    current["temperature"] = round(
        max(15, min(35, current["temperature"] + random.uniform(-0.3, 0.3))), 1)

    # Confiance varie légèrement
    current["confidence"] = round(
        max(0.80, min(0.99, current["confidence"] + random.uniform(-0.01, 0.01))), 2)

    return {
        "spot_id":     spot,
        "university":  uni,          # ✅ Champ obligatoire pour app.py
        "status":      current["status"],
        "battery":     current["battery"],
        "temperature": current["temperature"],
        "confidence":  current["confidence"],
        "timestamp":   datetime.now().isoformat(),
        "source":      "simulator_advanced",
    }

def main():
    client = mqtt.Client()
    client.connect(BROKER, PORT)

    total = len(UNIVERSITIES) * len(SPOTS)
    print(f"✅ Simulateur avancé démarré")
    print(f"📡 Broker MQTT : {BROKER}:{PORT}")
    print(f"📭 Topic       : {BASE_TOPIC}/<university>/<spot>")
    print(f"🏫 {len(UNIVERSITIES)} universités × {len(SPOTS)} places = {total} capteurs")
    print("━" * 58)

    cycle = 0
    try:
        while True:
            cycle += 1
            print(f"\n🔄 Cycle {cycle} — {datetime.now().strftime('%H:%M:%S')}")

            for uni in UNIVERSITIES:
                for spot in SPOTS:
                    data  = generate_data(uni, spot)
                    topic = f"{BASE_TOPIC}/{uni.replace(' ', '_')}/{spot}"
                    client.publish(topic, json.dumps(data))

                    bat_icon = "⚠️" if data["battery"] < 20 else "🔋"
                    print(f"  📤 {uni[:28]:28} {spot} → "
                          f"{data['status']:8} {bat_icon}{data['battery']:3}% "
                          f"| {data['temperature']}°C")
                    time.sleep(0.05)

            print(f"\n⏳ Prochain cycle dans 8 s...")
            time.sleep(8)

    except KeyboardInterrupt:
        print("\n🛑 Simulateur arrêté")
    finally:
        client.disconnect()

if __name__ == "__main__":
    main()
