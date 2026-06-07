from flask import Flask, jsonify, request, session, send_from_directory, Response
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from functools import wraps
import csv
import json
import threading
import time
import logging
from logging.handlers import RotatingFileHandler
import socket
from io import StringIO
from datetime import datetime, timedelta
from collections import deque
import os
import paho.mqtt.client as mqtt
import random
import qrcode
import secrets
import bcrypt
from io import BytesIO
import base64

from db import (
    get_conn, ph, sql_now, all_ddl,
    insert_or_ignore_spot, insert_or_ignore_qr, lastrowid,
    USE_POSTGRES
)
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

# Injecter DB_PATH dans db.py uniquement si pas déjà défini par les tests
if not os.environ.get("_FASTPARK_DB_PATH"):
    os.environ["_FASTPARK_DB_PATH"] = DB_PATH

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))

ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "http://localhost:5000").split(",")
CORS(app, origins=ALLOWED_ORIGINS, supports_credentials=True)

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://"
)

_log_handlers = [logging.StreamHandler()]

# En production (Railway), le filesystem est éphémère — logger uniquement sur stdout.
# En développement local, activer aussi le fichier tournant.
_is_production = bool(os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_PROJECT_ID"))
if not _is_production:
    try:
        _rotating_handler = RotatingFileHandler(
            os.path.join(_data_dir, "fastpark.log"),
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8"
        )
        _log_handlers.append(_rotating_handler)
    except Exception:
        pass

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=_log_handlers
)
logger = logging.getLogger(__name__)
logger.info(f"DB_PATH = {DB_PATH} | PostgreSQL = {USE_POSTGRES}")

# ── Coordonnées GPS ──────────────────────────────────────────
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

mcp.db_path = DB_PATH

mqtt_buffer    = deque(maxlen=1000)
mqtt_available = True

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
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def check_password(password: str, stored_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode('utf-8'), stored_hash.encode('utf-8'))
    except Exception:
        import hashlib
        return hashlib.sha256(password.encode()).hexdigest() == stored_hash

# ─────────────────────────────────────────────────────────────
# Protection CSRF — double-submit cookie
# ─────────────────────────────────────────────────────────────
def generate_csrf_token() -> str:
    """Génère ou récupère le token CSRF de la session."""
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_hex(32)
    return session['csrf_token']

def csrf_required(f):
    """
    Décorateur CSRF : vérifie que le header X-CSRF-Token correspond
    au token stocké en session. À appliquer sur tous les POST sensibles.
    Exempté en mode TESTING pour les tests automatisés.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if app.config.get('TESTING'):
            return f(*args, **kwargs)
        token_header  = request.headers.get('X-CSRF-Token', '')
        token_session = session.get('csrf_token', '')
        if not token_session or not secrets.compare_digest(token_header, token_session):
            logger.warning(f"CSRF invalide sur {request.path} — IP: {request.remote_addr}")
            return jsonify({"error": "Token CSRF invalide"}), 403
        return f(*args, **kwargs)
    return decorated

@app.route('/api/csrf_token', methods=['GET'])
def get_csrf_token():
    """Le frontend appelle cette route au chargement pour obtenir son token CSRF."""
    return jsonify({"csrf_token": generate_csrf_token()})

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
# Initialisation de la base de données
# ─────────────────────────────────────────────────────────────
def init_db():
    conn = get_conn()
    c    = conn.cursor()

    # Créer toutes les tables
    for ddl in all_ddl():
        c.execute(ddl)

    # ── Index pour les performances ──────────────────────────
    c.execute("""CREATE INDEX IF NOT EXISTS idx_res_expires
                 ON reservations(status, expires_at)""")
    c.execute("""CREATE INDEX IF NOT EXISTS idx_res_user
                 ON reservations(user_id, status)""")
    c.execute("""CREATE INDEX IF NOT EXISTS idx_spots_uni
                 ON parking_spots(university)""")
    c.execute("""CREATE INDEX IF NOT EXISTS idx_history_spot
                 ON parking_history(spot_id, university)""")
    c.execute("""CREATE INDEX IF NOT EXISTS idx_checkin_date
                 ON checkin_log(validated_at)""")

    # Insérer les places manquantes
    inserted = 0
    now_str  = datetime.now().isoformat()
    for uni in UNIVERSITIES:
        for spot in SPOTS:
            insert_or_ignore_spot(
                c, spot, uni, "free",
                random.randint(60, 100),
                round(random.uniform(18, 25), 1),
                0.95, now_str, "system"
            )
            inserted += c.rowcount

    # Compte admin
    c.execute(f"SELECT id, password_hash FROM users WHERE username = {ph}", ("admin",))
    existing_admin = c.fetchone()

    if not existing_admin:
        admin_password = os.environ.get("ADMIN_PASSWORD", "fastpark123")
        admin_hash     = hash_password(admin_password)
        c.execute(
            f"INSERT INTO users (username, email, password_hash, university, role) VALUES ({ph},{ph},{ph},{ph},{ph})",
            ("admin", "admin@fastpark.ma", admin_hash, "all", "admin")
        )
        logger.info("Compte admin créé (bcrypt).")
    else:
        stored = existing_admin[1] if not USE_POSTGRES else existing_admin["password_hash"] if hasattr(existing_admin, "keys") else existing_admin[1]
        if not str(stored).startswith("$2b$") and not str(stored).startswith("$2a$"):
            logger.info("Admin existant en SHA-256 : sera migré à la prochaine connexion.")

    conn.commit()
    conn.close()

    total_spots = len(UNIVERSITIES) * len(SPOTS)
    logger.info(f"DB prête : {len(UNIVERSITIES)} univ × {len(SPOTS)} places = {total_spots} ({inserted} nouvelles)")

# ─────────────────────────────────────────────────────────────
# Sauvegarde MQTT → DB
# ─────────────────────────────────────────────────────────────
def save_to_db(data):
    try:
        spot_id     = data.get('spot_id')
        uni         = data.get('university')
        status      = data.get('status')
        battery     = data.get('battery')
        temperature = data.get('temperature')
        confidence  = data.get('confidence', 0.95)
        source      = data.get('source', 'mqtt')
        timestamp   = data.get('timestamp', datetime.now().isoformat())

        if not spot_id or not uni or not status:
            return

        conn = get_conn()
        c    = conn.cursor()

        c.execute(f"SELECT status FROM parking_spots WHERE spot_id={ph} AND university={ph}", (spot_id, uni))
        current = c.fetchone()
        current_status = current[0] if current else None

        if current_status == 'reserved' and status == 'free':
            conn.close()
            return

        c.execute(f"""UPDATE parking_spots
            SET status={ph}, battery={ph}, temperature={ph}, confidence={ph}, updated_at={ph}, source={ph}
            WHERE spot_id={ph} AND university={ph}""",
            (status, battery, temperature, confidence, timestamp, source, spot_id, uni))

        if current_status and current_status != status and status != 'reserved':
            c.execute(f"INSERT INTO parking_history (spot_id, university, status, changed_at) VALUES ({ph},{ph},{ph},{ph})",
                      (spot_id, uni, status, timestamp))

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
            conn = get_conn()
            c    = conn.cursor()
            now  = datetime.now().isoformat()
            c.execute(f"SELECT spot_id, university FROM reservations WHERE status='active' AND expires_at <= {ph}", (now,))
            expired = c.fetchall()
            for row in expired:
                spot_id    = row[0]
                university = row[1]
                c.execute(f"UPDATE parking_spots SET status='free', updated_at={ph} WHERE spot_id={ph} AND university={ph}",
                          (datetime.now().isoformat(), spot_id, university))
                logger.info(f"Expiration: {spot_id} - {university[:20]}... libérée")
            c.execute(f"UPDATE reservations SET status='expired' WHERE status='active' AND expires_at <= {ph}", (now,))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Erreur nettoyage: {e}")

# ─────────────────────────────────────────────────────────────
# Démarrage des threads arrière-plan (compatible gunicorn + python app.py)
# ─────────────────────────────────────────────────────────────
_background_threads_started = False
_threads_lock = threading.Lock()

def start_background_threads():
    """
    Lance les threads démon une seule fois par process.
    Appelé au 1er request entrant — compatible avec gunicorn multi-workers
    (chaque worker est un process isolé, les threads démarrent dans chacun).
    """
    global _background_threads_started
    with _threads_lock:
        if _background_threads_started:
            return
        _background_threads_started = True

    threading.Thread(target=start_mqtt_bridge,  daemon=True, name="mqtt-bridge").start()
    threading.Thread(target=clean_expired_loop, daemon=True, name="clean-expired").start()
    threading.Thread(target=flush_mqtt_buffer,  daemon=True, name="mqtt-flush").start()
    logger.info("Threads arrière-plan démarrés (mqtt, clean_expired, flush)")

@app.before_request
def ensure_background_threads():
    """Hook Flask — déclenche le démarrage des threads au 1er request."""
    if not app.config.get('TESTING'):
        start_background_threads()

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
@csrf_required
def api_login():
    data     = request.get_json() or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')

    if not username or not password:
        return jsonify({"success": False, "message": "Identifiants manquants"}), 400

    conn = get_conn()
    c    = conn.cursor()
    c.execute(f"SELECT id, username, university, role, password_hash FROM users WHERE username={ph}", (username,))
    user = c.fetchone()

    if not user or not check_password(password, user[4]):
        conn.close()
        logger.warning(f"Échec connexion: {username}")
        return jsonify({"success": False, "message": "Identifiants incorrects"}), 401

    stored_hash = user[4]
    if not str(stored_hash).startswith("$2b$") and not str(stored_hash).startswith("$2a$"):
        new_hash = hash_password(password)
        c.execute(f"UPDATE users SET password_hash={ph} WHERE id={ph}", (new_hash, user[0]))
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
@csrf_required
def api_register():
    import re
    data       = request.get_json() or {}
    username   = data.get('username', '').strip()
    email      = data.get('email', '').strip()
    password   = data.get('password', '')
    university = data.get('university', '').strip()

    if not username:
        return jsonify({"success": False, "message": "Nom d'utilisateur requis"}), 400
    if len(username) < 3 or len(username) > 30:
        return jsonify({"success": False, "message": "Le nom d'utilisateur doit faire entre 3 et 30 caractères"}), 400
    if not re.match(r'^[a-zA-Z0-9_-]+$', username):
        return jsonify({"success": False, "message": "Caractères non autorisés dans le nom d'utilisateur"}), 400
    if not email or not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email):
        return jsonify({"success": False, "message": "Format d'email invalide"}), 400
    if len(email) > 100:
        return jsonify({"success": False, "message": "Email trop long"}), 400
    if not password or len(password) < 8:
        return jsonify({"success": False, "message": "Le mot de passe doit contenir au moins 8 caractères"}), 400
    if not university or university not in UNIVERSITIES:
        return jsonify({"success": False, "message": "Université non valide"}), 400

    password_hash = hash_password(password)
    try:
        conn = get_conn()
        conn.cursor().execute(
            f"INSERT INTO users (username, email, password_hash, university, role) VALUES ({ph},{ph},{ph},{ph},'user')",
            (username, email, password_hash, university)
        )
        conn.commit()
        conn.close()
        logger.info(f"Nouvel utilisateur inscrit: {username}")
        return jsonify({"success": True, "message": "Inscription réussie"})
    except Exception as e:
        err = str(e).lower()
        if "unique" in err or "duplicate" in err:
            return jsonify({"success": False, "message": "Nom d'utilisateur ou email déjà utilisé"}), 400
        return jsonify({"success": False, "message": "Erreur serveur"}), 500


@app.errorhandler(429)
def ratelimit_handler(e):
    return jsonify({"success": False, "message": "Trop de tentatives. Veuillez patienter une minute."}), 429


@app.route('/api/logout', methods=['POST'])
@csrf_required
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
        conn = get_conn()
        c    = conn.cursor()
        c.execute("SELECT MAX(updated_at) FROM parking_spots")
        last_update_str = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM parking_spots")
        total_spots = c.fetchone()[0]
        conn.close()

        data_fresh = False
        if last_update_str:
            try:
                lu = datetime.fromisoformat(str(last_update_str))
                data_fresh = (datetime.now() - lu).seconds < 300
            except:
                pass

        return jsonify({
            "status":           "healthy" if data_fresh else "warning",
            "last_data_update": str(last_update_str),
            "data_fresh":       data_fresh,
            "mqtt_broker":      "connected" if check_mqtt_broker() else "disconnected",
            "mqtt_host":        MQTT_BROKER,
            "total_spots":      total_spots,
            "database":         "postgresql" if USE_POSTGRES else "sqlite",
            "timestamp":        datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({"status": "unhealthy", "error": str(e)}), 500

# ─────────────────────────────────────────────────────────────
# API Réservations
# ─────────────────────────────────────────────────────────────
@app.route('/api/reserve', methods=['POST'])
@login_required
@csrf_required
def reserve_spot():
    data       = request.get_json() or {}
    spot_id    = data.get('spot_id')
    university = data.get('university')
    user_id    = session.get('user_id')
    username   = session.get('username')

    if not spot_id or not university:
        return jsonify({"success": False, "message": "Données manquantes"}), 400

    conn = get_conn()
    c    = conn.cursor()
    now  = datetime.now().isoformat()

    try:
        # ── Vérification réservation existante de l'utilisateur ──
        c.execute(f"SELECT id, spot_id, expires_at FROM reservations WHERE user_id={ph} AND status='active' AND expires_at > {ph}", (user_id, now))
        existing = c.fetchone()
        if existing:
            conn.close()
            return jsonify({"success": False, "message": f"Vous avez déjà une réservation active pour la place {existing[1]}"}), 400

        # ── Verrouillage atomique de la place (SELECT FOR UPDATE en PG, transaction en SQLite) ──
        # En SQLite, la transaction BEGIN IMMEDIATE pose un verrou exclusif sur la DB
        # En PostgreSQL, SELECT FOR UPDATE verrouille la ligne
        if USE_POSTGRES:
            c.execute(f"SELECT status FROM parking_spots WHERE spot_id={ph} AND university={ph} FOR UPDATE", (spot_id, university))
        else:
            c.execute("BEGIN IMMEDIATE") if not conn.in_transaction else None  # SQLite : verrou au niveau fichier
            c.execute(f"SELECT status FROM parking_spots WHERE spot_id={ph} AND university={ph}", (spot_id, university))

        current = c.fetchone()
        if not current:
            conn.close()
            return jsonify({"success": False, "message": "Place introuvable"}), 404

        current_status = current[0]
        if current_status in ('occupied', 'reserved'):
            conn.close()
            return jsonify({"success": False, "message": f"Cette place est déjà {current_status}"}), 400

        # ── Vérification doublon réservation active sur cette place ──
        c.execute(f"SELECT id FROM reservations WHERE spot_id={ph} AND university={ph} AND status='active' AND expires_at > {ph}", (spot_id, university, now))
        if c.fetchone():
            conn.close()
            return jsonify({"success": False, "message": "Cette place est déjà réservée"}), 400

        expires_at_str = (datetime.now() + timedelta(minutes=10)).isoformat()

        c.execute(
            f"INSERT INTO reservations (spot_id, university, user_id, username, reserved_at, expires_at, status) VALUES ({ph},{ph},{ph},{ph},{ph},{ph},'active')",
            (spot_id, university, user_id, username, now, expires_at_str)
        )
        reservation_id = lastrowid(c)

        qr_token     = secrets.token_urlsafe(32)
        qr_code_data = f"fastpark://checkin?res={reservation_id}&token={qr_token}"
        c.execute(f"INSERT INTO qr_codes (reservation_id, code) VALUES ({ph},{ph})", (reservation_id, qr_token))

        c.execute(f"UPDATE parking_spots SET status='reserved', updated_at={ph} WHERE spot_id={ph} AND university={ph}",
                  (now, spot_id, university))
        conn.commit()

    except Exception as e:
        conn.rollback()
        conn.close()
        logger.error(f"Erreur réservation: {e}")
        return jsonify({"success": False, "message": "Erreur serveur lors de la réservation"}), 500

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
@csrf_required
def cancel_reservation():
    data       = request.get_json() or {}
    spot_id    = data.get('spot_id')
    university = data.get('university')
    user_id    = session.get('user_id')

    conn = get_conn()
    c    = conn.cursor()
    c.execute(f"UPDATE reservations SET status='cancelled' WHERE spot_id={ph} AND university={ph} AND user_id={ph} AND status='active'",
              (spot_id, university, user_id))
    c.execute(f"UPDATE parking_spots SET status='free', updated_at={ph} WHERE spot_id={ph} AND university={ph}",
              (datetime.now().isoformat(), spot_id, university))
    conn.commit()
    conn.close()
    return jsonify({"success": True, "message": "Réservation annulée"})


@app.route('/api/my_reservation', methods=['GET'])
@login_required
def my_reservation():
    user_id = session.get('user_id')
    now     = datetime.now().isoformat()
    conn    = get_conn()
    c       = conn.cursor()
    c.execute(f"SELECT spot_id, university, reserved_at, expires_at FROM reservations WHERE user_id={ph} AND status='active' AND expires_at > {ph}", (user_id, now))
    r = c.fetchone()
    conn.close()
    if r:
        return jsonify({"has_reservation": True, "spot_id": r[0], "university": r[1], "reserved_at": r[2], "expires_at": r[3]})
    return jsonify({"has_reservation": False})

# ─────────────────────────────────────────────────────────────
# API Places & Stats
# ─────────────────────────────────────────────────────────────
@app.route('/api/spots', methods=['GET'])
@login_required
def get_spots():
    user_uni = session.get('university')
    is_admin = session.get('role') == 'admin'

    conn = get_conn()
    c    = conn.cursor()
    if is_admin:
        c.execute("SELECT spot_id, university, status, battery, temperature, confidence, updated_at, source FROM parking_spots ORDER BY university, spot_id")
    else:
        c.execute(f"SELECT spot_id, university, status, battery, temperature, confidence, updated_at, source FROM parking_spots WHERE university={ph} ORDER BY spot_id", (user_uni,))
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

    conn = get_conn()
    c    = conn.cursor()
    if is_admin:
        c.execute("SELECT COUNT(*) FROM parking_spots")
        total = c.fetchone()[0]
        c.execute("SELECT status, COUNT(*) FROM parking_spots GROUP BY status")
        sc = {r[0]: r[1] for r in c.fetchall()}
        c.execute("SELECT AVG(battery) FROM parking_spots WHERE battery > 0")
        avg_battery = c.fetchone()[0] or 0
    else:
        c.execute(f"SELECT COUNT(*) FROM parking_spots WHERE university={ph}", (user_uni,))
        total = c.fetchone()[0]
        c.execute(f"SELECT status, COUNT(*) FROM parking_spots WHERE university={ph} GROUP BY status", (user_uni,))
        sc = {r[0]: r[1] for r in c.fetchall()}
        c.execute(f"SELECT AVG(battery) FROM parking_spots WHERE university={ph} AND battery > 0", (user_uni,))
        avg_battery = c.fetchone()[0] or 0
    conn.close()

    free     = sc.get("free", 0)
    occupied = sc.get("occupied", 0)
    reserved = sc.get("reserved", 0)
    occ_rate = round(((occupied + reserved) / total) * 100, 1) if total else 0

    return jsonify({
        "total_spots": total, "occupied": occupied, "free": free, "reserved": reserved,
        "avg_battery_percent": round(avg_battery, 1),
        "occupancy_rate": occ_rate,
        "last_update": datetime.now().isoformat()
    })

# ─────────────────────────────────────────────────────────────
# API Export CSV
# ─────────────────────────────────────────────────────────────
@app.route('/api/export/csv', methods=['GET'])
@login_required
def export_csv():
    user_uni = session.get('university')
    is_admin = session.get('role') == 'admin'

    conn = get_conn()
    c    = conn.cursor()
    if is_admin:
        c.execute("SELECT spot_id, university, status, battery, temperature, updated_at FROM parking_spots")
    else:
        c.execute(f"SELECT spot_id, university, status, battery, temperature, updated_at FROM parking_spots WHERE university={ph}", (user_uni,))
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
# API GPS
# ─────────────────────────────────────────────────────────────
@app.route('/api/university_gps', methods=['GET'])
def university_gps():
    return jsonify(UNIVERSITY_GPS)

# ─────────────────────────────────────────────────────────────
# API Check-in history
# ─────────────────────────────────────────────────────────────
@app.route('/api/checkin_history', methods=['GET'])
@login_required
def get_checkin_history():
    conn = get_conn()
    c    = conn.cursor()
    if USE_POSTGRES:
        c.execute("""SELECT spot_id, university, username, validated_by, validated_at
                     FROM checkin_log
                     WHERE validated_at::date = CURRENT_DATE
                     ORDER BY validated_at DESC LIMIT 20""")
    else:
        c.execute("""SELECT spot_id, university, username, validated_by, validated_at
                     FROM checkin_log
                     WHERE date(validated_at) = date('now')
                     ORDER BY validated_at DESC LIMIT 20""")
    rows = c.fetchall()
    conn.close()
    return jsonify([{"spot_id": r[0], "university": r[1], "username": r[2], "validated_by": r[3], "time": r[4]} for r in rows])

# ─────────────────────────────────────────────────────────────
# API Chat IA
# ─────────────────────────────────────────────────────────────
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

    try:
        conn = get_conn()
        c    = conn.cursor()
        c.execute(f"INSERT INTO chat_history (user_id, role, message) VALUES ({ph},'user',{ph})", (user_id, question[:1000]))
        c.execute(f"INSERT INTO chat_history (user_id, role, message) VALUES ({ph},'assistant',{ph})", (user_id, response_text[:2000]))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"Erreur sauvegarde chat: {e}")

    return jsonify({"response": response_text})


@app.route('/api/chat_history', methods=['GET'])
@login_required
def get_chat_history():
    user_id = session.get('user_id')
    conn    = get_conn()
    c       = conn.cursor()
    c.execute(f"SELECT role, message, created_at FROM chat_history WHERE user_id={ph} ORDER BY created_at DESC LIMIT 20", (user_id,))
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
@csrf_required
def validate_checkin():
    data           = request.get_json() or {}
    qr_token       = data.get('token')
    reservation_id = data.get('reservation_id')
    user_id        = session.get('user_id')
    username       = session.get('username')
    is_admin       = session.get('role') == 'admin'
    now            = datetime.now().isoformat()

    if not reservation_id:
        return jsonify({"success": False, "message": "ID de réservation manquant"}), 400

    conn = get_conn()
    c    = conn.cursor()

    if qr_token and qr_token != str(reservation_id):
        c.execute(f"SELECT id FROM qr_codes WHERE reservation_id={ph} AND code={ph} AND used=0", (reservation_id, qr_token))
        if not c.fetchone():
            conn.close()
            return jsonify({"success": False, "message": "QR code invalide ou déjà utilisé"}), 400

    if is_admin:
        c.execute(f"SELECT spot_id, university, expires_at, status FROM reservations WHERE id={ph} AND status='active' AND expires_at > {ph}", (reservation_id, now))
    else:
        c.execute(f"SELECT spot_id, university, expires_at, status FROM reservations WHERE id={ph} AND user_id={ph} AND status='active' AND expires_at > {ph}", (reservation_id, user_id, now))

    reservation = c.fetchone()
    if not reservation:
        conn.close()
        return jsonify({"success": False, "message": "Réservation invalide ou expirée"}), 400

    spot_id, university, expires_at, status = reservation[0], reservation[1], reservation[2], reservation[3]

    if not qr_token or qr_token == str(reservation_id):
        insert_or_ignore_qr(c, reservation_id, str(reservation_id))
        conn.commit()

    c.execute(f"UPDATE reservations SET status='checked_in' WHERE id={ph}", (reservation_id,))
    c.execute(f"UPDATE parking_spots SET status='occupied', updated_at={ph} WHERE spot_id={ph} AND university={ph}",
              (now, spot_id, university))
    c.execute(f"UPDATE qr_codes SET used=1, used_at={ph} WHERE reservation_id={ph} AND used=0",
              (now, reservation_id))

    c.execute(f"SELECT username FROM reservations WHERE id={ph}", (reservation_id,))
    res_row = c.fetchone()
    client_username = res_row[0] if res_row else username

    validator = session.get('username', 'agent')
    c.execute(f"INSERT INTO checkin_log (reservation_id, spot_id, university, username, validated_by) VALUES ({ph},{ph},{ph},{ph},{ph})",
              (reservation_id, spot_id, university, client_username, validator))

    conn.commit()
    conn.close()
    logger.info(f"Check-in validé: {username} → place {spot_id}")
    return jsonify({"success": True, "message": f"Bienvenue ! Place {spot_id} est maintenant occupée", "spot_id": spot_id})


@app.route('/api/reservation_details', methods=['GET'])
@login_required
def reservation_details():
    res_id = request.args.get('res_id')
    if not res_id:
        return jsonify({"error": "ID requis"}), 400

    conn = get_conn()
    c    = conn.cursor()
    c.execute(f"""SELECT r.spot_id, r.university, u.username, u.university
                  FROM reservations r JOIN users u ON r.user_id = u.id
                  WHERE r.id = {ph}""", (res_id,))
    result = c.fetchone()
    conn.close()

    if result:
        return jsonify({"spot_id": result[0], "university": result[1], "username": result[2], "user_university": result[3]})
    return jsonify({"error": "Réservation non trouvée"}), 404

# ─────────────────────────────────────────────────────────────
# Initialisation DB au démarrage (python app.py ET gunicorn)
# ─────────────────────────────────────────────────────────────
# Appelé une fois à l'import du module, avant le 1er request.
# Le guard permet d'éviter une double exécution en mode test.
if not app.config.get('TESTING'):
    init_db()

# ─────────────────────────────────────────────────────────────
# Lancement
# ─────────────────────────────────────────────────────────────
if __name__ == '__main__':
    start_background_threads()

    port = int(os.environ.get("PORT", 5000))

    print("\n" + "═" * 55)
    print("  FastPark multi-universités démarré !")
    print("═" * 55)
    print(f"  Dashboard  →  http://localhost:{port}")
    print(f"  MQTT       →  {MQTT_BROKER}:{MQTT_PORT}")
    print(f"  Base       →  {'PostgreSQL ☁️' if USE_POSTGRES else 'SQLite 💾'}")
    print(f"  Inscription→  http://localhost:{port}/register")
    print(f"  Monitoring →  http://localhost:{port}/health")
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
