import sqlite3

conn = sqlite3.connect('parking.db')
c = conn.cursor()

c.execute("SELECT spot_id, status, updated_at FROM parking_spots WHERE university='EMSI'")
rows = c.fetchall()

print("Places EMSI dans la base :")
for row in rows:
    print(f"  {row[0]} → {row[1]} (dernière MAJ: {row[2]})")

conn.close()