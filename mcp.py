"""
FastPark MCP -- Assistant IA + contexte parking
- Utilise l'API GROQ (100% gratuite, sans carte bancaire)
  Modele : llama-3.3-70b-versatile
  Inscription : https://console.groq.com
- Fallback local intelligent si la cle est absente
- db_path injecte depuis app.py (chemin absolu)
- Chargement automatique du .env
"""

import sqlite3
import os
import json
import urllib.request
import urllib.error
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# Chargement automatique du fichier .env
# ─────────────────────────────────────────────────────────────
def _load_dotenv():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
    if not os.path.exists(env_path):
        return
    with open(env_path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, val = line.partition('=')
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val

_load_dotenv()


class FastParkMCP:
    GROQ_MODEL = "llama-3.3-70b-versatile"
    GROQ_URL   = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(self, db_path: str = "parking.db"):
        self.db_path = db_path

    @property
    def api_key(self) -> str:
        return os.environ.get("GROQ_API_KEY", "").strip()

    # ─────────────────────────────────────────────────────────
    # Helpers DB
    # ─────────────────────────────────────────────────────────
    def _query(self, sql: str, params=()):
        from db import USE_POSTGRES
        if USE_POSTGRES:
            import psycopg2
            DATABASE_URL = os.environ.get("DATABASE_URL", "")
            if DATABASE_URL.startswith("postgres://"):
                DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
            # Convertir les ? en %s pour psycopg2
            sql_pg = sql.replace("?", "%s")
            with psycopg2.connect(DATABASE_URL) as conn:
                c = conn.cursor()
                c.execute(sql_pg, params)
                return c.fetchall()
        else:
            with sqlite3.connect(self.db_path) as conn:
                c = conn.cursor()
                c.execute(sql, params)
                return c.fetchall()

    def get_all_universities(self):
        rows = self._query("SELECT DISTINCT university FROM parking_spots ORDER BY university")
        return [r[0] for r in rows]

    def get_parking_context(self, university=None) -> dict:
        if university and university != "all":
            rows_total  = self._query("SELECT COUNT(*) FROM parking_spots WHERE university=?", (university,))
            rows_status = self._query("SELECT status, COUNT(*) FROM parking_spots WHERE university=? GROUP BY status", (university,))
            rows_bat    = self._query("SELECT AVG(battery) FROM parking_spots WHERE university=? AND battery>0", (university,))
        else:
            rows_total  = self._query("SELECT COUNT(*) FROM parking_spots")
            rows_status = self._query("SELECT status, COUNT(*) FROM parking_spots GROUP BY status")
            rows_bat    = self._query("SELECT AVG(battery) FROM parking_spots WHERE battery>0")

        total    = rows_total[0][0] if rows_total else 0
        sc       = {r[0]: r[1] for r in rows_status}
        avg_bat  = rows_bat[0][0] or 0
        free     = sc.get("free", 0)
        occupied = sc.get("occupied", 0)
        reserved = sc.get("reserved", 0)

        return {
            "total_spots":    total,
            "free_spots":     free,
            "occupied_spots": occupied,
            "reserved_spots": reserved,
            "occupancy_rate": round(((occupied + reserved) / total) * 100, 1) if total else 0,
            "avg_battery":    round(avg_bat, 1),
        }

    def get_university_summary(self) -> list:
        rows = self._query("""
            SELECT university,
                   COUNT(CASE WHEN status='free'     THEN 1 END),
                   COUNT(CASE WHEN status='occupied' THEN 1 END),
                   COUNT(CASE WHEN status='reserved' THEN 1 END),
                   COUNT(*),
                   AVG(battery)
            FROM parking_spots
            GROUP BY university
            ORDER BY university
        """)
        return [
            {
                "university":     r[0],
                "free":           r[1],
                "occupied":       r[2],
                "reserved":       r[3],
                "total":          r[4],
                "avg_battery":    round(r[5] or 0, 1),
                "occupancy_rate": round(((r[2] + r[3]) / r[4]) * 100, 1) if r[4] else 0,
            }
            for r in rows
        ]

    # ─────────────────────────────────────────────────────────
    # System prompt avec contexte temps reel
    # ─────────────────────────────────────────────────────────
    def _build_system_prompt(self, university=None) -> str:
        ctx      = self.get_parking_context(university)
        uni_data = self.get_university_summary()
        critical = [u for u in uni_data if u["avg_battery"] < 20]
        top3     = sorted(uni_data, key=lambda u: u["occupancy_rate"], reverse=True)[:3]

        uni_lines = "\n".join(
            f"  - {u['university']}: {u['free']}/{u['total']} libres "
            f"(occupation {u['occupancy_rate']}%, batterie {u['avg_battery']}%)"
            for u in uni_data
        )
        top3_lines = "\n".join(
            f"  {i+1}. {u['university']} ({u['occupancy_rate']}%)"
            for i, u in enumerate(top3)
        )
        alert_lines = ""
        if critical:
            alert_lines = "\nALERTES BATTERIE CRITIQUE (<20%) :\n" + "\n".join(
                f"  - {u['university']} : {u['avg_battery']}%" for u in critical
            )

        scope = f"pour {university}" if university and university != "all" else "global (toutes universites)"
        now   = datetime.now().strftime("%d/%m/%Y a %H:%M")

        return (
            "Tu es l'assistant intelligent du systeme FastPark - gestion de parkings "
            "pour les universites de Casablanca, Maroc.\n\n"
            f"=== DONNEES TEMPS REEL ({now}) - {scope} ===\n\n"
            "RESUME GLOBAL :\n"
            f"  Total : {ctx['total_spots']} places | Libres : {ctx['free_spots']} | "
            f"Occupees : {ctx['occupied_spots']} | Reservees : {ctx['reserved_spots']}\n"
            f"  Taux d'occupation : {ctx['occupancy_rate']}% | Batterie capteurs moyenne : {ctx['avg_battery']}%\n\n"
            f"ETAT PAR UNIVERSITE :\n{uni_lines}\n\n"
            f"TOP 3 LES PLUS CHARGEES :\n{top3_lines}\n"
            f"{alert_lines}\n\n"
            "=== TES REGLES ===\n"
            "1. Reponds TOUJOURS en francais, de facon claire et avec des emojis.\n"
            "2. Utilise UNIQUEMENT les donnees ci-dessus. Ne les invente pas.\n"
            "3. Pour une universite precise, fais une correspondance partielle sur le nom.\n"
            "4. Si quelqu'un envoie un message vague, reponds poliment avec des exemples.\n"
            "5. Propose des alternatives si une universite est pleine.\n"
            "6. Sois concis : maximum 5 lignes par reponse.\n"
        )

    # ─────────────────────────────────────────────────────────
    # Appel Groq API (gratuit)
    # ─────────────────────────────────────────────────────────
    def _call_llm(self, question: str, university=None):
        key = self.api_key
        if not key:
            return None

        payload = json.dumps({
            "model":       self.GROQ_MODEL,
            "max_tokens":  500,
            "temperature": 0.3,
            "messages": [
                {"role": "system", "content": self._build_system_prompt(university)},
                {"role": "user",   "content": question},
            ],
        }).encode("utf-8")

        req = urllib.request.Request(
            self.GROQ_URL,
            data=payload, method="POST",
            headers={
                "Content-Type":  "application/json",
                "Authorization": f"Bearer {key}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                return body["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            err = e.read().decode()[:300]
            raise RuntimeError(f"Groq API HTTP {e.code}: {err}")
        except Exception as e:
            raise RuntimeError(f"Groq API error: {e}")

    # ─────────────────────────────────────────────────────────
    # Fallback local intelligent
    # ─────────────────────────────────────────────────────────
    def _ask_local(self, question: str, university=None) -> str:
        ctx = self.get_parking_context(university)
        q   = question.lower().strip()

        unis      = self.get_all_universities()
        found_uni = None
        for uni in unis:
            parts = [uni.lower()] + [p.lower().strip() for p in uni.split("-")]
            if any(p and p in q for p in parts):
                found_uni = uni
                break

        if found_uni:
            uc = self.get_parking_context(found_uni)
            if any(w in q for w in ["libre", "disponible", "dispo", "place"]):
                return (f"Universite : {found_uni}\n"
                        f"{uc['free_spots']} places libres sur {uc['total_spots']} "
                        f"(occupation {uc['occupancy_rate']}%)")
            elif any(w in q for w in ["taux", "occup", "plein", "charg"]):
                return (f"Universite : {found_uni}\n"
                        f"Taux d'occupation : {uc['occupancy_rate']}% "
                        f"({uc['occupied_spots']}/{uc['total_spots']} occupees)")
            elif "batterie" in q:
                return f"{found_uni} - Batterie moyenne : {uc['avg_battery']}%"
            elif "reserv" in q:
                return f"{found_uni} - {uc['reserved_spots']} place(s) reservee(s)"
            return (f"{found_uni}\n"
                    f"Libres : {uc['free_spots']} | Occupees : {uc['occupied_spots']} | "
                    f"Reservees : {uc['reserved_spots']} | Taux : {uc['occupancy_rate']}%")

        if any(w in q for w in ["chaque", "toutes", "liste", "universite", "par uni"]):
            summary = self.get_university_summary()
            lines   = "\n".join(
                f"{u['university'][:40]} : {u['free']}/{u['total']} libres ({u['occupancy_rate']}%)"
                for u in summary
            )
            return f"Etat de toutes les universites :\n\n{lines}"

        if any(w in q for w in ["libre", "disponible", "dispo", "combien", "place"]):
            return (f"{ctx['free_spots']} places libres sur {ctx['total_spots']}\n"
                    f"Taux d'occupation global : {ctx['occupancy_rate']}%")

        if any(w in q for w in ["taux", "pourcentage", "occupation", "stat"]):
            return (f"Taux d'occupation global : {ctx['occupancy_rate']}%\n"
                    f"Occupees : {ctx['occupied_spots']} | Libres : {ctx['free_spots']} | "
                    f"Reservees : {ctx['reserved_spots']}")

        if "batterie" in q or "battery" in q or "capteur" in q:
            return f"Batterie moyenne des capteurs : {ctx['avg_battery']}%"

        if "reserv" in q:
            return f"{ctx['reserved_spots']} places reservees sur {ctx['total_spots']}"

        if any(w in q for w in ["bonjour", "salut", "bonsoir", "hello", "hi", "salam", "bsr", "bjr"]):
            return (f"Bonjour ! Je suis l'assistant FastPark.\n"
                    f"En ce moment : {ctx['free_spots']} places libres sur {ctx['total_spots']}.\n\n"
                    f"Essayez : Combien de places libres ? / Taux EMSI / Etat toutes universites")

        if "merci" in q or "thank" in q:
            return "Avec plaisir ! N'hesitez pas si vous avez d'autres questions."

        if any(w in q for w in ["aide", "help", "quoi", "comment", "que peux"]):
            return ("Je peux vous aider avec :\n"
                    "- Combien de places libres ?\n"
                    "- Taux d'occupation de l'EMSI\n"
                    "- Etat des batteries\n"
                    "- Prediction dans 2h")

        return (f"Je suis l'assistant FastPark, dedie aux parkings universitaires de Casablanca.\n\n"
                f"En ce moment : {ctx['free_spots']} places libres disponibles.\n\n"
                f"Exemples : Combien de places a Mundiapolis ? / Taux global / Prediction dans 1h")

    # ─────────────────────────────────────────────────────────
    # Methode principale ask()
    # ─────────────────────────────────────────────────────────
    def ask(self, question: str, university=None) -> str:
        try:
            result = self._call_llm(question, university)
            if result:
                return result
        except Exception as e:
            logger.warning(f"Groq indisponible, fallback local : {e}")
        return self._ask_local(question, university)

    # ─────────────────────────────────────────────────────────
    # Rapport texte
    # ─────────────────────────────────────────────────────────
    def generate_report(self, university=None) -> str:
        ctx = self.get_parking_context(university)
        now = datetime.now().strftime('%d/%m/%Y %H:%M')

        lines = [
            "=" * 64,
            f"  RAPPORT FASTPARK -- {now}",
            "=" * 64,
            f"  Places libres     : {ctx['free_spots']:>3} / {ctx['total_spots']}",
            f"  Places occupees   : {ctx['occupied_spots']:>3} / {ctx['total_spots']}",
            f"  Places reservees  : {ctx['reserved_spots']:>3} / {ctx['total_spots']}",
            f"  Taux d'occupation : {ctx['occupancy_rate']:>5.1f}%",
            f"  Batterie moyenne  : {ctx['avg_battery']:>5.1f}%",
            "=" * 64,
        ]

        if not university or university == "all":
            lines.append("\nDETAIL PAR UNIVERSITE :")
            for u in self.get_university_summary():
                bar   = "#" * int(u['occupancy_rate'] / 10) + "-" * (10 - int(u['occupancy_rate'] / 10))
                lines.append(f"  {u['university'][:35]:<35} [{bar}] {u['occupancy_rate']:>5.1f}%")

        return "\n".join(lines)

    # ─────────────────────────────────────────────────────────
    # CORRECTIF : Prediction basee sur l'historique reel
    # ─────────────────────────────────────────────────────────
    def get_hourly_occupancy_profile(self, university=None) -> dict:
        """
        Calcule le taux d'occupation moyen par heure (0-23)
        a partir de parking_history (30 derniers jours).
        Compatible SQLite et PostgreSQL.
        """
        from db import USE_POSTGRES

        if USE_POSTGRES:
            hour_expr = "EXTRACT(HOUR FROM changed_at::timestamp)::int"
            date_filter = "changed_at >= NOW() - INTERVAL '30 days'"
        else:
            hour_expr = "CAST(strftime('%H', changed_at) AS INTEGER)"
            date_filter = "changed_at >= datetime('now', '-30 days')"

        if university and university != "all":
            rows = self._query(f"""
                SELECT {hour_expr} AS heure,
                       SUM(CASE WHEN status='occupied' THEN 1 ELSE 0 END) AS occ,
                       COUNT(*) AS total
                FROM parking_history
                WHERE university=?
                  AND {date_filter}
                GROUP BY heure
                ORDER BY heure
            """, (university,))
        else:
            rows = self._query(f"""
                SELECT {hour_expr} AS heure,
                       SUM(CASE WHEN status='occupied' THEN 1 ELSE 0 END) AS occ,
                       COUNT(*) AS total
                FROM parking_history
                WHERE {date_filter}
                GROUP BY heure
                ORDER BY heure
            """)

        profile = {}
        for row in rows:
            heure = int(row[0])
            occ   = row[1] or 0
            total = row[2] or 1
            profile[heure] = round((occ / total) * 100, 1)
        return profile

    def predict_occupation(self, hours_ahead: int = 1, university=None) -> str:
        ctx          = self.get_parking_context(university)
        current_rate = ctx['occupancy_rate']
        now_hour     = datetime.now().hour
        target_hour  = (now_hour + hours_ahead) % 24
        scope        = f" pour {university}" if university and university != "all" else " global"

        # --- Priorite 1 : historique reel (parking_history) ---
        profile = self.get_hourly_occupancy_profile(university)

        if len(profile) >= 6:
            now_hist    = profile.get(now_hour)
            target_hist = profile.get(target_hour)

            if now_hist is not None and target_hist is not None and now_hist > 0:
                # Ratio historique applique au taux actuel
                predicted = round(min(98, max(2, current_rate * (target_hist / now_hist))), 1)
                source    = "historique reel (30 derniers jours)"
            elif target_hist is not None:
                predicted = target_hist
                source    = "historique reel (30 derniers jours)"
            else:
                # Interpolation lineaire entre les heures connues les plus proches
                known  = sorted(profile.keys())
                before = [h for h in known if h <= target_hour]
                after  = [h for h in known if h > target_hour]
                if before and after:
                    h1, h2    = before[-1], after[0]
                    t         = (target_hour - h1) / (h2 - h1)
                    predicted = round(profile[h1] + t * (profile[h2] - profile[h1]), 1)
                elif before:
                    predicted = profile[before[-1]]
                else:
                    predicted = profile[after[0]]
                source = "historique reel (interpolation)"

            hist_now    = profile.get(now_hour, current_rate)
            hist_target = profile.get(target_hour, predicted)
            trend = ("en hausse" if hist_target > hist_now + 5
                     else "en baisse" if hist_target < hist_now - 5
                     else "stable")
            peak_h    = max(profile, key=profile.get)
            peak_rate = profile[peak_h]

            return (
                f"Prediction{scope} dans {hours_ahead}h :\n"
                f"Taux estime : {predicted}% ({trend})\n"
                f"Actuellement : {current_rate}% - {ctx['free_spots']} places libres\n"
                f"Source : {source}\n"
                f"Pic habituel : {peak_h}h ({peak_rate}%) - {len(profile)} heures analysees"
            )

        # --- Priorite 2 : coefficients statiques (si < 6 heures d'historique) ---
        PEAK_COEF   = {8: 12, 9: 10, 10: 6, 12: 8, 13: 7, 17: 9, 18: 6}
        coef_now    = PEAK_COEF.get(now_hour, 2)
        coef_future = PEAK_COEF.get(target_hour, 2)
        predicted   = round(min(98, max(5, current_rate + ((coef_now + coef_future) / 2) * hours_ahead)), 1)
        trend       = "en hausse" if coef_future >= 6 else "en baisse" if coef_future <= 2 else "stable"

        return (
            f"Prediction{scope} dans {hours_ahead}h :\n"
            f"Taux estime : {predicted}% ({trend})\n"
            f"Actuellement : {current_rate}% - {ctx['free_spots']} places libres\n"
            f"Source : coefficients statiques (historique insuffisant - {len(profile)} heures connues)"
        )


# Instance globale — db_path reassigne depuis app.py
mcp = FastParkMCP()
