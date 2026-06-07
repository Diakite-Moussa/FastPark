"""
test_auth.py — Tests d'authentification (login, register, logout)
"""


class TestLogin:
    def test_login_admin_succes(self, client):
        """L'admin peut se connecter avec les bons identifiants."""
        r = client.post("/api/login", json={
            "username": "admin",
            "password": "admintest123"
        })
        assert r.status_code == 200, f"Réponse : {r.get_json()}"
        data = r.get_json()
        assert data["success"] is True
        assert data["role"] == "admin"

    def test_login_mauvais_mot_de_passe(self, client):
        """Mauvais mot de passe → 401."""
        r = client.post("/api/login", json={
            "username": "admin",
            "password": "mauvaismdp"
        })
        assert r.status_code == 401
        assert r.get_json()["success"] is False

    def test_login_utilisateur_inexistant(self, client):
        """Utilisateur qui n'existe pas → 401."""
        r = client.post("/api/login", json={
            "username": "fantome",
            "password": "nimporte"
        })
        assert r.status_code == 401

    def test_login_champs_vides(self, client):
        """Champs vides → 400."""
        r = client.post("/api/login", json={"username": "", "password": ""})
        assert r.status_code == 400

    def test_check_auth_non_connecte(self, client):
        """Sans session active, logged_in doit être False."""
        r = client.get("/api/check_auth")
        assert r.status_code == 200
        assert r.get_json()["logged_in"] is False

    def test_check_auth_connecte(self, admin_client):
        """Après login, logged_in doit être True."""
        r = admin_client.get("/api/check_auth")
        data = r.get_json()
        assert data["logged_in"] is True
        assert data["username"] == "admin"
        assert data["role"] == "admin"

    def test_logout(self, admin_client):
        """Après logout, la session est détruite."""
        r = admin_client.post("/api/logout")
        assert r.get_json()["success"] is True

        r2 = admin_client.get("/api/check_auth")
        assert r2.get_json()["logged_in"] is False


class TestRegister:
    def test_inscription_valide(self, client):
        """Inscription avec données valides → succès."""
        r = client.post("/api/register", json={
            "username": "moussa",
            "email": "moussa@mundiapolis.ma",
            "password": "monmdp1234",
            "university": "Mundiapolis"
        })
        assert r.status_code == 200, f"Réponse : {r.get_json()}"
        assert r.get_json()["success"] is True

    def test_inscription_puis_login(self, client):
        """Un utilisateur inscrit peut se connecter."""
        client.post("/api/register", json={
            "username": "ali",
            "email": "ali@emsi.ma",
            "password": "alipass123",
            "university": "EMSI"
        })
        r = client.post("/api/login", json={
            "username": "ali",
            "password": "alipass123"
        })
        assert r.get_json()["success"] is True

    def test_inscription_doublon_username(self, client):
        """Deux inscriptions avec le même username → erreur."""
        client.post("/api/register", json={
            "username": "doublon",
            "email": "doublon1@emsi.ma",
            "password": "pass12345",
            "university": "EMSI"
        })
        r = client.post("/api/register", json={
            "username": "doublon",
            "email": "doublon2@emsi.ma",
            "password": "pass12345",
            "university": "EMSI"
        })
        assert r.status_code == 400

    def test_inscription_mot_de_passe_trop_court(self, client):
        """Mot de passe < 8 caractères → rejeté."""
        r = client.post("/api/register", json={
            "username": "userA",
            "email": "userA@emsi.ma",
            "password": "court",
            "university": "EMSI"
        })
        assert r.status_code == 400

    def test_inscription_universite_invalide(self, client):
        """Université hors liste → rejetée."""
        r = client.post("/api/register", json={
            "username": "userB",
            "email": "userB@test.ma",
            "password": "monmdp1234",
            "university": "Université de Narnia"
        })
        assert r.status_code == 400

    def test_inscription_email_invalide(self, client):
        """Email sans @ → rejeté."""
        r = client.post("/api/register", json={
            "username": "userC",
            "email": "pasunemail",
            "password": "monmdp1234",
            "university": "EMSI"
        })
        assert r.status_code == 400
