# app.py
from flask import Flask, render_template, request, jsonify, redirect, url_for
import os, yaml, requests
import stripe

app = Flask(__name__)

# =========================
# Config ENV (à définir)
# =========================
TOGETHER_API_KEY = os.getenv("TOGETHER_API_KEY") or "TA_CLE_TOGETHER_ICI"
TOGETHER_API_URL = "https://api.together.xyz/v1/chat/completions"
LLM_MODEL = os.getenv("LLM_MODEL") or "meta-llama/Meta-Llama-3-8B-Instruct"
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS") or 90)

stripe.api_key = os.getenv("STRIPE_SECRET_KEY") or "sk_test_xxx"
PRICE_ID = os.getenv("STRIPE_PRICE_ID") or "price_xxx"   # 29,99€/mois dans Stripe
BASE_URL = (os.getenv("BASE_URL") or "http://127.0.0.1:5000").rstrip("/")

# =========================
# Helpers
# =========================
def static_url(filename: str) -> str:
    # URL absolue pour servir les images dans l’embed/iframe
    return url_for("static", filename=filename, _external=True)

def load_pack_prompt(pack_name: str) -> str:
    path = f"data/packs/{pack_name}.yaml"
    if not os.path.exists(path):
        return "Tu es une assistante AI professionnelle. Réponds avec clarté et concision."
    with open(path, "r") as f:
        data = yaml.safe_load(f) or {}
    return data.get("prompt", "Tu es une assistante AI professionnelle.")

def query_llm(user_input: str, pack_name: str) -> str:
    """Appel LLM (Together)."""
    headers = {"Authorization": f"Bearer {TOGETHER_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": LLM_MODEL,
        "max_tokens": LLM_MAX_TOKENS,
        "messages": [
            {"role": "system", "content": load_pack_prompt(pack_name)},
            {"role": "user", "content": user_input}
        ]
    }
    try:
        r = requests.post(TOGETHER_API_URL, headers=headers, json=payload, timeout=20)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print("[LLM ERROR]", e)
        return "Désolé, une erreur est survenue lors de la génération de la réponse."

# =========================
# Mini “DB” en mémoire
# =========================
# Les avatars sont directement dans /static/ (ex: static/avocat.png, etc.)
BOTS = {
    "avocat-001":  {"pack": "avocat",  "name": "Betty (Avocat)",    "color": "#4F46E5", "avatar_file": "avocat.png"},
    "immo-002":    {"pack": "immo",    "name": "Betty (Immobilier)","color": "#16A34A", "avatar_file": "immo.png"},
    "medecin-003": {"pack": "medecin", "name": "Betty (Médecin)",   "color": "#0284C7", "avatar_file": "medecin.png"},
}

# =========================
# Pages
# =========================
@app.route("/")
def index():
    # Page 1 — découverte (ton template index.html affiche l’iframe de démo + bouton Configurer)
    return render_template("index.html", title="Découvrez Betty")

@app.route("/config", methods=["GET", "POST"])
def config_page():
    # Page 2 — configuration
    if request.method == "POST":
        pack  = request.form.get("pack", "avocat")
        color = request.form.get("color", "#4F46E5")
        avatar= request.form.get("avatar", "avocat.png")
        return redirect(url_for("inscription_page", pack=pack, color=color, avatar=avatar))
    return render_template("config.html", title="Configurer votre bot")

@app.route("/inscription", methods=["GET", "POST"])
def inscription_page():
    # Page 3 — inscription + redirection Stripe
    if request.method == "POST":
        email = request.form.get("email")
        pack  = request.args.get("pack", "avocat")
        color = request.args.get("color", "#4F46E5")
        avatar= request.args.get("avatar", "avocat.png")

        # Créer la session Stripe (mode abonnement)
        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": PRICE_ID, "quantity": 1}],
            customer_email=email,
            success_url=f"{BASE_URL}/recap?pack={pack}&session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{BASE_URL}/inscription?pack={pack}&color={color}&avatar={avatar}",
            allow_promotion_codes=False,
            billing_address_collection="auto",
            automatic_tax={"enabled": False},
        )
        return redirect(session.url, code=303)
    return render_template("inscription.html", title="Inscription")

@app.route("/recap")
def recap_page():
    # Page 4 — récap + script d’intégration
    pack = request.args.get("pack", "avocat")
    return render_template("recap.html", pack=pack, title="Récapitulatif")

@app.route("/chat")
def chat_page():
    # Fenêtre carrée du bot (démo ou intégration)
    # Ex: /chat?bot_id=avocat-001
    return render_template("chat.html", title="Betty — Chat")

# =========================
# API
# =========================
@app.route("/api/bettybot", methods=["POST"])
def bettybot_reply():
    payload = request.get_json(force=True, silent=True) or {}
    user_input = payload.get("message", "").strip()
    bot_id = payload.get("bot_id", "avocat-001")

    bot = BOTS.get(bot_id, BOTS["avocat-001"])
    answer = query_llm(user_input, bot["pack"])
    return jsonify({"response": answer})

@app.route("/api/bot_meta")
def bot_meta():
    bot_id = request.args.get("bot_id", "avocat-001")
    bot = BOTS.get(bot_id)
    if not bot:
        return jsonify({"error": "bot inconnu"}), 404
    return jsonify({
        "name": bot["name"],
        "avatar_url": static_url(bot["avatar_file"]),
        "color_hex": bot["color"]
    })

# =========================
# Run local
# =========================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
