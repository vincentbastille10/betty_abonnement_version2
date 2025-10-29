from flask import Flask, render_template, request, redirect, url_for, abort
import os, sqlite3, uuid, stripe, sys
from dotenv import load_dotenv

# ------------------------------
# CONFIGURATION
# ------------------------------
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-me")

# Stripe
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
PRICE_ID = os.getenv("PRICE_ID")
BASE_URL = os.getenv("BASE_URL", "https://betty-abonnement-version2.vercel.app")

# Base SQLite (stockée dans /tmp pour compatibilité Vercel)
DB_PATH = "/tmp/users.db"


# ------------------------------
# DATABASE
# ------------------------------
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


# ------------------------------
# ROUTES
# ------------------------------

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/config', methods=['GET', 'POST'])
def config_page():
    if request.method == 'POST':
        # Redirection directe avec paramètres GET
        color = request.form.get('color_hex')
        avatar = request.form.get('avatar_key')
        persona = request.form.get('persona')
        pack = request.form.get('pack')
        return redirect(url_for('inscription', color=color, avatar=avatar, persona=persona, pack=pack))

    palette = ['#4F46E5', '#16A34A', '#DC2626', '#EA580C', '#0891B2', '#7C3AED']
    packs = ['avocat', 'medecin', 'immo']
    personas = ['neutre', 'chaleureux', 'expert']
    return render_template('config.html', palette=palette, packs=packs, personas=personas)


@app.route('/inscription', methods=['GET', 'POST'])
def inscription():
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
        script = f'<script src="{BASE_URL}/static/js/embed.js?bot_id={bot_id}"></script>'

    return render_template('recap.html', script_snippet=script)


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


# ------------------------------
# LOG DE DÉMARRAGE (DEBUG)
# ------------------------------
def startup_log():
    print("🚀 Betty Abonnement (Vercel Edition)")
    print(f"🐍 Python {sys.version.split()[0]}")
    print(f"🌍 BASE_URL : {BASE_URL}")
    print(f"💾 Database path : {DB_PATH}")
    print(f"💳 Stripe PRICE_ID : {PRICE_ID}")
    print(f"📡 Routes : {[r.rule for r in app.url_map.iter_rules()]}")
    print("=====================================")


startup_log()

# ------------------------------
# EXECUTION LOCALE
# ------------------------------
if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000, debug=True)
