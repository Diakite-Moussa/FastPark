"""
test_db.py — Tests de la base de données : index et rotation des logs
"""
import os
import sqlite3
import logging
from logging.handlers import RotatingFileHandler


class TestIndex:
    def test_index_existent_apres_init(self, app):
        """CORRECTIF 2 : Les 5 index doivent exister dans la DB après init_db()."""
        db_path = os.environ.get("_FASTPARK_DB_PATH")
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
        ).fetchall()
        conn.close()

        index_names = {r[0] for r in rows}
        attendus = {
            "idx_res_expires",
            "idx_res_user",
            "idx_spots_uni",
            "idx_history_spot",
            "idx_checkin_date",
        }
        manquants = attendus - index_names
        assert not manquants, f"Index manquants : {manquants}"

    def test_places_inserees_au_demarrage(self, app):
        """42 places (14 univ × 3 spots) doivent exister après init_db()."""
        db_path = os.environ.get("_FASTPARK_DB_PATH")
        conn = sqlite3.connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM parking_spots").fetchone()[0]
        conn.close()
        assert count == 42, f"Attendu 42 places, trouvé {count}"

    def test_admin_cree_au_demarrage(self, app):
        """Le compte admin doit exister après init_db()."""
        db_path = os.environ.get("_FASTPARK_DB_PATH")
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT username, role FROM users WHERE username='admin'"
        ).fetchone()
        conn.close()
        assert row is not None, "Compte admin introuvable"
        assert row[1] == "admin"

    def test_reinit_db_idempotente(self, app):
        """Appeler init_db() deux fois ne doit pas dupliquer les données."""
        from app import init_db
        with app.app_context():
            init_db()

        db_path = os.environ.get("_FASTPARK_DB_PATH")
        conn = sqlite3.connect(db_path)
        count_spots = conn.execute("SELECT COUNT(*) FROM parking_spots").fetchone()[0]
        count_admin = conn.execute(
            "SELECT COUNT(*) FROM users WHERE username='admin'"
        ).fetchone()[0]
        conn.close()

        assert count_spots == 42, f"Duplication détectée : {count_spots} places"
        assert count_admin == 1, f"Admin dupliqué : {count_admin} entrées"


class TestRotationLogs:
    def test_rotating_handler_configure(self, app):
        """
        CORRECTIF 1 : Le logger 'app' doit utiliser un RotatingFileHandler.
        On cherche dans le logger de l'app (pas le root logger de pytest).
        """
        # Le logger FastPark est nommé 'app' (getLogger(__name__) dans app.py)
        app_logger = logging.getLogger("app")

        # Remonter aussi les handlers hérités (propagate=True par défaut)
        all_handlers = list(app_logger.handlers)
        logger = app_logger
        while logger.parent and logger.propagate:
            all_handlers.extend(logger.parent.handlers)
            logger = logger.parent
            if not logger.parent:
                break

        types = [type(h).__name__ for h in all_handlers]
        assert "RotatingFileHandler" in types, (
            f"RotatingFileHandler absent dans le logger 'app'.\n"
            f"Handlers trouvés : {types}\n"
            f"Conseil : vérifier que app.py utilise bien RotatingFileHandler."
        )

    def test_rotating_handler_max_bytes(self, app):
        """Le RotatingFileHandler doit être configuré à 5 MB max, 3 backups."""
        app_logger = logging.getLogger("app")

        all_handlers = list(app_logger.handlers)
        logger = app_logger
        while logger.parent and logger.propagate:
            all_handlers.extend(logger.parent.handlers)
            logger = logger.parent
            if not logger.parent:
                break

        for h in all_handlers:
            if isinstance(h, RotatingFileHandler):
                assert h.maxBytes == 5 * 1024 * 1024, (
                    f"maxBytes = {h.maxBytes}, attendu {5 * 1024 * 1024}"
                )
                assert h.backupCount == 3, f"backupCount = {h.backupCount}, attendu 3"
                return

        assert False, (
            "Aucun RotatingFileHandler trouvé.\n"
            f"Handlers disponibles : {[type(h).__name__ for h in all_handlers]}"
        )

    def test_log_ecrit_sans_erreur(self, app):
        """Écrire dans le logger de l'app ne doit pas lever d'exception."""
        logger = logging.getLogger("app")
        try:
            logger.info("Test rotation log FastPark")
            logger.warning("Avertissement test")
        except Exception as e:
            assert False, f"Erreur lors de l'écriture du log : {e}"
