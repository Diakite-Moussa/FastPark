"""
conftest.py — Configuration pytest pour FastPark
"""
import os
import sys
import tempfile
import pytest

# ─── Nettoyer l'env avant tout import de app ────────────────
os.environ.pop("DATABASE_URL", None)

# ADMIN_PASSWORD doit être défini AVANT le premier import de app.py
# car init_db() lit os.environ["ADMIN_PASSWORD"] au moment de créer l'admin
os.environ["ADMIN_PASSWORD"] = "admintest123"
os.environ["SECRET_KEY"]     = "test-secret-key-fastpark"


def _make_app(db_path: str):
    """
    Crée une nouvelle instance Flask propre avec la DB pointant sur db_path.
    Purge sys.modules pour forcer le rechargement complet à chaque appel.
    """
    os.environ["_FASTPARK_DB_PATH"] = db_path

    # Purger tous les modules FastPark pour forcer le rechargement
    for mod in list(sys.modules.keys()):
        if mod in ("app", "db", "mcp") or mod.startswith("app.") or mod.startswith("db."):
            del sys.modules[mod]

    from app import app as flask_app, init_db

    flask_app.config.update({
        "TESTING":          True,
        "SECRET_KEY":       "test-secret-key-fastpark",
        "RATELIMIT_ENABLED": False,
    })

    # Désactiver Flask-Limiter
    try:
        from flask_limiter import Limiter
        for ext in flask_app.extensions.values():
            if isinstance(ext, Limiter):
                ext._enabled = False
    except Exception:
        pass

    # Initialiser la DB avec la connexion propre
    # On utilise check_same_thread=False pour SQLite en mode test
    import sqlite3
    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=10)
    conn.close()

    with flask_app.app_context():
        init_db()

    return flask_app


@pytest.fixture(scope="function")
def app():
    """Crée une instance Flask avec DB temporaire isolée par test."""
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)  # Fermer le fd immédiatement — SQLite gère sa propre ouverture

    flask_app = _make_app(db_path)
    yield flask_app

    # Nettoyage — fermer toutes les connexions SQLite restantes
    try:
        import gc
        gc.collect()  # Force la fermeture des connexions lingering
        os.unlink(db_path)
    except (PermissionError, OSError):
        pass  # Windows : fichier encore utilisé, le GC le libérera


@pytest.fixture(scope="function")
def client(app):
    return app.test_client()


@pytest.fixture(scope="function")
def admin_client(client):
    """Client connecté en tant qu'admin."""
    r = client.post("/api/login", json={
        "username": "admin",
        "password": "admintest123"
    })
    assert r.status_code == 200, (
        f"Login admin échoué : {r.get_json()}\n"
        f"Vérifier que ADMIN_PASSWORD='admintest123' est bien lu par init_db()"
    )
    return client


@pytest.fixture(scope="function")
def user_client(client):
    """Client connecté en tant qu'utilisateur normal (Mundiapolis)."""
    r = client.post("/api/register", json={
        "username": "testuser",
        "email":    "test@mundiapolis.ma",
        "password": "testpass123",
        "university": "Mundiapolis"
    })
    assert r.status_code == 200, f"Register échoué : {r.get_json()}"

    r = client.post("/api/login", json={
        "username": "testuser",
        "password": "testpass123"
    })
    assert r.status_code == 200, f"Login utilisateur échoué : {r.get_json()}"
    return client
