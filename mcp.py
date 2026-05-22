"""
FastPark MCP — Assistant IA + contexte parking
- Utilise l'API GROQ (100% gratuite, sans carte bancaire)
  Modèle : llama-3.3-70b-versatile
  Inscription : https://console.groq.com
- Fallback local intelligent si la clé est absente
- db_path injecté depuis app.py (chemin absolu)
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
    # ── Groq API — 100% gratuit, pas de carte bancaire ───────
    # Inscription : https://console.groq.com → API Keys → Create
    GROQ_MODEL = "llama-3.3-70b-versatile"
    GROQ_URL   = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(self, db_path: str = "parking.db"):
        self.db_path = db_path

    @property
    def api_key(self) -> str:
        """Relu à chaque appel — prend en compte le .env chargé après l'import."""
        return os.environ.get("GROQ_API_KEY", "").strip()

    # ─────────────────────────────────────────────────────────
    # Helpers DB
    # ─────────────────────────────────────────────────────────
    def _query(self, sql: str, params=()):
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
    # System prompt avec contexte temps réel
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
            alert_lines = "\n⚠️ ALERTES BATTERIE CRITIQUE (<20%) :\n" + "\n".join(
                f"  - {u['university']} : {u['avg_battery']}%" for u in critical
            )

        scope = f"pour {university}" if university and university != "all" else "global (toutes universités)"
        now   = datetime.now().strftime("%d/%m/%Y à %H:%M")

        return f"""Tu es l'assistant intelligent du système FastPark — gestion de parkings pour les universités de Casablanca, Maroc.

=== DONNÉES TEMPS RÉEL ({now}) — {scope} ===

📊 RÉSUMÉ GLOBAL :
  Total : {ctx['total_spots']} places | Libres : {ctx['free_spots']} | Occupées : {ctx['occupied_spots']} | Réservées : {ctx['reserved_spots']}
  Taux d'occupation : {ctx['occupancy_rate']}% | Batterie capteurs moyenne : {ctx['avg_battery']}%

🏫 ÉTAT PAR UNIVERSITÉ :
{uni_lines}

🔝 TOP 3 LES PLUS CHARGÉES :
{top3_lines}
{alert_lines}

=== TES RÈGLES ===
1. Réponds TOUJOURS en français, de façon claire et avec des emojis.
2. Utilise UNIQUEMENT les données ci-dessus. Ne les invente pas.
3. Pour une université précise, fais une correspondance partielle sur le nom (ex: "EMSI" → "EMSI Casablanca").
4. Si quelqu'un envoie un message vague ("c", "test", "ok"), réponds poliment que tu es disponible pour des questions sur le parking FastPark et donne 2-3 exemples concrets.
5. Propose des alternatives si une université est pleine.
6. Sois concis : maximum 5 lignes par réponse.
"""

    # ─────────────────────────────────────────────────────────
    # Appel Groq API (gratuit)
    # ─────────────────────────────────────────────────────────
    def _call_llm(self, question: str, university=None) -> str | None:
        """
        Appelle l'API Groq (format OpenAI compatible).
        Retourne None si clé absente — bascule sur le fallback local.
        """
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
    # Fallback local intelligent (si pas de clé)
    # ─────────────────────────────────────────────────────────
    def _ask_local(self, question: str, university=None) -> str:
        ctx = self.get_parking_context(university)
        q   = question.lower().strip()

        # Cherche une université mentionnée dans la question
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
                return (f"🏫 **{found_uni}**\n"
                        f"✅ **{uc['free_spots']} places libres** sur {uc['total_spots']} "
                        f"(occupation {uc['occupancy_rate']}%)")
            elif any(w in q for w in ["taux", "occup", "plein", "charg"]):
                return (f"📊 **{found_uni}**\n"
                        f"Taux d'occupation : **{uc['occupancy_rate']}%** "
                        f"({uc['occupied_spots']}/{uc['total_spots']} occupées)")
            elif "batterie" in q:
                return f"🔋 **{found_uni}** — Batterie moyenne : **{uc['avg_battery']}%**"
            elif "reserv" in q:
                return f"🟠 **{found_uni}** — {uc['reserved_spots']} place(s) réservée(s)"
            return (f"🏫 **{found_uni}**\n"
                    f"✅ Libres : {uc['free_spots']} | 🔴 Occupées : {uc['occupied_spots']} | "
                    f"🟠 Réservées : {uc['reserved_spots']} | 📈 Taux : {uc['occupancy_rate']}%")

        # Questions globales
        if any(w in q for w in ["chaque", "toutes", "liste", "universite", "université", "par uni", "toutes les"]):
            summary = self.get_university_summary()
            lines   = "\n".join(
                f"{'✅' if u['free'] > 0 else '🔴'} **{u['university'][:40]}** : "
                f"{u['free']}/{u['total']} libres ({u['occupancy_rate']}%)"
                for u in summary
            )
            return f"📊 **État de toutes les universités :**\n\n{lines}"

        if any(w in q for w in ["libre", "disponible", "dispo", "combien", "place"]):
            return (f"🔍 **{ctx['free_spots']} places libres** sur {ctx['total_spots']}\n"
                    f"📈 Taux d'occupation global : {ctx['occupancy_rate']}%")

        if any(w in q for w in ["taux", "pourcentage", "occupation", "stat"]):
            return (f"📈 **Taux d'occupation global : {ctx['occupancy_rate']}%**\n"
                    f"🔴 Occupées : {ctx['occupied_spots']} | ✅ Libres : {ctx['free_spots']} | "
                    f"🟠 Réservées : {ctx['reserved_spots']}")

        if "batterie" in q or "battery" in q or "capteur" in q:
            return f"🔋 **Batterie moyenne des capteurs : {ctx['avg_battery']}%**"

        if "reserv" in q:
            return f"🟠 **{ctx['reserved_spots']} places réservées** sur {ctx['total_spots']}"

        if any(w in q for w in ["bonjour", "salut", "bonsoir", "hello", "hi", "salam", "bsr", "bjr"]):
            return (f"👋 **Bonjour !** Je suis l'assistant FastPark.\n"
                    f"En ce moment : **{ctx['free_spots']} places libres** sur {ctx['total_spots']}.\n\n"
                    f"💡 Essayez :\n"
                    f"• *Combien de places libres ?*\n"
                    f"• *Taux d'occupation de l'EMSI*\n"
                    f"• *État de toutes les universités*")

        if "merci" in q or "thank" in q:
            return "🙏 Avec plaisir ! N'hésitez pas si vous avez d'autres questions."

        if any(w in q for w in ["aide", "help", "?", "quoi", "comment", "que peux"]):
            return ("❓ **Je peux vous aider avec :**\n"
                    "• *Combien de places libres ?*\n"
                    "• *Places libres par université*\n"
                    "• *Taux d'occupation de l'EMSI*\n"
                    "• *État des batteries*\n"
                    "• *Prédiction dans 2h*")

        # Réponse par défaut pour tout le reste (ex: "c", "ask", "test"...)
        return (f"🤖 Je suis l'assistant **FastPark**, dédié aux parkings universitaires de Casablanca.\n\n"
                f"En ce moment : ✅ **{ctx['free_spots']} places libres** disponibles.\n\n"
                f"💡 Exemples de questions :\n"
                f"• *Combien de places libres à Mundiapolis ?*\n"
                f"• *Quelle université a le plus de places libres ?*\n"
                f"• *Taux d'occupation global*")

    # ─────────────────────────────────────────────────────────
    # Méthode principale ask()
    # ─────────────────────────────────────────────────────────
    def ask(self, question: str, university=None) -> str:
        """
        1. Essaie Groq API (LLM gratuit, LLaMA 3.3 70B)
        2. Si pas de clé ou erreur → fallback local intelligent
        """
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
            "╔══════════════════════════════════════════════════════════════╗",
            f"║              📊 RAPPORT FASTPARK — {now}         ║",
            "╠══════════════════════════════════════════════════════════════╣",
            f"║  ✅ Places libres     : {ctx['free_spots']:>3} / {ctx['total_spots']:<6}                      ║",
            f"║  🔴 Places occupées   : {ctx['occupied_spots']:>3} / {ctx['total_spots']:<6}                      ║",
            f"║  🟠 Places réservées  : {ctx['reserved_spots']:>3} / {ctx['total_spots']:<6}                      ║",
            f"║  📈 Taux d'occupation : {ctx['occupancy_rate']:>5.1f}%                              ║",
            f"║  🔋 Batterie moyenne  : {ctx['avg_battery']:>5.1f}%                              ║",
            "╚══════════════════════════════════════════════════════════════╝",
        ]

        if not university or university == "all":
            lines.append("\n📋 DÉTAIL PAR UNIVERSITÉ :")
            for u in self.get_university_summary():
                bar   = "█" * int(u['occupancy_rate'] / 10) + "░" * (10 - int(u['occupancy_rate'] / 10))
                lines.append(f"  {u['university'][:35]:<35} [{bar}] {u['occupancy_rate']:>5.1f}%")

        return "\n".join(lines)

    # ─────────────────────────────────────────────────────────
    # Prédiction
    # ─────────────────────────────────────────────────────────
    def predict_occupation(self, hours_ahead: int = 1, university=None) -> str:
        ctx  = self.get_parking_context(university)
        hour = datetime.now().hour

        PEAK_COEF   = {8: 12, 9: 10, 10: 6, 12: 8, 13: 7, 17: 9, 18: 6}
        coef_now    = PEAK_COEF.get(hour, 2)
        coef_future = PEAK_COEF.get((hour + hours_ahead) % 24, 2)
        predicted   = min(98, max(5, ctx['occupancy_rate'] + ((coef_now + coef_future) / 2) * hours_ahead))
        trend       = "📈 en hausse" if coef_future >= 6 else "📉 en baisse" if coef_future <= 2 else "➡️ stable"
        target      = f" pour {university}" if university and university != "all" else " global"

        return (f"🔮 **Prédiction{target} dans {hours_ahead}h :**\n"
                f"Taux estimé : **{predicted:.0f}%** ({trend})\n"
                f"Actuellement : {ctx['occupancy_rate']}% · {ctx['free_spots']} places libres")


# Instance globale — db_path réassigné depuis app.py avec DB_PATH absolu
mcp = FastParkMCP()
