# app.py
from flask import Flask, render_template, request, jsonify, redirect, url_for, session
import os, yaml, requests, re, stripe, json

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")

# =========================
# CONFIG (env)
# =========================
# Together
TOGETHER_API_KEY = os.getenv("TOGETHER_API_KEY", "").strip()
TOGETHER_API_URL = "https://api.together.xyz/v1/chat/completions"
LLM_MODEL = os.getenv("LLM_MODEL", "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo").strip()
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "180"))

# Stripe
stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "").strip()
PRICE_ID = os.getenv("STRIPE_PRICE_ID", "").strip()  # abonnement 29,99 €/mois

# Base URL (utile pour success/cancel Stripe)
BASE_URL = (os.getenv("BASE_URL", "http://127.0.0.1:5000")).rstrip("/")

# Mailjet (envoi réel)
MJ_API_KEY     = os.getenv("MJ_API_KEY", "").strip()
MJ_API_SECRET  = os.getenv("MJ_API_SECRET", "").strip()
MJ_FROM_EMAIL  = os.getenv("MJ_FROM_EMAIL", "no-reply@spectramedia.ai").strip()
MJ_FROM_NAME   = os.getenv("MJ_FROM_NAME", "Spectra Media AI").strip()

# =========================
# HELPERS
# =========================
def static_url(filename: str) -> str:
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
    guide = f"""
Tu es **Betty**, assistante {pack_name}. Objectif prioritaire : **QUALIFIER** le prospect puis **proposer un rendez-vous**.

RÈGLES DE CONVERSATION (OBLIGATOIRES) :
- Pose **UNE seule question** à la fois. 2 phrases max par message.
- **Pas de répétitions** : n'explique pas à nouveau ce qui vient d'être dit.
- Oriente la qualification dès les 1ers échanges.
- Champs à collecter (ordre conseillé) : **motif**, **nom**, **email**, **téléphone**, **disponibilités**.
- Dès que tu as au moins **motif + nom + (email ou téléphone)**, propose un RDV (créneau ou demande la dispo).
- Tu ne donnes pas d'avis juridique/médical ; tu orientes vers le pro.
- Termine souvent par une question **courte** qui fait avancer.

### SORTIE LEAD JSON
À CHAQUE message, ajoute en **dernière ligne** (sans texte avant/après, sans markdown) :
<LEAD_JSON>{{"reason":"<motif ou ''>","name":"<nom ou ''>","email":"<email ou ''>","phone":"<téléphone ou ''>","availability":"<dispo ou ''>","stage":"<collecting|ready>"}}</LEAD_JSON>
"""
    base = load_pack_prompt(pack_name)
    biz  = build_business_block(profile)
    greet= f"\nMessage d'accueil recommandé : {greeting}\n" if greeting else ""
    return f"{base}\n{biz}\n{guide}\n{greet}"

def query_llm(user_input: str, pack_name: str, profile: dict = None, greeting: str = "") -> str:
    if not TOGETHER_API_KEY:
        return "⚠️ Clé Together absente côté serveur. Ajoutez TOGETHER_API_KEY."
    headers = {"Authorization": f"Bearer {TOGETHER_API_KEY}", "Content-Type": "application/json"}
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
        if not r.ok:
            try: err = r.json()
            except Exception: err = {"status": r.status_code, "text": r.text[:200]}
            return f"⚠️ Erreur Together: {err}"
        data = r.json()
        return (data.get("choices", [{}])[0].get("message", {}).get("content", "") or "").strip() \
               or "Désolé, je n’ai pas pu générer de réponse."
    except Exception as e:
        print("[LLM ERROR]", type(e).__name__, e)
        return f"⚠️ Exception serveur: {type(e).__name__}: {e}"

def call_llm_with_history(system_prompt: str, history: list, user_input: str) -> str:
    if not TOGETHER_API_KEY:
        return "⚠️ Clé Together absente côté serveur. Ajoutez TOGETHER_API_KEY."
    headers = {"Authorization": f"Bearer {TOGETHER_API_KEY}", "Content-Type": "application/json"}
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history or [])
    messages.append({"role": "user", "content": user_input})
    payload = {"model": LLM_MODEL, "max_tokens": LLM_MAX_TOKENS, "temperature": 0.4, "messages": messages}
    try:
        r = requests.post(TOGETHER_API_URL, headers=headers, json=payload, timeout=30)
        if not r.ok:
            try: err = r.json()
            except Exception: err = {"status": r.status_code, "text": r.text[:200]}
            return f"⚠️ Erreur Together: {err}"
        data = r.json()
        return (data.get("choices", [{}])[0].get("message", {}).get("content", "") or "").strip() \
               or "Désolé, je n’ai pas pu générer de réponse."
    except Exception as e:
        print("[LLM ERROR]", type(e).__name__, e)
        return f"⚠️ Exception serveur: {type(e).__name__}: {e}"

def parse_contact_info(text: str) -> dict:
    if not text: return {}
    d = {}
    m = re.search(r'(\+?\d[\d\s\.\-]{6,})', text);                   d["phone"]   = m.group(1) if m else None
    m = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', text);                  d["email"]   = m.group(0) if m else None
    m = re.search(r'horaires?\s*:\s*(.+)', text, re.I);              d["hours"]   = m.group(1).strip() if m else None
    m = re.search(r'(rue|avenue|bd|boulevard|place).+', text, re.I); d["address"] = m.group(0).strip() if m else None
    m = re.search(r'(nom|cabinet|agence)\s*:\s*(.+)', text, re.I);   d["name"]    = m.group(2).strip() if m else None
    return {k: v for k, v in d.items() if v}

LEAD_TAG_RE = re.compile(r"<LEAD_JSON>(\{.*?\})</LEAD_JSON>$")

def extract_lead_json(text: str):
    if not text: return text, None
    m = LEAD_TAG_RE.search(text)
    if not m: return text, None
    lead_raw = m.group(1)
    message = text[:m.start()].rstrip()
    try:
        lead = json.loads(lead_raw)
    except Exception:
        lead = None
    return message, lead

def send_lead_email(to_email: str, lead: dict, bot_name: str = "Betty Bot"):
    """
    Envoi réel via Mailjet (v3.1). Nécessite MJ_API_KEY, MJ_API_SECRET, MJ_FROM_EMAIL, MJ_FROM_NAME.
    """
    if not (MJ_API_KEY and MJ_API_SECRET and MJ_FROM_EMAIL and to_email):
        print("[LEAD][MAILJET] Config manquante, email non envoyé.")
        return
    subject = f"Nouveau lead qualifié via {bot_name}"
    text = (
        f"Motif         : {lead.get('reason','')}\n"
        f"Nom           : {lead.get('name','')}\n"
        f"Email         : {lead.get('email','')}\n"
        f"Téléphone     : {lead.get('phone','')}\n"
        f"Disponibilités: {lead.get('availability','')}\n"
        f"Statut        : {lead.get('stage','')}\n"
    )
    payload = {
        "Messages": [{
            "From": {"Email": MJ_FROM_EMAIL, "Name": MJ_FROM_NAME},
            "To":   [{"Email": to_email}],
            "Subject": subject,
            "TextPart": text
        }]
    }
    try:
        r = requests.post(
            "https://api.mailjet.com/v3.1/send",
            auth=(MJ_API_KEY, MJ_API_SECRET),
            json=payload,
            timeout=15
        )
        print("[LEAD][MAILJET]", "OK" if r.ok else f"KO {r.status_code} {r.text[:160]}")
    except Exception as e:
        print("[LEAD][MAILJET][EXC]", type(e).__name__, e)

# =========================
# MINI-DB (démo)
# =========================
BOTS = {
    "avocat-001":  {"pack": "avocat",  "name": "Betty (Avocat)",     "color": "#4F46E5", "avatar_file": "avocat.jpg",  "profile": {}, "greeting": "", "buyer_email": None},
    "immo-002":    {"pack": "immo",    "name": "Betty (Immobilier)", "color": "#16A34A", "avatar_file": "immo.jpg",    "profile": {}, "greeting": "", "buyer_email": None},
    "medecin-003": {"pack": "medecin", "name": "Betty (Médecin)",    "color": "#0284C7", "avatar_file": "medecin.jpg", "profile": {}, "greeting": "", "buyer_email": None},
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

        profile = parse_contact_info(contact)
        bot_id = "avocat-001" if pack == "avocat" else ("medecin-003" if pack == "medecin" else "immo-002")
        BOTS[bot_id]["profile"]     = profile
        BOTS[bot_id]["greeting"]    = greet
        BOTS[bot_id]["color"]       = color
        BOTS[bot_id]["avatar_file"] = avatar
        BOTS[bot_id]["buyer_email"] = email  # en prod, fiabiliser via webhook

        if not stripe.api_key or not PRICE_ID:
            return redirect(f"{BASE_URL}/recap?pack={pack}&session_id=fake_checkout_dev", code=303)

        session_obj = stripe.checkout.Session.create(
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
        return redirect(session_obj.url, code=303)
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
        return jsonify({"response": "Dites-moi ce dont vous avez besoin 🙂"}), 200

    bot = BOTS.get(bot_id, BOTS["avocat-001"])

    # mémoire courte pour limiter les redites
    key = f"conv_{bot_id}"
    history = session.get(key, [])[-6:]  # 3 tours

    system_prompt = build_system_prompt(bot["pack"], bot.get("profile", {}), bot.get("greeting", ""))
    full_text = call_llm_with_history(system_prompt=system_prompt, history=history, user_input=user_input)

    response_text, lead = extract_lead_json(full_text)

    history.append({"role": "user", "content": user_input})
    history.append({"role": "assistant", "content": response_text})
    session[key] = history

    # si lead prêt et email acheteur connu -> envoi Mailjet
    if lead and isinstance(lead, dict) and lead.get("stage") == "ready":
        buyer_email = bot.get("buyer_email")
        if buyer_email:
            send_lead_email(buyer_email, lead, bot_name=bot["name"])

    return jsonify({"response": response_text})

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

@app.route("/api/reset", methods=["POST"])
def reset_conv():
    bot_id = (request.get_json(silent=True) or {}).get("bot_id", "avocat-001")
    session.pop(f"conv_{bot_id}", None)
    return jsonify({"ok": True})

@app.route("/healthz")
def healthz():
    return "ok", 200

# =========================
# RUN (local)
# =========================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
