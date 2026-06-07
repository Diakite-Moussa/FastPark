"""
db.py — Couche d'abstraction base de données pour FastPark
- Local  : SQLite (via sqlite3, chemin DB_PATH)
- Cloud  : PostgreSQL (via psycopg2, variable DATABASE_URL)

Usage :
    from db import get_conn, ph

    conn = get_conn()
    c = conn.cursor()
    c.execute(f"SELECT * FROM users WHERE id = {ph}", (user_id,))
    conn.commit()
    conn.close()

    ph  → "?" en SQLite, "%s" en PostgreSQL
"""

import os
import sqlite3
import logging

logger = logging.getLogger(__name__)

# ── Détection du mode ────────────────────────────────────────
DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Railway fournit parfois "postgres://" mais psycopg2 veut "postgresql://"
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

USE_POSTGRES = bool(DATABASE_URL)

if USE_POSTGRES:
    import psycopg2
    import psycopg2.extras
    ph = "%s"          # placeholder PostgreSQL
    logger.info("Mode base de données : PostgreSQL")
else:
    ph = "?"           # placeholder SQLite
    logger.info("Mode base de données : SQLite")


# ─────────────────────────────────────────────────────────────
# Connexion
# ─────────────────────────────────────────────────────────────
def get_conn():
    """Retourne une connexion à la base de données active."""
    if USE_POSTGRES:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = False
        return conn
    else:
        db_path = os.environ.get("_FASTPARK_DB_PATH", "parking.db")
        conn = sqlite3.connect(db_path, timeout=10, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")   # évite 'database is locked' en concurrence
        conn.execute("PRAGMA busy_timeout=5000")  # attend 5s max avant d'abandonner
        conn.row_factory = sqlite3.Row
        return conn


# ─────────────────────────────────────────────────────────────
# Helpers DDL — CREATE TABLE compatible SQLite & PostgreSQL
# ─────────────────────────────────────────────────────────────
def _ddl_parking_spots():
    if USE_POSTGRES:
        return """
        CREATE TABLE IF NOT EXISTS parking_spots (
            spot_id     TEXT        NOT NULL,
            university  TEXT        NOT NULL,
            status      TEXT        NOT NULL DEFAULT 'free',
            battery     INTEGER,
            temperature REAL,
            confidence  REAL,
            updated_at  TEXT,
            source      TEXT,
            PRIMARY KEY (spot_id, university)
        )"""
    return """
    CREATE TABLE IF NOT EXISTS parking_spots (
        spot_id     TEXT,
        university  TEXT,
        status      TEXT,
        battery     INTEGER,
        temperature REAL,
        confidence  REAL,
        updated_at  TEXT,
        source      TEXT,
        PRIMARY KEY (spot_id, university)
    )"""


def _ddl_users():
    if USE_POSTGRES:
        return """
        CREATE TABLE IF NOT EXISTS users (
            id            SERIAL PRIMARY KEY,
            username      TEXT UNIQUE NOT NULL,
            email         TEXT UNIQUE NOT NULL,
            password_hash TEXT        NOT NULL,
            university    TEXT        NOT NULL,
            role          TEXT        NOT NULL DEFAULT 'user',
            created_at    TEXT        DEFAULT to_char(now(), 'YYYY-MM-DD HH24:MI:SS')
        )"""
    return """
    CREATE TABLE IF NOT EXISTS users (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        username      TEXT UNIQUE NOT NULL,
        email         TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        university    TEXT NOT NULL,
        role          TEXT DEFAULT 'user',
        created_at    TEXT DEFAULT CURRENT_TIMESTAMP
    )"""


def _ddl_parking_history():
    if USE_POSTGRES:
        return """
        CREATE TABLE IF NOT EXISTS parking_history (
            id         SERIAL PRIMARY KEY,
            spot_id    TEXT,
            university TEXT,
            status     TEXT,
            changed_at TEXT
        )"""
    return """
    CREATE TABLE IF NOT EXISTS parking_history (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        spot_id    TEXT,
        university TEXT,
        status     TEXT,
        changed_at TEXT
    )"""


def _ddl_reservations():
    if USE_POSTGRES:
        return """
        CREATE TABLE IF NOT EXISTS reservations (
            id          SERIAL PRIMARY KEY,
            spot_id     TEXT,
            university  TEXT,
            user_id     INTEGER,
            username    TEXT,
            reserved_at TEXT,
            expires_at  TEXT,
            status      TEXT DEFAULT 'active'
        )"""
    return """
    CREATE TABLE IF NOT EXISTS reservations (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        spot_id     TEXT,
        university  TEXT,
        user_id     INTEGER,
        username    TEXT,
        reserved_at TEXT,
        expires_at  TEXT,
        status      TEXT DEFAULT 'active'
    )"""


def _ddl_chat_history():
    if USE_POSTGRES:
        return """
        CREATE TABLE IF NOT EXISTS chat_history (
            id         SERIAL PRIMARY KEY,
            user_id    INTEGER NOT NULL,
            role       TEXT    NOT NULL,
            message    TEXT    NOT NULL,
            created_at TEXT    DEFAULT to_char(now(), 'YYYY-MM-DD HH24:MI:SS')
        )"""
    return """
    CREATE TABLE IF NOT EXISTS chat_history (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER NOT NULL,
        role       TEXT NOT NULL,
        message    TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )"""


def _ddl_checkin_log():
    if USE_POSTGRES:
        return """
        CREATE TABLE IF NOT EXISTS checkin_log (
            id             SERIAL PRIMARY KEY,
            reservation_id INTEGER,
            spot_id        TEXT,
            university     TEXT,
            username       TEXT,
            validated_by   TEXT,
            validated_at   TEXT DEFAULT to_char(now(), 'YYYY-MM-DD HH24:MI:SS')
        )"""
    return """
    CREATE TABLE IF NOT EXISTS checkin_log (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        reservation_id INTEGER,
        spot_id        TEXT,
        university     TEXT,
        username       TEXT,
        validated_by   TEXT,
        validated_at   TEXT DEFAULT CURRENT_TIMESTAMP
    )"""


def _ddl_qr_codes():
    if USE_POSTGRES:
        return """
        CREATE TABLE IF NOT EXISTS qr_codes (
            id             SERIAL PRIMARY KEY,
            reservation_id INTEGER,
            code           TEXT UNIQUE NOT NULL,
            used           INTEGER     NOT NULL DEFAULT 0,
            used_at        TEXT,
            created_at     TEXT DEFAULT to_char(now(), 'YYYY-MM-DD HH24:MI:SS')
        )"""
    return """
    CREATE TABLE IF NOT EXISTS qr_codes (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        reservation_id INTEGER,
        code           TEXT UNIQUE NOT NULL,
        used           INTEGER DEFAULT 0,
        used_at        TEXT,
        created_at     TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (reservation_id) REFERENCES reservations(id)
    )"""


# ─────────────────────────────────────────────────────────────
# Helper INSERT OR IGNORE compatible
# ─────────────────────────────────────────────────────────────
def insert_or_ignore_spot(c, spot_id, university, status, battery, temperature,
                           confidence, updated_at, source):
    """INSERT OR IGNORE compatible SQLite et PostgreSQL."""
    if USE_POSTGRES:
        c.execute("""
            INSERT INTO parking_spots
                (spot_id, university, status, battery, temperature, confidence, updated_at, source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (spot_id, university) DO NOTHING
        """, (spot_id, university, status, battery, temperature, confidence, updated_at, source))
    else:
        c.execute("""
            INSERT OR IGNORE INTO parking_spots
                (spot_id, university, status, battery, temperature, confidence, updated_at, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (spot_id, university, status, battery, temperature, confidence, updated_at, source))
    return c.rowcount


def insert_or_ignore_qr(c, reservation_id, code):
    """INSERT OR IGNORE pour qr_codes, compatible SQLite et PostgreSQL."""
    if USE_POSTGRES:
        c.execute("""
            INSERT INTO qr_codes (reservation_id, code, used)
            VALUES (%s, %s, 0)
            ON CONFLICT (code) DO NOTHING
        """, (reservation_id, code))
    else:
        c.execute("""
            INSERT OR IGNORE INTO qr_codes (reservation_id, code, used)
            VALUES (?, ?, 0)
        """, (reservation_id, code))


# ─────────────────────────────────────────────────────────────
# Helper lastrowid compatible
# ─────────────────────────────────────────────────────────────
def lastrowid(c, table="reservations"):
    """Récupère le dernier ID inséré, compatible SQLite et PostgreSQL."""
    if USE_POSTGRES:
        c.execute(f"SELECT lastval()")
        return c.fetchone()[0]
    return c.lastrowid


# ─────────────────────────────────────────────────────────────
# Helper datetime('now') compatible
# ─────────────────────────────────────────────────────────────
def sql_now():
    """Expression SQL pour l'heure courante selon le moteur."""
    if USE_POSTGRES:
        return "NOW()"
    return "datetime('now')"


def all_ddl():
    """Retourne toutes les instructions DDL dans l'ordre."""
    return [
        _ddl_parking_spots(),
        _ddl_users(),
        _ddl_parking_history(),
        _ddl_reservations(),
        _ddl_chat_history(),
        _ddl_checkin_log(),
        _ddl_qr_codes(),
    ]
