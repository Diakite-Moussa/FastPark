import sqlite3

conn = sqlite3.connect('parking.db')
c = conn.cursor()

# Vérifier la réservation 19
c.execute("SELECT id, spot_id, user_id, status, expires_at, datetime('now') FROM reservations WHERE id=19")
res = c.fetchone()

if res:
    print(f"Réservation trouvée :")
    print(f"  ID: {res[0]}")
    print(f"  Place: {res[1]}")
    print(f"  User ID: {res[2]}")
    print(f"  Status: {res[3]}")
    print(f"  Expire le: {res[4]}")
    print(f"  Maintenant: {res[5]}")
    
    if res[4] > res[5]:
        print("✅ Réservation encore valide")
    else:
        print("❌ Réservation EXPIRÉE")
else:
    print("❌ Réservation 19 non trouvée")

conn.close()
