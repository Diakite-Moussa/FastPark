from flask import Flask, jsonify, request, session, send_from_directory, Response
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from functools import wraps
import sqlite3
import bcrypt
import csv
import json
import threading
import time
import logging
import socket
from io import StringIO
from datetime import datetime, timedelta
from collections import deque
import os
import paho.mqtt.client as mqtt
import random
import qrcode
import secrets
from io import BytesIO
import base64

from mcp import mcp, FastParkMCP

DEMARRAGE_TIME = time.time()

# ─────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────

MQTT_BROKER = os.environ.get("MQTT_BROKER", "172.16.2.64")
MQTT_PORT   = int(os.environ.get("MQTT_PORT", 1883))
MQTT_TOPIC  = "fastpark/sensors/#"

_data_dir = "/app/data" if os.path.isdir("/app/data") else os.path.dirname(os.path.abspath(__file__))
DB_PATH   = os.path.join(_data_dir, "parking.db")
BASE_DIR  = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))

# ── CORS restreint en production ─────────────────────────────
ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "http://localhost:5000").split(",")
CORS(app, origins=ALLOWED_ORIGINS, supports_credentials=True)

# ── Rate Limiter ──────────────────────────────────────────────
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],          # pas de limite globale
    storage_uri="memory://"
)

# ── Logging : stdout + fichier si possible ───────────────────
_log_handlers = [logging.StreamHandler()]
try:
    _log_handlers.append(logging.FileHandler(os.path.join(_data_dir, "fastpark.log")))
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=_log_handlers
)
logger = logging.getLogger(__name__)
logger.info(f"DB_PATH = {DB_PATH}")

# ── Coordonnées GPS des universités (pour Google Maps) ───────
UNIVERSITY_GPS = {
    "Université Hassan II - FST":          {"lat": 33.5832, "lng": -7.5504},
    "Université Hassan II - FSJES":         {"lat": 33.5729, "lng": -7.6059},
    "Université Hassan II - FLS":           {"lat": 33.5831, "lng": -7.5498},
    "Université Hassan II - FMPC":          {"lat": 33.5920, "lng": -7.6216},
    "ENCG Casablanca":                      {"lat": 33.5925, "lng": -7.6217},
    "EMI Casablanca":                       {"lat": 33.9842, "lng": -6.8519},
    "ENSA Casablanca":                      {"lat": 33.5296, "lng": -7.6636},
    "Mundiapolis":                          {"lat": 33.5228, "lng": -7.6836},
    "UIC":                                  {"lat": 33.5614, "lng": -7.6335},
    "ISCAE":                                {"lat": 33.5921, "lng": -7.6203},
    "ESITH":                                {"lat": 33.5476, "lng": -7.6176},
    "EMSI":                                 {"lat": 33.5891, "lng": -7.6156},
    "INPT":                                 {"lat": 33.9965, "lng": -6.8479},
    "Université Privée de Marrakech (Casa)":{"lat": 33.5344, "lng": -7.6614},
}

# ── Injecter DB_PATH dans l'instance MCP (chemin absolu) ────
mcp.db_path = DB_PATH

# ── Buffer MQTT ──────────────────────────────────────────────
mqtt_buffer    = deque(maxlen=1000)
mqtt_available = True

# ─────────────────────────────────────────────────────────────
# Universités (14 universités, 3 places chacune)
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
# Helpers bcrypt
# ─────────────────────────────────────────────────────────────
def hash_password(password: str) -> str:
    """Génère un hash bcrypt du mot de passe."""
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def check_password(password: str, stored_hash: str) -> bool:
    """Vérifie un mot de passe contre son hash bcrypt (compatible aussi SHA-256 legacy)."""
    try:
        # Tentative bcrypt
        return bcrypt.checkpw(password.encode('utf-8'), stored_hash.encode('utf-8'))
    except Exception:
        # Fallback SHA-256 pour les anciens comptes (migration transparente)
        import hashlib
        sha_hash = hashlib.sha256(password.encode()).hexdigest()
        return sha_hash == stored_hash

# ─────────────────────────────────────────────────────────────
# Décorateur d'authentification
# ─────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return jsonify({"error": "Non autorisé"}), 401
        return f(*args, **kwargs)
    return decorated

# ─────────────────────────────────────────────────────────────
# Base de données — SANS destruction des données existantes
# ─────────────────────────────────────────────────────────────
def init_db():
    """
    Initialise les tables si elles n'existent pas (CREATE TABLE IF NOT EXISTS).
    N'efface jamais les données existantes.
    Insère les places manquantes uniquement (INSERT OR IGNORE).
    Crée le compte admin uniquement s'il n'existe pas (INSERT OR IGNORE).
    """
    conn = sqlite3.connect(DB_PATH)
    c    = conn.cursor()

    # ── Création des tables (idempotent) ────────────────────
    c.execute('''CREATE TABLE IF NOT EXISTS parking_spots (
        spot_id     TEXT,
        university  TEXT,
        status      TEXT,
        battery     INTEGER,
        temperature REAL,
        confidence  REAL,
        updated_at  TEXT,
        source      TEXT,
        PRIMARY KEY (spot_id, university)
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        username      TEXT UNIQUE NOT NULL,
        email         TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        university    TEXT NOT NULL,
        role          TEXT DEFAULT 'user',
        created_at    TEXT DEFAULT CURRENT_TIMESTAMP
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS parking_history (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        spot_id    TEXT,
        university TEXT,
        status     TEXT,
        changed_at TEXT
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS reservations (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        spot_id     TEXT,
        university  TEXT,
        user_id     INTEGER,
        username    TEXT,
        reserved_at TEXT,
        expires_at  TEXT,
        status      TEXT DEFAULT 'active'
    )''')

    # ── Table historique chat (Bug 3 fix) ────────────────────
    c.execute('''CREATE TABLE IF NOT EXISTS chat_history (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER NOT NULL,
        role       TEXT NOT NULL,
        message    TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )''')

    # ── Table historique checkin (Bug 3 fix) ─────────────────
    c.execute('''CREATE TABLE IF NOT EXISTS checkin_log (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        reservation_id INTEGER,
        spot_id        TEXT,
        university     TEXT,
        username       TEXT,
        validated_by   TEXT,
        validated_at   TEXT DEFAULT CURRENT_TIMESTAMP
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS qr_codes (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        reservation_id INTEGER,
        code           TEXT UNIQUE NOT NULL,
        used           INTEGER DEFAULT 0,
        used_at        TEXT,
        created_at     TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (reservation_id) REFERENCES reservations(id)
    )''')

    # ── Insertion des places manquantes seulement ────────────
    inserted = 0
    for uni in UNIVERSITIES:
        for spot in SPOTS:
            c.execute('''INSERT OR IGNORE INTO parking_spots
                (spot_id, university, status, battery, temperature, confidence, updated_at, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                (spot, uni, "free",
                 random.randint(60, 100),
                 round(random.uniform(18, 25), 1),
                 0.95,
                 datetime.now().isoformat(),
                 "system"))
            inserted += c.rowcount

    # ── Compte admin — créé une seule fois, jamais écrasé ───
    # Vérifie si un admin existe déjà
    c.execute("SELECT id, password_hash FROM users WHERE username = 'admin'")
    existing_admin = c.fetchone()

    if not existing_admin:
        # Premier lancement : crée l'admin avec bcrypt
        admin_password = os.environ.get("ADMIN_PASSWORD", "fastpark123")
        admin_hash = hash_password(admin_password)
        c.execute('''INSERT INTO users (username, email, password_hash, university, role)
            VALUES (?, ?, ?, ?, ?)''',
            ("admin", "admin@fastpark.ma", admin_hash, "all", "admin"))
        logger.info("Compte admin créé (bcrypt). Changez le mot de passe via ADMIN_PASSWORD.")
    else:
        # Admin existant : migrer SHA-256 → bcrypt si nécessaire
        stored = existing_admin[1]
        if not stored.startswith("$2b$") and not stored.startswith("$2a$"):
            # Hash SHA-256 détecté → migration silencieuse vers bcrypt
            # On ne connaît pas le mdp en clair, on le migrera à la prochaine connexion
            logger.info("Admin existant en SHA-256 : sera migré vers bcrypt à la prochaine connexion.")

    conn.commit()
    conn.close()

    total_spots = len(UNIVERSITIES) * len(SPOTS)
    logger.info(
        f"DB prête : {len(UNIVERSITIES)} univ × {len(SPOTS)} places = {total_spots} capteurs "
        f"({inserted} nouvelles places ajoutées)"
    )

# ─────────────────────────────────────────────────────────────
# Sauvegarde MQTT → DB
# ─────────────────────────────────────────────────────────────
def save_to_db(data):
    try:
        conn = sqlite3.connect(DB_PATH)
        c    = conn.cursor()

        spot_id     = data.get('spot_id')
        uni         = data.get('university')
        status      = data.get('status')
        battery     = data.get('battery')
        temperature = data.get('temperature')
        confidence  = data.get('confidence', 0.95)
        source      = data.get('source', 'mqtt')
        timestamp   = data.get('timestamp', datetime.now().isoformat())

        if not spot_id or not uni or not status:
            conn.close()
            return

        # Ne pas écraser une place réservée avec "free"
        c.execute("SELECT status FROM parking_spots WHERE spot_id=? AND university=?", (spot_id, uni))
        current = c.fetchone()
        if current and current[0] == 'reserved' and status == 'free':
            conn.close()
            return

        c.execute('''UPDATE parking_spots
            SET status=?, battery=?, temperature=?, confidence=?, updated_at=?, source=?
            WHERE spot_id=? AND university=?''',
            (status, battery, temperature, confidence, timestamp, source, spot_id, uni))

        # Historique si changement de statut
        if current and current[0] != status and status != 'reserved':
            c.execute('''INSERT INTO parking_history (spot_id, university, status, changed_at)
                         VALUES (?, ?, ?, ?)''', (spot_id, uni, status, timestamp))

        conn.commit()
        conn.close()
        logger.info(f"DB: {uni[:20]}... {spot_id} → {status} (🔋{battery}%)")

    except Exception as e:
        logger.error(f"Erreur DB: {e}")

# ─────────────────────────────────────────────────────────────
# MQTT Bridge
# ─────────────────────────────────────────────────────────────
def check_mqtt_broker():
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        sock.connect((MQTT_BROKER, MQTT_PORT))
        sock.close()
        return True
    except:
        return False

def on_connect(client, userdata, flags, rc):
    global mqtt_available
    if rc == 0:
        mqtt_available = True
        logger.info(f"MQTT connecté ({MQTT_BROKER}:{MQTT_PORT})")
        client.subscribe(MQTT_TOPIC)
    else:
        mqtt_available = False
        logger.error(f"MQTT erreur code {rc}")

def on_message(client, userdata, msg):
    try:
        payload = msg.payload.decode('utf-8')
        data    = json.loads(payload)

        if time.time() - DEMARRAGE_TIME < 5 and data.get('status') == 'occupied':
            return

        if 'spot_id' not in data or 'university' not in data or 'status' not in data:
            logger.warning(f"Message MQTT ignoré (champs manquants) sur {msg.topic}")
            return

        if 'timestamp' not in data:
            data['timestamp'] = datetime.now().isoformat()

        save_to_db(data)

    except json.JSONDecodeError:
        pass
    except Exception as e:
        logger.error(f"MQTT erreur message: {e}")

def start_mqtt_bridge():
    global mqtt_available
    while True:
        try:
            client = mqtt.Client()
            client.on_connect = on_connect
            client.on_message = on_message
            client.connect(MQTT_BROKER, MQTT_PORT, 60)
            logger.info("MQTT Bridge démarré")
            client.loop_forever()
        except Exception as e:
            mqtt_available = False
            logger.error(f"MQTT indisponible ({MQTT_BROKER}): {e} — tentative dans 10s")
            time.sleep(10)

def flush_mqtt_buffer():
    while True:
        time.sleep(5)
        if mqtt_available and mqtt_buffer:
            logger.info(f"Vidage buffer MQTT: {len(mqtt_buffer)} messages")
            mqtt_buffer.clear()

# ─────────────────────────────────────────────────────────────
# Nettoyage des réservations expirées
# ─────────────────────────────────────────────────────────────
def clean_expired_loop():
    while True:
        time.sleep(30)
        try:
            conn = sqlite3.connect(DB_PATH)
            c    = conn.cursor()
            c.execute('''SELECT spot_id, university FROM reservations
                         WHERE status='active' AND expires_at <= datetime('now')''')
            expired = c.fetchall()
            for spot_id, university in expired:
                c.execute('''UPDATE parking_spots SET status='free', updated_at=?
                             WHERE spot_id=? AND university=?''',
                          (datetime.now().isoformat(), spot_id, university))
                logger.info(f"Expiration: {spot_id} - {university[:20]}... libérée")
            c.execute('''UPDATE reservations SET status='expired'
                         WHERE status='active' AND expires_at <= datetime('now')''')
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Erreur nettoyage: {e}")

# ─────────────────────────────────────────────────────────────
# Routes HTML
# ─────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return send_from_directory(BASE_DIR, 'login.html')

@app.route('/dashboard')
def dashboard():
    return send_from_directory(BASE_DIR, 'dashboard.html')

@app.route('/register')
def register_page():
    return send_from_directory(BASE_DIR, 'register.html')

@app.route('/health')
def health_page():
    return send_from_directory(BASE_DIR, 'admin_health.html')

@app.route('/install')
def install_page():
    return send_from_directory(BASE_DIR, 'install.html')

@app.route('/manifest.json')
def manifest():
    return send_from_directory(BASE_DIR, 'manifest.json')

@app.route('/sw.js')
def service_worker():
    return send_from_directory(BASE_DIR, 'sw.js', mimetype='application/javascript')

@app.route('/static/<path:filename>')
def static_files(filename):
    return send_from_directory(os.path.join(BASE_DIR, 'static'), filename)

@app.route('/checkin')
def checkin_page():
    return send_from_directory(BASE_DIR, 'checkin.html')

# ─────────────────────────────────────────────────────────────
# API Auth
# ─────────────────────────────────────────────────────────────
@app.route('/api/login', methods=['POST'])
@limiter.limit("5 per minute")
def api_login():
    data     = request.get_json() or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')

    # Validation basique
    if not username or not password:
        return jsonify({"success": False, "message": "Identifiants manquants"}), 400

    conn = sqlite3.connect(DB_PATH)
    c    = conn.cursor()
    c.execute('SELECT id, username, university, role, password_hash FROM users WHERE username=?', (username,))
    user = c.fetchone()

    if not user or not check_password(password, user[4]):
        conn.close()
        logger.warning(f"Échec connexion: {username}")
        return jsonify({"success": False, "message": "Identifiants incorrects"}), 401

    # Migration transparente SHA-256 → bcrypt à la connexion
    stored_hash = user[4]
    if not stored_hash.startswith("$2b$") and not stored_hash.startswith("$2a$"):
        new_hash = hash_password(password)
        c.execute('UPDATE users SET password_hash=? WHERE id=?', (new_hash, user[0]))
        conn.commit()
        logger.info(f"Mot de passe de {username} migré SHA-256 → bcrypt")

    conn.close()

    session.clear()
    session.update({
        'logged_in': True,
        'user_id':   user[0],
        'username':  user[1],
        'university':user[2],
        'role':      user[3]
    })
    logger.info(f"Connexion: {username} ({user[3]})")
    return jsonify({"success": True, "role": user[3], "username": user[1], "university": user[2]})


@app.route('/api/register', methods=['POST'])
@limiter.limit("5 per minute")
def api_register():
    import re
    data       = request.get_json() or {}
    username   = data.get('username', '').strip()
    email      = data.get('email', '').strip()
    password   = data.get('password', '')
    university = data.get('university', '').strip()

    # ── Validation username ──────────────────────────────────
    if not username:
        return jsonify({"success": False, "message": "Nom d'utilisateur requis"}), 400
    if len(username) < 3 or len(username) > 30:
        return jsonify({"success": False, "message": "Le nom d'utilisateur doit faire entre 3 et 30 caractères"}), 400
    if not re.match(r'^[a-zA-Z0-9_-]+$', username):
        return jsonify({"success": False, "message": "Le nom d'utilisateur ne peut contenir que des lettres, chiffres, _ et -"}), 400

    # ── Validation email ─────────────────────────────────────
    if not email:
        return jsonify({"success": False, "message": "Email requis"}), 400
    if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email):
        return jsonify({"success": False, "message": "Format d'email invalide"}), 400
    if len(email) > 100:
        return jsonify({"success": False, "message": "Email trop long"}), 400

    # ── Validation mot de passe ──────────────────────────────
    if not password:
        return jsonify({"success": False, "message": "Mot de passe requis"}), 400
    if len(password) < 8:
        return jsonify({"success": False, "message": "Le mot de passe doit contenir au moins 8 caractères"}), 400

    # ── Validation université ────────────────────────────────
    if not university or university not in UNIVERSITIES:
        return jsonify({"success": False, "message": "Université non valide"}), 400

    # Hash bcrypt
    password_hash = hash_password(password)
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute('''INSERT INTO users (username, email, password_hash, university, role)
                        VALUES (?, ?, ?, ?, 'user')''',
                     (username, email, password_hash, university))
        conn.commit()
        conn.close()
        logger.info(f"Nouvel utilisateur inscrit: {username}")
        return jsonify({"success": True, "message": "Inscription réussie"})
    except sqlite3.IntegrityError:
        return jsonify({"success": False, "message": "Nom d'utilisateur ou email déjà utilisé"}), 400


# Gestion propre des erreurs de rate limit
@app.errorhandler(429)
def ratelimit_handler(e):
    return jsonify({
        "success": False,
        "message": "Trop de tentatives. Veuillez patienter une minute avant de réessayer."
    }), 429
@app.route('/api/logout', methods=['POST'])
def api_logout():
    logger.info(f"Déconnexion: {session.get('username')}")
    session.clear()
    return jsonify({"success": True})

@app.route('/api/check_auth', methods=['GET'])
def check_auth():
    return jsonify({
        "logged_in": session.get('logged_in', False),
        "username":  session.get('username'),
        "role":      session.get('role'),
        "university":session.get('university')
    })

# ─────────────────────────────────────────────────────────────
# API Health
# ─────────────────────────────────────────────────────────────
@app.route('/api/health', methods=['GET'])
def health_check():
    try:
        conn = sqlite3.connect(DB_PATH)
        c    = conn.cursor()
        c.execute("SELECT MAX(updated_at) FROM parking_spots")
        last_update_str = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM parking_spots")
        total_spots = c.fetchone()[0]
        conn.close()

        data_fresh = False
        if last_update_str:
            try:
                lu = datetime.fromisoformat(last_update_str)
                data_fresh = (datetime.now() - lu).seconds < 300
            except:
                pass

        return jsonify({
            "status":           "healthy" if data_fresh else "warning",
            "last_data_update": last_update_str,
            "data_fresh":       data_fresh,
            "mqtt_broker":      "connected" if check_mqtt_broker() else "disconnected",
            "mqtt_host":        MQTT_BROKER,
            "total_spots":      total_spots,
            "timestamp":        datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({"status": "unhealthy", "error": str(e)}), 500

# ─────────────────────────────────────────────────────────────
# API Réservations
# ─────────────────────────────────────────────────────────────
@app.route('/api/reserve', methods=['POST'])
@login_required
def reserve_spot():
    data = request.get_json() or {}
    spot_id = data.get('spot_id')
    university = data.get('university')
    user_id = session.get('user_id')
    username = session.get('username')

    if not spot_id or not university:
        return jsonify({"success": False, "message": "Données manquantes"}), 400

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute('''SELECT id, spot_id, expires_at FROM reservations
                 WHERE user_id = ? AND status = 'active' AND expires_at > datetime('now')''', (user_id,))
    existing = c.fetchone()
    if existing:
        conn.close()
        return jsonify({"success": False,
                        "message": f"Vous avez déjà une réservation active pour la place {existing[1]}"}), 400

    c.execute('''SELECT id FROM reservations
                 WHERE spot_id = ? AND university = ? AND status = 'active' AND expires_at > datetime('now')''',
              (spot_id, university))
    if c.fetchone():
        conn.close()
        return jsonify({"success": False, "message": "Cette place est déjà réservée"}), 400

    c.execute("SELECT status FROM parking_spots WHERE spot_id = ? AND university = ?", (spot_id, university))
    current = c.fetchone()
    if current and current[0] == 'occupied':
        conn.close()
        return jsonify({"success": False, "message": "Cette place est déjà occupée"}), 400

    expires_dt = datetime.now() + timedelta(minutes=10)
    expires_at_str = expires_dt.isoformat()

    c.execute('''INSERT INTO reservations (spot_id, university, user_id, username, reserved_at, expires_at, status)
                 VALUES (?, ?, ?, ?, ?, ?, 'active')''',
              (spot_id, university, user_id, username, datetime.now().isoformat(), expires_at_str))
    reservation_id = c.lastrowid

    qr_token = secrets.token_urlsafe(32)
    qr_code_data = f"fastpark://checkin?res={reservation_id}&token={qr_token}"
    c.execute('INSERT INTO qr_codes (reservation_id, code) VALUES (?, ?)', (reservation_id, qr_token))

    c.execute('''UPDATE parking_spots SET status = 'reserved', updated_at = ?
                 WHERE spot_id = ? AND university = ?''',
              (datetime.now().isoformat(), spot_id, university))
    conn.commit()
    conn.close()

    qr = qrcode.QRCode(box_size=8, border=2)
    qr.add_data(qr_code_data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#667eea", back_color="white")
    buffered = BytesIO()
    img.save(buffered, format="PNG")
    qr_base64 = base64.b64encode(buffered.getvalue()).decode()

    logger.info(f"Réservation: {username} a réservé {spot_id} de {university[:20]}...")
    return jsonify({
        "success": True,
        "message": f"Place {spot_id} réservée pour 10 minutes",
        "expires_at": expires_at_str,
        "qr_code": qr_base64,
        "reservation_id": reservation_id
    })

@app.route('/api/cancel_reservation', methods=['POST'])
@login_required
def cancel_reservation():
    data       = request.get_json() or {}
    spot_id    = data.get('spot_id')
    university = data.get('university')
    user_id    = session.get('user_id')

    conn = sqlite3.connect(DB_PATH)
    c    = conn.cursor()
    c.execute('''UPDATE reservations SET status='cancelled'
                 WHERE spot_id=? AND university=? AND user_id=? AND status='active' ''',
              (spot_id, university, user_id))
    c.execute('''UPDATE parking_spots SET status='free', updated_at=?
                 WHERE spot_id=? AND university=?''',
              (datetime.now().isoformat(), spot_id, university))
    conn.commit()
    conn.close()
    return jsonify({"success": True, "message": "Réservation annulée"})

@app.route('/api/my_reservation', methods=['GET'])
@login_required
def my_reservation():
    user_id = session.get('user_id')
    conn    = sqlite3.connect(DB_PATH)
    c       = conn.cursor()
    c.execute('''SELECT spot_id, university, reserved_at, expires_at FROM reservations
                 WHERE user_id=? AND status='active' AND expires_at > datetime('now')''', (user_id,))
    r = c.fetchone()
    conn.close()
    if r:
        return jsonify({"has_reservation": True, "spot_id": r[0], "university": r[1],
                        "reserved_at": r[2], "expires_at": r[3]})
    return jsonify({"has_reservation": False})

# ─────────────────────────────────────────────────────────────
# API Places & Stats
# ─────────────────────────────────────────────────────────────
@app.route('/api/spots', methods=['GET'])
@login_required
def get_spots():
    user_uni = session.get('university')
    is_admin = session.get('role') == 'admin'

    conn = sqlite3.connect(DB_PATH)
    c    = conn.cursor()
    if is_admin:
        c.execute('SELECT spot_id, university, status, battery, temperature, confidence, updated_at, source FROM parking_spots ORDER BY university, spot_id')
    else:
        c.execute('SELECT spot_id, university, status, battery, temperature, confidence, updated_at, source FROM parking_spots WHERE university=? ORDER BY spot_id', (user_uni,))
    rows = c.fetchall()
    conn.close()

    return jsonify([{
        "spot_id": r[0], "university": r[1], "status": r[2],
        "battery": r[3], "temperature": r[4], "confidence": r[5],
        "updated_at": r[6], "source": r[7]
    } for r in rows])

@app.route('/api/stats', methods=['GET'])
@login_required
def get_stats():
    user_uni = session.get('university')
    is_admin = session.get('role') == 'admin'

    conn = sqlite3.connect(DB_PATH)
    c    = conn.cursor()
    if is_admin:
        c.execute("SELECT COUNT(*) FROM parking_spots")
        total = c.fetchone()[0]
        c.execute("SELECT status, COUNT(*) FROM parking_spots GROUP BY status")
        sc = {r[0]: r[1] for r in c.fetchall()}
        c.execute("SELECT AVG(battery) FROM parking_spots WHERE battery > 0")
        avg_battery = c.fetchone()[0] or 0
    else:
        c.execute("SELECT COUNT(*) FROM parking_spots WHERE university=?", (user_uni,))
        total = c.fetchone()[0]
        c.execute("SELECT status, COUNT(*) FROM parking_spots WHERE university=? GROUP BY status", (user_uni,))
        sc = {r[0]: r[1] for r in c.fetchall()}
        c.execute("SELECT AVG(battery) FROM parking_spots WHERE university=? AND battery > 0", (user_uni,))
        avg_battery = c.fetchone()[0] or 0
    conn.close()

    free     = sc.get("free", 0)
    occupied = sc.get("occupied", 0)
    reserved = sc.get("reserved", 0)
    occ_rate = round(((occupied + reserved) / total) * 100, 1) if total else 0

    return jsonify({
        "total_spots":         total,
        "occupied":            occupied,
        "free":                free,
        "reserved":            reserved,
        "avg_battery_percent": round(avg_battery, 1),
        "occupancy_rate":      occ_rate,
        "last_update":         datetime.now().isoformat()
    })

# ─────────────────────────────────────────────────────────────
# API Export CSV
# ─────────────────────────────────────────────────────────────
@app.route('/api/export/csv', methods=['GET'])
@login_required
def export_csv():
    user_uni = session.get('university')
    is_admin = session.get('role') == 'admin'

    conn = sqlite3.connect(DB_PATH)
    c    = conn.cursor()
    if is_admin:
        c.execute("SELECT spot_id, university, status, battery, temperature, updated_at FROM parking_spots")
    else:
        c.execute("SELECT spot_id, university, status, battery, temperature, updated_at FROM parking_spots WHERE university=?", (user_uni,))
    rows = c.fetchall()
    conn.close()

    out = StringIO()
    w   = csv.writer(out)
    w.writerow(['spot_id', 'university', 'status', 'battery(%)', 'temperature(°C)', 'last_update'])
    for r in rows:
        w.writerow(r)
    out.seek(0)
    return Response(out.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': 'attachment; filename=parking_export.csv'})

# ─────────────────────────────────────────────────────────────
# API MCP : Chat, Rapport, Prédiction
# ─────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────
# API coordonnées GPS des universités (pour Google Maps)
# ─────────────────────────────────────────────────────────────
@app.route('/api/university_gps', methods=['GET'])
def university_gps():
    """Retourne les coordonnées GPS de toutes les universités."""
    return jsonify(UNIVERSITY_GPS)

# ─────────────────────────────────────────────────────────────
# API historique check-ins (Bug 3 fix — plus de localStorage)
# ─────────────────────────────────────────────────────────────
@app.route('/api/checkin_history', methods=['GET'])
@login_required
def get_checkin_history():
    """Retourne les 20 derniers check-ins de la journée pour l'agent."""
    conn = sqlite3.connect(DB_PATH)
    c    = conn.cursor()
    c.execute('''SELECT spot_id, university, username, validated_by, validated_at
                 FROM checkin_log
                 WHERE date(validated_at) = date('now')
                 ORDER BY validated_at DESC
                 LIMIT 20''')
    rows = c.fetchall()
    conn.close()
    return jsonify([{
        "spot_id":      r[0],
        "university":   r[1],
        "username":     r[2],
        "validated_by": r[3],
        "time":         r[4],
    } for r in rows])

@app.route('/api/chat', methods=['POST'])
@login_required
def chat():
    data       = request.get_json() or {}
    question   = data.get('question', '')
    university = session.get('university') if session.get('role') != 'admin' else data.get('university', 'all')
    user_id    = session.get('user_id')
    if not question:
        return jsonify({"error": "Question vide"}), 400

    response_text = mcp.ask(question, university)

    # Sauvegarder l'échange en DB (Bug 3 fix — plus de localStorage)
    try:
        conn = sqlite3.connect(DB_PATH)
        c    = conn.cursor()
        c.execute("INSERT INTO chat_history (user_id, role, message) VALUES (?, 'user', ?)",
                  (user_id, question[:1000]))
        c.execute("INSERT INTO chat_history (user_id, role, message) VALUES (?, 'assistant', ?)",
                  (user_id, response_text[:2000]))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"Erreur sauvegarde chat: {e}")

    return jsonify({"response": response_text})

@app.route('/api/chat_history', methods=['GET'])
@login_required
def get_chat_history():
    """Retourne les 20 derniers messages du chat pour l'utilisateur courant."""
    user_id = session.get('user_id')
    conn    = sqlite3.connect(DB_PATH)
    c       = conn.cursor()
    c.execute('''SELECT role, message, created_at
                 FROM chat_history
                 WHERE user_id = ?
                 ORDER BY created_at DESC
                 LIMIT 20''', (user_id,))
    rows = list(reversed(c.fetchall()))
    conn.close()
    return jsonify([{"role": r[0], "message": r[1], "time": r[2]} for r in rows])

@app.route('/api/report', methods=['GET'])
@login_required
def generate_report():
    university = session.get('university') if session.get('role') != 'admin' else 'all'
    return jsonify({"report": mcp.generate_report(university)})

@app.route('/api/predict', methods=['GET'])
@login_required
def predict():
    hours      = request.args.get('hours', 1, type=int)
    university = session.get('university') if session.get('role') != 'admin' else 'all'
    return jsonify({"prediction": mcp.predict_occupation(hours, university), "hours": hours})

# ─────────────────────────────────────────────────────────────
# API Check-in QR
# ─────────────────────────────────────────────────────────────
@app.route('/api/validate_checkin', methods=['POST'])
@login_required
def validate_checkin():
    data           = request.get_json() or {}
    qr_token       = data.get('token')
    reservation_id = data.get('reservation_id')
    user_id        = session.get('user_id')
    username       = session.get('username')
    is_admin       = session.get('role') == 'admin'

    if not reservation_id:
        return jsonify({"success": False, "message": "ID de réservation manquant"}), 400

    conn = sqlite3.connect(DB_PATH)
    c    = conn.cursor()

    if qr_token and qr_token != str(reservation_id):
        c.execute('''SELECT id FROM qr_codes
                     WHERE reservation_id = ? AND code = ? AND used = 0''',
                  (reservation_id, qr_token))
        if not c.fetchone():
            conn.close()
            return jsonify({"success": False, "message": "QR code invalide ou déjà utilisé"}), 400

    if is_admin:
        c.execute('''SELECT spot_id, university, expires_at, status FROM reservations
                     WHERE id = ? AND status = 'active' AND expires_at > datetime('now')''', (reservation_id,))
    else:
        c.execute('''SELECT spot_id, university, expires_at, status FROM reservations
                     WHERE id = ? AND user_id = ? AND status = 'active' AND expires_at > datetime('now')''',
                  (reservation_id, user_id))

    reservation = c.fetchone()
    if not reservation:
        conn.close()
        return jsonify({"success": False, "message": "Réservation invalide ou expirée"}), 400

    spot_id, university, expires_at, status = reservation

    if not qr_token or qr_token == str(reservation_id):
        c.execute('INSERT OR IGNORE INTO qr_codes (reservation_id, code, used) VALUES (?, ?, 0)',
                  (reservation_id, str(reservation_id)))
        conn.commit()

    c.execute("UPDATE reservations SET status = 'checked_in' WHERE id = ?", (reservation_id,))
    c.execute('''UPDATE parking_spots SET status = 'occupied', updated_at = ?
                 WHERE spot_id = ? AND university = ?''',
              (datetime.now().isoformat(), spot_id, university))
    c.execute('''UPDATE qr_codes SET used = 1, used_at = ?
                 WHERE reservation_id = ? AND used = 0''',
              (datetime.now().isoformat(), reservation_id))

    # Récupérer le nom du client depuis la table reservations
    c.execute('SELECT username FROM reservations WHERE id=?', (reservation_id,))
    res_row = c.fetchone()
    client_username = res_row[0] if res_row else username

    # Enregistrer le check-in en DB (Bug 3 fix — plus de localStorage)
    validator = session.get('username', 'agent')
    c.execute('''INSERT INTO checkin_log (reservation_id, spot_id, university, username, validated_by)
                 VALUES (?, ?, ?, ?, ?)''',
              (reservation_id, spot_id, university, client_username, validator))

    conn.commit()
    conn.close()
    logger.info(f"Check-in validé: {username} → place {spot_id}")
    return jsonify({"success": True,
                    "message": f"Bienvenue ! Place {spot_id} est maintenant occupée",
                    "spot_id": spot_id})

@app.route('/api/reservation_details', methods=['GET'])
@login_required
def reservation_details():
    res_id = request.args.get('res_id')
    if not res_id:
        return jsonify({"error": "ID requis"}), 400

    conn = sqlite3.connect(DB_PATH)
    c    = conn.cursor()
    c.execute('''SELECT r.spot_id, r.university, u.username, u.university
                 FROM reservations r JOIN users u ON r.user_id = u.id
                 WHERE r.id = ?''', (res_id,))
    result = c.fetchone()
    conn.close()

    if result:
        return jsonify({"spot_id": result[0], "university": result[1],
                        "username": result[2], "user_university": result[3]})
    return jsonify({"error": "Réservation non trouvée"}), 404

# ─────────────────────────────────────────────────────────────
# Lancement
# ─────────────────────────────────────────────────────────────
if __name__ == '__main__':
    init_db()

    # Threads
    threading.Thread(target=start_mqtt_bridge,  daemon=True).start()
    threading.Thread(target=clean_expired_loop, daemon=True).start()
    threading.Thread(target=flush_mqtt_buffer,  daemon=True).start()

    # Port dynamique (Cloud) ou 5000 par défaut (local)
    port = int(os.environ.get("PORT", 5000))

    print("\n" + "═" * 55)
    print("  FastPark multi-universités démarré !")
    print("═" * 55)
    print(f"  Dashboard  →  http://localhost:{port}")
    print(f"  MQTT       →  {MQTT_BROKER}:{MQTT_PORT}")
    print(f"  Inscription→  http://localhost:{port}/register")
    print(f"  Monitoring →  http://localhost:{port}/health")
    print("═" * 55)
    print("  Mot de passe admin : voir variable ADMIN_PASSWORD")
    print("  (par défaut : fastpark123 — à changer en production)")
    print("═" * 55)

    cert_file = os.path.join(BASE_DIR, 'server.crt')
    key_file  = os.path.join(BASE_DIR, 'server.key')

    if os.path.exists(cert_file) and os.path.exists(key_file):
        print("  HTTPS activé avec certificat local")
        app.run(host='0.0.0.0', port=port, debug=False,
                ssl_context=(cert_file, key_file), use_reloader=False)
    else:
        print("  Certificats manquants, lancement en HTTP")
        app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
