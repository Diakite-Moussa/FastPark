"""
test_reservations.py — Tests des réservations et du correctif de concurrence
"""
import threading


class TestReservation:
    def test_reserver_place_libre(self, user_client):
        """Un utilisateur peut réserver une place libre."""
        r = user_client.post("/api/reserve", json={
            "spot_id": "A1",
            "university": "Mundiapolis"
        })
        data = r.get_json()
        assert r.status_code == 200, f"Réponse : {data}"
        assert data["success"] is True
        assert "qr_code" in data
        assert "expires_at" in data
        assert "reservation_id" in data

    def test_reserver_place_deja_reservee(self, app):
        """Deux utilisateurs ne peuvent pas réserver la même place."""
        client1 = app.test_client()
        client2 = app.test_client()

        client1.post("/api/register", json={
            "username": "user_a",
            "email": "usera@mundiapolis.ma",
            "password": "passA12345",
            "university": "Mundiapolis"
        })
        client1.post("/api/login", json={"username": "user_a", "password": "passA12345"})

        client2.post("/api/register", json={
            "username": "user_b",
            "email": "userb@mundiapolis.ma",
            "password": "passB12345",
            "university": "Mundiapolis"
        })
        client2.post("/api/login", json={"username": "user_b", "password": "passB12345"})

        # User A réserve A1
        r1 = client1.post("/api/reserve", json={"spot_id": "A1", "university": "Mundiapolis"})
        assert r1.get_json()["success"] is True, f"Réservation user_a échouée : {r1.get_json()}"

        # User B essaie de réserver A1 → doit échouer
        r2 = client2.post("/api/reserve", json={"spot_id": "A1", "university": "Mundiapolis"})
        assert r2.get_json()["success"] is False

    def test_double_reservation_meme_utilisateur(self, user_client):
        """Un utilisateur ne peut pas avoir deux réservations actives."""
        r1 = user_client.post("/api/reserve", json={"spot_id": "A1", "university": "Mundiapolis"})
        assert r1.get_json()["success"] is True

        r2 = user_client.post("/api/reserve", json={"spot_id": "A2", "university": "Mundiapolis"})
        assert r2.get_json()["success"] is False

    def test_annuler_reservation(self, user_client):
        """Un utilisateur peut annuler sa réservation."""
        user_client.post("/api/reserve", json={"spot_id": "A1", "university": "Mundiapolis"})
        r = user_client.post("/api/cancel_reservation", json={
            "spot_id": "A1", "university": "Mundiapolis"
        })
        assert r.get_json()["success"] is True

    def test_ma_reservation(self, user_client):
        """Après réservation, my_reservation retourne la bonne place."""
        user_client.post("/api/reserve", json={"spot_id": "A2", "university": "Mundiapolis"})
        r = user_client.get("/api/my_reservation")
        data = r.get_json()
        assert data["has_reservation"] is True
        assert data["spot_id"] == "A2"

    def test_pas_de_reservation_active(self, user_client):
        """Sans réservation, has_reservation doit être False."""
        r = user_client.get("/api/my_reservation")
        assert r.get_json()["has_reservation"] is False

    def test_reserver_sans_auth(self, client):
        """Sans être connecté → 401."""
        r = client.post("/api/reserve", json={
            "spot_id": "A1", "university": "Mundiapolis"
        })
        assert r.status_code == 401

    def test_annuler_puis_reserver_a_nouveau(self, user_client):
        """Après annulation, la place redevient disponible."""
        user_client.post("/api/reserve", json={"spot_id": "A1", "university": "Mundiapolis"})
        user_client.post("/api/cancel_reservation", json={"spot_id": "A1", "university": "Mundiapolis"})

        r = user_client.post("/api/reserve", json={"spot_id": "A1", "university": "Mundiapolis"})
        assert r.get_json()["success"] is True


class TestConcurrence:
    def test_concurrence_reservation_simultanee(self, app):
        """
        CORRECTIF 3 : Deux requêtes simultanées sur la même place.
        Exactement une doit réussir, l'autre doit échouer.
        """
        client1 = app.test_client()
        client2 = app.test_client()

        for username, email, c in [
            ("conc_a", "conca@mundiapolis.ma", client1),
            ("conc_b", "concb@mundiapolis.ma", client2),
        ]:
            c.post("/api/register", json={
                "username": username,
                "email": email,
                "password": "concpass123",
                "university": "Mundiapolis"
            })
            c.post("/api/login", json={"username": username, "password": "concpass123"})

        resultats = []

        def reserver(c):
            r = c.post("/api/reserve", json={
                "spot_id": "A1", "university": "Mundiapolis"
            })
            data = r.get_json()
            resultats.append(data.get("success", False))

        t1 = threading.Thread(target=reserver, args=(client1,))
        t2 = threading.Thread(target=reserver, args=(client2,))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert len(resultats) == 2, f"Résultats incomplets : {resultats}"
        assert resultats.count(True) == 1, (
            f"Attendu exactement 1 succès, obtenu : {resultats}\n"
            f"Le correctif de concurrence ne fonctionne pas."
        )
        assert resultats.count(False) == 1
