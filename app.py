# app.py
from flask import Flask, render_template, request, jsonify, redirect, url_for
import os, yaml, requests, re, stripe

app = Flask(__name__)

# =========================
# CONFIG
# =========================
TOGETHER_API_KEY = os.getenv("TOGETHER_API_KEY") or "TA_CLE_TOGETHER_ICI"
TOGETHER_API_URL = "https://api.together.xyz/v1/chat/completions"
LLM_MODEL = os.getenv("LLM_MODEL") or "meta-llama/Meta-Llama-3-8B-Instruct"
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS") or 90)

stripe.api_key = os.getenv("STRIPE_SECRET_KEY") or "sk_test_xxx"
PRICE_ID = os.getenv("STRIPE_PRICE_ID") or "price_xxx"
BASE_URL = (os.getenv("BASE_URL") or "http://127.0.0.1:5000").rstrip("/")

# =========================
# FONCTIONS UTILES
# =========================
def static_url(filename: str) -> str:
    return url_for("static", filename=filename, _external=True)

def load_pack_prompt(pack_name: str) -> str:
    path = f"data/packs/{pack_name}.yaml"
    if not os.path.exists(path):
        return "Tu es une assistante AI professionnelle. Réponds avec clarté et concision."
    with open(path, "r") as f:
        data = yaml.safe_load(f) or {}
    return data.get("prompt", "Tu es une assistante AI professionnelle.")

def build_business_block(profile: dict) -> str:
    if not profile:
        return ""
    lignes = ["\n---\nINFORMATIONS ETABLISSEMENT (à utiliser dans tes réponses) :"]
    if profile.get("name"): lignes.append(f"• Nom : {profile['name']}")
    if profile.get("phone"): lignes.append(f"• Téléphone : {profile['phone']}")
    if profile.get("email"): lignes.append(f"• Email : {profile['email']}")
    if profile.get("address"): lignes.append(f"• Adresse : {profile['address']}")
    if profile.get("hours"): lignes.append(f"• Horaires : {profile['hours']}")
    lignes.append("---\n")
    return "\n".join(lignes)

def build_system_prompt(pack_name: str, profile: dict, greeting: str = "") -> str:
    base = load_pack_prompt(pack_name)
    biz  = build_business_block(profile)
    greet = f"\nMessage d'accueil recommandé : {greeting}\n" if greeting else ""
    return f"{base}{biz}{greet}"

def query_llm(user_input: str, pack_name: str, profile: dict = None, greeting: str = "") -> str:
    headers = {"Authorization": f"Bearer {TOGETHER_API_KEY}", "Content-Type": "application/json"}
    system_prompt = build_system_prompt(pack_name, profile or {}, greeting)
    payload = {
        "model": LLM_MODEL,
        "max_tokens": LLM_MAX_TOKENS,
        "messages": [
            {"role": "system", "content": system_prompt},
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

def parse_contact_info(text: str) -> dict:
    if not text: return {}
    d = {}
    m = re.search(r'(\+?\d[\d\s\.]{6,})', text);           d["phone"]   = m.group(1) if m else None
    m = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', text);        d["email"]   = m.group(0) if m else None
    m = re.search(r'horaires?:\s*(.+)', text, re.I);       d["hours"]   = m.group(1).strip() if m else None
    m = re.search(r'(rue|avenue|bd|boulevard|place).+', text, re.I); d["address"] = m.group(0).strip() if m else None
    m = re.search(r'(nom|cabinet|agence)\s*:\s*(.+)', text, re.I);   d["name"] = m.group(2).strip() if m else None
    return {k:v for k,v in d.items() if v}

# =========================
# MINI-DB DES BOTS
# =========================
BOTS = {
    "avocat-001":  {"pack": "avocat",  "name": "Betty (Avocat)",    "color": "#4F46E5", "avatar_file": "avocat.jpg",  "profile": {}, "greeting": ""},
    "immo-002":    {"pack": "immo",    "name": "Betty (Immobilier)","color": "#16A34A", "avatar_file": "immo.jpg",    "profile": {}, "greeting": ""},
    "medecin-003": {"pack": "medecin", "name": "Betty (Médecin)",   "color": "#0284C7", "avatar_file": "medecin.jpg", "profile": {}, "greeting": ""},
}

# =========================
# ROUTES PAGES
# =========================
@app.route("/")
def index():
    return render_template("index.html", title="Découvrez Betty")

@app.route("/config", methods=["GET", "POST"])
def config_page():
    if request.method == "POST":
        pack       = request.form.get("pack", "avocat")
        color      = request.form.get("color", "#4F46E5")
        avatar     = request.form.get("avatar", "avocat.png")
        greeting   = request.form.get("greeting", "")
        contact    = request.form.get("contact_info", "")
        persona_x  = request.form.get("persona_x", "0")
        persona_y  = request.form.get("persona_y", "0")
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
        avatar  = request.args.get("avatar", "avocat.png")
        greet   = request.args.get("greeting", "") or ""
        contact = request.args.get("contact", "") or ""
        px      = request.args.get("px", "0")
        py      = request.args.get("py", "0")

        profile = parse_contact_info(contact)
        bot_id = "avocat-001" if pack=="avocat" else ("medecin-003" if pack=="medecin" else "immo-002")
        BOTS[bot_id]["profile"] = profile
        BOTS[bot_id]["greeting"] = greet

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
    payload = request.get_json(force=True, silent=True) or {}
    user_input = payload.get("message", "").strip()
    bot_id = payload.get("bot_id", "avocat-001")

    bot = BOTS.get(bot_id, BOTS["avocat-001"])
    answer = query_llm(user_input, bot["pack"], profile=bot.get("profile", {}), greeting=bot.get("greeting",""))
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

# =========================
# RUN LOCAL
# =========================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
