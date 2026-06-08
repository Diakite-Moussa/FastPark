# 🅿️ FastPark — Système de Gestion de Parking Intelligent

Système IoT de gestion de parkings universitaires en temps réel, basé sur Flask, MQTT et SQLite.
Développé à l'Université Mundiapolis, Casablanca.

---

## ✨ Fonctionnalités

- **Temps réel** : capteurs IoT via MQTT → mise à jour automatique toutes les 3s
- **14 universités** de Casablanca gérées simultanément (42 places)
- **Réservation en ligne** avec QR code d'accès généré automatiquement
- **Check-in agent** : scan QR via caméra, saisie manuelle, lampe torche intégrée
- **Assistant IA** : chatbot branché sur Groq (LLaMA 3.3 70B, **gratuit**)
- **PWA** : installable sur mobile, notifications push
- **Export CSV** des données de parking
- **Rapports** et **prédictions** d'occupation
- **Sécurité** : bcrypt, rate limiting, CORS restreint, sessions Flask

---

## 🚀 Installation rapide

### 1. Cloner et installer les dépendances

```bash
cd FastPark
python -m venv fastpark_env
fastpark_env\Scripts\activate        # Windows
# ou : source fastpark_env/bin/activate  # Linux/Mac

pip install -r requirements.txt
```

### 2. Configurer les variables d'environnement

```bash
# Copier le fichier d'exemple
copy .env.example .env               # Windows
# ou : cp .env.example .env          # Linux/Mac

# Éditer .env et remplir les valeurs
```

### 3. Activer l'IA (optionnel mais recommandé — **GRATUIT**)

1. Va sur **https://console.groq.com**
2. Crée un compte (Google ou GitHub — sans carte bancaire)
3. Clique sur **API Keys** → **Create API Key**
4. Copie la clé (commence par `gsk_...`)
5. Dans `.env`, colle-la :
   ```
   GROQ_API_KEY=gsk_ta_vraie_cle_ici
   ```

> Sans clé Groq, le chatbot fonctionne en mode local avec des réponses prédéfinies.

### 4. Lancer l'application

```bash
python app.py
```

Ouvre **http://localhost:5000** dans ton navigateur.

**Compte admin par défaut :**
- Identifiant : `admin`
- Mot de passe : défini dans `ADMIN_PASSWORD` du `.env` (par défaut `fastpark123`)

---

## 📡 Simulateur de capteurs (sans matériel IoT)

Si tu n'as pas de capteurs physiques, utilise le simulateur :

```bash
# Dans un second terminal (avec l'env activé) :
python simulateur_avance.py
```

Le simulateur publie des données MQTT réalistes pour toutes les universités.

---

## 🏗️ Architecture

```
FastPark/
├── app.py                  # Serveur Flask principal (routes API + MQTT)
├── mcp.py                  # Assistant IA (Groq LLaMA 3.3 + fallback local)
├── simulateur_avance.py    # Simulateur de capteurs MQTT
├── parking.db              # Base SQLite (auto-créée au 1er lancement)
├── .env                    # Variables d'environnement (non versionné)
├── .env.example            # Template de configuration
├── requirements.txt        # Dépendances Python
│
├── login.html              # Page de connexion
├── register.html           # Page d'inscription
├── dashboard.html          # Tableau de bord principal
├── checkin.html            # Interface agent de sécurité (scan QR)
├── admin_health.html       # Monitoring système
│
├── static/                 # Icônes PWA
├── manifest.json           # Manifest PWA
├── sw.js                   # Service Worker (mode offline)
└── test_1_capteur.ino      # Firmware Arduino (capteur réel)
```

---

## 🔌 API Endpoints

| Méthode | Route | Description |
|---------|-------|-------------|
| POST | `/api/login` | Connexion (limité à 5/min) |
| POST | `/api/register` | Inscription (limité à 5/min) |
| POST | `/api/logout` | Déconnexion |
| GET | `/api/check_auth` | Vérification session |
| GET | `/api/spots` | Liste des places (filtrée par université) |
| GET | `/api/stats` | Statistiques globales |
| POST | `/api/reserve` | Réserver une place |
| POST | `/api/cancel_reservation` | Annuler une réservation |
| GET | `/api/my_reservation` | Réservation active de l'utilisateur |
| POST | `/api/validate_checkin` | Valider un check-in QR |
| GET | `/api/checkin_history` | Historique check-ins du jour |
| POST | `/api/chat` | Question à l'assistant IA |
| GET | `/api/chat_history` | Historique de conversation |
| GET | `/api/report` | Rapport textuel |
| GET | `/api/predict` | Prédiction d'occupation |
| GET | `/api/export/csv` | Export CSV des données |
| GET | `/api/university_gps` | Coordonnées GPS des universités |
| GET | `/api/health` | État du système |

---

## 🗄️ Structure de la base de données

| Table | Description |
|-------|-------------|
| `parking_spots` | État de chaque place (statut, batterie, température) |
| `users` | Comptes utilisateurs (bcrypt) |
| `reservations` | Réservations actives/expirées |
| `qr_codes` | Codes QR générés |
| `parking_history` | Historique des changements de statut |
| `chat_history` | Conversations avec l'IA (persistées en DB) |
| `checkin_log` | Log des check-ins validés par les agents |

---

## 🔒 Sécurité

- **Mots de passe** : hashés avec bcrypt (migration automatique depuis SHA-256)
- **Sessions** : Flask sessions signées avec SECRET_KEY aléatoire
- **Rate limiting** : 5 tentatives/min sur login et register
- **CORS** : restreint aux origines définies dans `ALLOWED_ORIGINS`
- **Validation** : username (3-30 chars, alphanumérique), email (regex), mot de passe (min 8 chars)

---

## 🌐 Déploiement Docker

```bash
docker-compose up -d
```

Variables d'environnement injectées via le `.env` ou `docker-compose.yml`.

---

## 📦 Dépendances principales

```
Flask==3.0.0
flask-cors==4.0.0
flask-limiter==3.5.0
paho-mqtt==1.6.1
qrcode[pil]==7.4.2
bcrypt==4.1.3
gunicorn==21.2.0
```

> **Groq** est appelé via `urllib` (stdlib Python) — aucune dépendance supplémentaire.

---

## 👨‍💻 Équipe

| Rôle | Nom |
|------|-----|
| CEO + UX | **Eliman** |
| CTO + Dev | **Moussa Diakite** |
| Lead Dev + QA | **Abdoul Majid** |

**Superviseur :** Prof. Marouane Slaitane  
Génie Informatique, Université Mundiapolis, Casablanca  
Projet FastPark — 2025/2026
