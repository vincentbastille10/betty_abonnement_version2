from flask import Flask, render_template, request, redirect, url_for, abort
import os, sys, sqlite3, uuid, stripe, yaml
from dotenv import load_dotenv

# ==========================
# CONFIGURATION GLOBALE
# ==========================
load_dotenv()  # utile en local, ignoré sur Vercel

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-me")

# Stripe (clé publique facultative dans ce flux)
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
PRICE_ID = os.getenv("PRICE_ID")
BASE_URL = os.getenv("BASE_URL", "https://betty-abonnement-version2.vercel.app")

# LLM (OpenAI)
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "90"))

# DB (Vercel => /tmp persistant pendant le runtime uniquement)
DB_PATH = "/tmp/users.db"

# Packs YAML
PACK_DIR = "data/packs"


# ==========================
# OUTILS / DB
# ==========================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE,
        name TEXT,
        bot_id TEXT,
        active INTEGER DEFAULT 0,
        session_id TEXT
    )
    """)
    conn.commit()
    conn.close()

def get_conn():
    if not os.path.exists(DB_PATH):
        init_db()
    return sqlite3.connect(DB_PATH)


# ==========================
# PACKS YAML + PROMPT
# ==========================
def load_pack(pack_name: str) -> dict:
    path = os.path.join(PACK_DIR, f"{pack_name}.yaml")
    if not os.path.exists(path):
        return {"name": pack_name, "greeting": "Bonjour, je suis Betty.", "faqs": []}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def build_system_prompt(pack: dict, persona: str | None = None) -> str:
    persona = persona or "neutre"
    faqs = pack.get("faqs", [])
    faq_lines = "\n".join([f"- Q: {f.get('q')} | A: {f.get('a')}" for f in faqs])
    return (
        "Tu es Betty, chatbot métier qui QUALIFIE les leads de façon concise et polie.\n"
        f"Persona: {persona}. Métier: {pack.get('name','')}\n"
        "Contrainte stricte: MAX 90 tokens de sortie, pas de pavé. "
        "Pose 1-2 questions pour qualifier si nécessaire. "
        "N'invente pas d'informations internes au cabinet/entreprise.\n"
        "FAQ de référence (utilise-la si pertinent) :\n" + faq_lines
    )


# ==========================
# ROUTES PAGES
# ==========================
@app.route('/')
def index():
    # Page 1 — Découverte (démo)
    return render_template('index.html')

@app.route('/config', methods=['GET', 'POST'])
def config_page():
    # Page 2 — Configuration
    if request.method == 'POST':
        color = request.form.get('color_hex')
        avatar = request.form.get('avatar_key')
        persona = request.form.get('persona')
        pack = request.form.get('pack')
        # On passe la config en querystring vers /inscription
        return redirect(url_for('inscription', color=color, avatar=avatar, persona=persona, pack=pack))

    palette = ['#4F46E5', '#16A34A', '#DC2626', '#EA580C', '#0891B2', '#7C3AED']
    packs = ['avocat', 'medecin', 'immo']
    personas = ['neutre', 'chaleureux', 'expert']
    return render_template('config.html', palette=palette, packs=packs, personas=personas)

@app.route('/inscription', methods=['GET', 'POST'])
def inscription():
    # Page 3 — Inscription + redirection Stripe Checkout
    bot_cfg = {
        "color": request.args.get("color"),
        "avatar": request.args.get("avatar"),
        "persona": request.args.get("persona"),
        "pack": request.args.get("pack"),
    }

    if request.method == 'POST':
        email = request.form.get("email")
        name = request.form.get("name")
        if not email:
            abort(400, "Email requis")

        conn = get_conn()
        c = conn.cursor()
        bot_id = str(uuid.uuid4())
        c.execute("INSERT OR IGNORE INTO users (email, name, bot_id) VALUES (?, ?, ?)", (email, name, bot_id))
        conn.commit()

        try:
            checkout_session = stripe.checkout.Session.create(
                mode='subscription',
                line_items=[{'price': PRICE_ID, 'quantity': 1}],
                customer_email=email,
                success_url=f"{BASE_URL}/recap?session_id={{CHECKOUT_SESSION_ID}}",
                cancel_url=f"{BASE_URL}/inscription",
            )
        except Exception as e:
            conn.close()
            return render_template('inscription.html', error=str(e), bot_cfg=bot_cfg)

        c.execute("UPDATE users SET session_id=? WHERE email=?", (checkout_session.id, email))
        conn.commit()
        conn.close()

        print(f"[✅ STRIPE] Checkout lancé pour {email}")
        return redirect(checkout_session.url, code=303)

    return render_template('inscription.html', bot_cfg=bot_cfg)

@app.route('/recap')
def recap():
    # Page 4 — Récap après paiement
    session_id = request.args.get("session_id")
    if not session_id:
        return redirect(url_for("index"))

    try:
        checkout = stripe.checkout.Session.retrieve(session_id)
    except Exception:
        return "Erreur de vérification Stripe", 400

    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT email, bot_id FROM users WHERE session_id=?", (session_id,))
    row = c.fetchone()
    conn.close()

    script = None
    if row and checkout.status == "complete":
        email, bot_id = row
        conn = get_conn()
        conn.execute("UPDATE users SET active=1 WHERE session_id=?", (session_id,))
        conn.commit()
        conn.close()
        # Snippet d’intégration (ici on pointe vers notre embed.js)
        script = f'<script src="{BASE_URL}/static/js/embed.js?bot_id={bot_id}"></script>'

    return render_template('recap.html', script_snippet=script)


# ==========================
# ROUTE CHAT (UI + API LLM)
# ==========================
@app.route('/chat')
def chat_page():
    """
    Mini UI de chat.
    Paramètres GET:
      - bot_id (facultatif pour l’instant)
      - pack=avocat|medecin|immo (defaut: immo)
      - persona=neutre|chaleureux|expert
    """
    pack_name = request.args.get("pack", "immo")
    persona = request.args.get("persona", "neutre")
    pack = load_pack(pack_name)
    greeting = pack.get("greeting", "Bonjour, je suis Betty.")
    return render_template('chat.html', pack_name=pack_name, persona=persona, greeting=greeting)

@app.post('/api/ask')
def api_ask():
    """
    Body JSON attendu:
    {
      "messages": [{"role":"user","content":"..."}, ...],   # historique maintenu côté client
      "pack": "avocat|medecin|immo",
      "persona": "neutre|chaleureux|expert"
    }
    Répond: {"reply":"..."}
    """
    data = request.get_json(force=True, silent=True) or {}
    msgs = data.get("messages") or []
    pack_name = data.get("pack", "immo")
    persona = data.get("persona", "neutre")

    pack = load_pack(pack_name)
    system_prompt = build_system_prompt(pack, persona)

    # Fallback si pas de clé LLM
    if LLM_PROVIDER != "openai" or not OPENAI_API_KEY:
        return {"reply": "Mode démo sans LLM actif. Ajoutez OPENAI_API_KEY pour activer la réponse."}

    # OpenAI v1
    try:
        from openai import OpenAI
        oai = OpenAI(api_key=OPENAI_API_KEY)
        chat_messages = [{"role": "system", "content": system_prompt}] + msgs
        resp = oai.chat.completions.create(
            model=LLM_MODEL,
            messages=chat_messages,
            max_tokens=LLM_MAX_TOKENS,
            temperature=0.4,
        )
        text = (resp.choices[0].message.content or "").strip()
        return {"reply": text}
    except Exception as e:
        return {"reply": f"[Erreur LLM] {e}"}, 200


# ==========================
# WEBHOOK STRIPE
# ==========================
@app.route('/webhook', methods=['POST'])
def webhook():
    payload = request.data
    sig_header = request.headers.get("Stripe-Signature")
    endpoint_secret = os.getenv("STRIPE_WEBHOOK_SECRET")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, endpoint_secret)
    except Exception:
        return "Webhook invalide", 400

    if event["type"] == "checkout.session.completed":
        session_obj = event["data"]["object"]
        session_id = session_obj["id"]
        conn = get_conn()
        conn.execute("UPDATE users SET active=1 WHERE session_id=?", (session_id,))
        conn.commit()
        conn.close()
        print(f"[💳 WEBHOOK] Paiement validé pour session {session_id}")

    return '', 200


# ==========================
# LOG DEMARRAGE
# ==========================
def startup_log():
    print("🚀 Betty Abonnement v2 (Vercel)")
    print(f"🐍 Python: {sys.version.split()[0]}")
    print(f"🌍 BASE_URL: {BASE_URL}")
    print(f"💾 DB_PATH: {DB_PATH}")
    print(f"💳 PRICE_ID: {PRICE_ID}")
    print(f"🧠 LLM: provider={LLM_PROVIDER} model={LLM_MODEL} max_tokens={LLM_MAX_TOKENS}")
    print(f"📡 Routes: {[r.rule for r in app.url_map.iter_rules()]}")
    print("=====================================")

startup_log()


# ==========================
# RUN LOCAL
# ==========================
if __name__ == '__main__':
    # En local seulement
    app.run(host="0.0.0.0", port=5000, debug=True)
