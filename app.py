# app.py
from flask import Flask, render_template, request, jsonify, redirect, url_for
import os, yaml, requests, re, stripe

app = Flask(__name__)

# =========================
# CONFIG (env)
# =========================
# Together
TOGETHER_API_KEY = os.getenv("TOGETHER_API_KEY", "").strip()
TOGETHER_API_URL = "https://api.together.xyz/v1/chat/completions"
# Modèle stable (tu peux surcharger via env LLM_MODEL)
LLM_MODEL        = os.getenv(
    "LLM_MODEL",
    "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo"
).strip()
# Un peu plus de marge que 90 pour rendre les réponses utiles
LLM_MAX_TOKENS   = int(os.getenv("LLM_MAX_TOKENS", "180"))

# Stripe
stripe.api_key   = os.getenv("STRIPE_SECRET_KEY", "").strip()
PRICE_ID         = os.getenv("STRIPE_PRICE_ID", "").strip()  # abonnement 29,99 €/mois

# Base URL (utile pour success/cancel Stripe)
BASE_URL         = (os.getenv("BASE_URL", "http://127.0.0.1:5000")).rstrip("/")

# =========================
# HELPERS
# =========================
def static_url(filename: str) -> str:
    # URL absolue pour les clients externes si besoin
    return url_for("static", filename=filename, _external=True)

def load_pack_prompt(pack_name: str) -> str:
    path = f"data/packs/{pack_name}.yaml"
    if not os.path.exists(path):
        return (
            "Tu es une assistante AI professionnelle. Réponds clairement et concrètement. "
            "Ta mission principale est de QUALIFIER la demande (nom, email, téléphone, motif) "
            "et de proposer un rendez-vous avec le professionnel si pertinent. "
            "Reste concise, polie, en français. Ne donne pas d'avis juridique/médical : oriente."
        )
    with open(path, "r") as f:
        data = yaml.safe_load(f) or {}
    return data.get("prompt", "Tu es une assistante AI professionnelle.")

def build_business_block(profile: dict) -> str:
    if not profile:
        return ""
    lines = ["\n---\nINFORMATIONS ETABLISSEMENT (utilise-les dans tes réponses) :"]
    if profile.get("name"):    lines.append(f"• Nom : {profile['name']}")
    if profile.get("phone"):   lines.append(f"• Téléphone : {profile['phone']}")
    if profile.get("email"):   lines.append(f"• Email : {profile['email']}")
    if profile.get("address"): lines.append(f"• Adresse : {profile['address']}")
    if profile.get("hours"):   lines.append(f"• Horaires : {profile['hours']}")
    lines.append("---\n")
    return "\n".join(lines)

def build_system_prompt(pack_name: str, profile: dict, greeting: str = "") -> str:
    base = load_pack_prompt(pack_name)
    biz  = build_business_block(profile)
    greet= f"\nMessage d'accueil recommandé : {greeting}\n" if greeting else ""
    return f"{base}{biz}{greet}"

def query_llm(user_input: str, pack_name: str, profile: dict = None, greeting: str = "") -> str:
    """
    Appel robuste à Together /chat/completions
    """
    # Clé manquante -> message propre (évite la bulle "erreur")
    if not TOGETHER_API_KEY:
        return "⚠️ Clé Together absente côté serveur. Ajoutez TOGETHER_API_KEY dans vos variables d’environnement."

    headers = {
        "Authorization": f"Bearer {TOGETHER_API_KEY}",
        "Content-Type": "application/json",
    }
    system_prompt = build_system_prompt(pack_name, profile or {}, greeting)
    payload = {
        "model": LLM_MODEL,
        "max_tokens": LLM_MAX_TOKENS,
        "temperature": 0.4,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_input}
        ]
    }
    try:
        r = requests.post(TOGETHER_API_URL, headers=headers, json=payload, timeout=30)
        # Si Together renvoie une erreur, remonter l’info lisible
        if not r.ok:
            try:
                err = r.json()
            except Exception:
                err = {"status": r.status_code, "text": r.text[:200]}
            return f"⚠️ Erreur Together: {err}"
        data = r.json()
        content = (
            data.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                .strip()
        )
        return content or "Désolé, je n’ai pas pu générer de réponse."
    except Exception as e:
        print("[LLM ERROR]", type(e).__name__, e)
        return f"⚠️ Exception serveur: {type(e).__name__}: {e}"

def parse_contact_info(text: str) -> dict:
    """Heuristiques simples pour extraire téléphone/email/adresse/horaires/nom depuis un champ libre."""
    if not text:
        return {}
    d = {}
    m = re.search(r'(\+?\d[\d\s\.\-]{6,})', text);                  d["phone"]   = m.group(1) if m else None
    m = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', text);                 d["email"]   = m.group(0) if m else None
    m = re.search(r'horaires?\s*:\s*(.+)', text, re.I);             d["hours"]   = m.group(1).strip() if m else None
    m = re.search(r'(rue|avenue|bd|boulevard|place).+', text, re.I); d["address"] = m.group(0).strip() if m else None
    m = re.search(r'(nom|cabinet|agence)\s*:\s*(.+)', text, re.I);  d["name"]    = m.group(2).strip() if m else None
    return {k: v for k, v in d.items() if v}

# =========================
# MINI-DB (démo)
# =========================
BOTS = {
    "avocat-001":  {"pack": "avocat",  "name": "Betty (Avocat)",     "color": "#4F46E5", "avatar_file": "avocat.jpg",  "profile": {}, "greeting": ""},
    "immo-002":    {"pack": "immo",    "name": "Betty (Immobilier)", "color": "#16A34A", "avatar_file": "immo.jpg",    "profile": {}, "greeting": ""},
    "medecin-003": {"pack": "medecin", "name": "Betty (Médecin)",    "color": "#0284C7", "avatar_file": "medecin.jpg", "profile": {}, "greeting": ""},
}

# =========================
# PAGES
# =========================
@app.route("/")
def index():
    return render_template("index.html", title="Découvrez Betty")

@app.route("/config", methods=["GET", "POST"])
def config_page():
    if request.method == "POST":
        pack      = request.form.get("pack", "avocat")
        color     = request.form.get("color", "#4F46E5")
        avatar    = request.form.get("avatar", "avocat.jpg")
        greeting  = request.form.get("greeting", "")
        contact   = request.form.get("contact_info", "")
        persona_x = request.form.get("persona_x", "0")
        persona_y = request.form.get("persona_y", "0")
        return redirect(url_for("inscription_page",
                                pack=pack, color=color, avatar=avatar,
                                greeting=greeting, contact=contact,
                                px=persona_x, py=persona_y))
    return render_template("config.html", title="Configurer votre bot")

@app.route("/inscription", methods=["GET", "POST"])
def inscription_page():
    if request.method == "POST":
        email   = request.form.get("email")
        pack    = request.args.get("pack", "avocat")
        color   = request.args.get("color", "#4F46E5")
        avatar  = request.args.get("avatar", "avocat.jpg")
        greet   = request.args.get("greeting", "") or ""
        contact = request.args.get("contact", "") or ""
        px      = request.args.get("px", "0")
        py      = request.args.get("py", "0")

        # Associe temporairement le profil au bot-type choisi (démo)
        profile = parse_contact_info(contact)
        bot_id = "avocat-001" if pack == "avocat" else ("medecin-003" if pack == "medecin" else "immo-002")
        BOTS[bot_id]["profile"]  = profile
        BOTS[bot_id]["greeting"] = greet
        BOTS[bot_id]["color"]    = color
        BOTS[bot_id]["avatar_file"] = avatar

        # Stripe : si clés/price manquent en dev, on simule un succès propre
        if not stripe.api_key or not PRICE_ID:
            return redirect(f"{BASE_URL}/recap?pack={pack}&session_id=fake_checkout_dev", code=303)

        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": PRICE_ID, "quantity": 1}],
            customer_email=email,
            success_url=f"{BASE_URL}/recap?pack={pack}&session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{BASE_URL}/inscription?pack={pack}&color={color}&avatar={avatar}",
            metadata={
                "pack": pack, "color": color, "avatar": avatar,
                "greeting": greet, "contact_info": contact,
                "persona_x": px, "persona_y": py
            }
        )
        return redirect(session.url, code=303)
    return render_template("inscription.html", title="Inscription")

@app.route("/recap")
def recap_page():
    pack = request.args.get("pack", "avocat")
    return render_template("recap.html", pack=pack, title="Récapitulatif")

@app.route("/chat")
def chat_page():
    return render_template("chat.html", title="Betty — Chat")

# =========================
# API
# =========================
@app.route("/api/bettybot", methods=["POST"])
def bettybot_reply():
    payload    = request.get_json(force=True, silent=True) or {}
    user_input = (payload.get("message") or "").strip()
    bot_id     = payload.get("bot_id", "avocat-001")

    if not user_input:
        # message neutre si on envoie à vide
        return jsonify({"response": "Dites-moi ce dont vous avez besoin 🙂"}), 200

    bot = BOTS.get(bot_id, BOTS["avocat-001"])
    answer = query_llm(
        user_input,
        bot["pack"],
        profile=bot.get("profile", {}),
        greeting=bot.get("greeting", "")
    )
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
        "color_hex": bot["color"],
        "profile": bot.get("profile", {}),
        "greeting": bot.get("greeting", "")
    })

# Petit endpoint santé (utile pour Vercel)
@app.route("/healthz")
def healthz():
    return "ok", 200

# =========================
# RUN (local)
# =========================
if __name__ == "__main__":
    # En local uniquement
    app.run(host="0.0.0.0", port=5000, debug=True)
