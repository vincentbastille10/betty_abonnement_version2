# app.py
from flask import Flask, render_template, request, jsonify, redirect, url_for, session
import os, yaml, requests, re, stripe, json, uuid, hashlib

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")

# --- Cookies compat iframe (Wix / domaines tiers) ---
# En prod HTTPS : laisser True. En dev local http://, passe SECURE=False.
SESSION_SECURE = os.getenv("SESSION_SECURE", "true").lower() == "true"
app.config.update(
    SESSION_COOKIE_SAMESITE='None',
    SESSION_COOKIE_SECURE=SESSION_SECURE
)

# =========================
# CONFIG (env)
# =========================
TOGETHER_API_KEY = os.getenv("TOGETHER_API_KEY", "").strip()
TOGETHER_API_URL = "https://api.together.xyz/v1/chat/completions"
LLM_MODEL = os.getenv("LLM_MODEL", "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo").strip()
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "180"))

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "").strip()
PRICE_ID = os.getenv("STRIPE_PRICE_ID", "").strip()  # 29,99 €/mois

BASE_URL = (os.getenv("BASE_URL", "http://127.0.0.1:5000")).rstrip("/")

MJ_API_KEY    = os.getenv("MJ_API_KEY", "").strip()
MJ_API_SECRET = os.getenv("MJ_API_SECRET", "").strip()
MJ_FROM_EMAIL = os.getenv("MJ_FROM_EMAIL", "no-reply@spectramedia.ai").strip()
MJ_FROM_NAME  = os.getenv("MJ_FROM_NAME", "Spectra Media AI").strip()

app.jinja_env.globals['BASE_URL'] = BASE_URL

# =========================
# HELPERS
# =========================
def static_url(filename: str) -> str:
    return url_for("static", filename=filename, _external=True)

def load_pack_prompt(pack_name: str) -> str:
    path = f"data/packs/{pack_name}.yaml"
    if not os.path.exists(path):
        return (
            "Tu es une assistante AI professionnelle. "
            "Ta mission principale est de QUALIFIER la demande (motif, nom, email, téléphone, disponibilités) "
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
    guide = f"""
Tu es **Betty**, assistante {pack_name}. Objectif prioritaire : **QUALIFIER** le prospect puis **proposer un rendez-vous**.

RÈGLES DE CONVERSATION (OBLIGATOIRES) :
- Pose **UNE seule question** à la fois. 2 phrases max par message.
- Oriente la qualification dès les 1ers échanges.
- Champs à collecter (ordre conseillé) : **motif**, **téléphone** OU **email**, **nom complet**, **disponibilités**.
- Dès que tu as **motif + nom + (email ou téléphone)**, annonce : "Parfait, je transmets au cabinet pour vous proposer un créneau." et passe le stage à "ready".
- Tu ne donnes pas d'avis juridique/médical ; tu orientes vers le pro.
- **Ne te réinitialise jamais** en cours d’échange.

RÈGLES SUPPLÉMENTAIRES (QUALIF LEAD) :
- Ne JAMAIS afficher de variables ou placeholders (ex. {{Téléphone}}, {{Email}}). Pose des questions concrètes :
  1) "Quel est votre numéro de téléphone ?" (ou "Quelle est votre adresse e-mail ?"),
  2) "Quel est votre nom complet ?",
  3) Demander des disponibilités si utile.
- N'affiche pas le JSON ci-dessous. Réponds normalement, puis ajoute juste la balise technique en dernière ligne.

### SORTIE LEAD JSON
À CHAQUE message, ajoute en **dernière ligne** (sans texte avant/après, sans markdown) un tag :
<LEAD_JSON>{{"reason": "<motif ou ''>", "name": "<nom ou ''>", "email": "<email ou ''>", "phone": "<téléphone ou ''>", "availability": "<dispo ou ''>", "stage": "<collecting|ready>"}}</LEAD_JSON>

- `stage = "ready"` **uniquement** si tu as **motif + nom + (email ou téléphone)**.
- Sinon `stage = "collecting"`.
- Le JSON doit être **une seule ligne** valide. Pas de retour à la ligne, pas de ``` ni autre balise.
"""
    greet = f"\nMessage d'accueil recommandé : {greeting}\n" if greeting else ""
    return f"{base}\n{biz}\n{guide}\n{greet}"

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
    if not text:
        return {}
    d = {}
    m = re.search(r'(\+?\d[\d\s\.\-]{6,})', text);                   d["phone"]   = m.group(1) if m else None
    m = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', text);                  d["email"]   = m.group(0) if m else None
    m = re.search(r'horaires?\s*:\s*(.+)', text, re.I);              d["hours"]   = m.group(1).strip() if m else None
    m = re.search(r'(rue|avenue|bd|boulevard|place).+', text, re.I); d["address"] = m.group(0).strip() if m else None
    m = re.search(r'(nom|cabinet|agence)\s*:\s*(.+)', text, re.I);   d["name"]    = m.group(2).strip() if m else None
    return {k: v for k, v in d.items() if v}

LEAD_TAG_RE = re.compile(r"<LEAD_JSON>(\{.*?\})</LEAD_JSON>$")

def extract_lead_json(text: str):
    if not text:
        return text, None
    m = LEAD_TAG_RE.search(text)
    if not m:
        return text, None
    lead_raw = m.group(1)
    message = text[:m.start()].rstrip()
    try:
        lead = json.loads(lead_raw)
    except Exception:
        lead = None
    return message, lead

def send_lead_email(to_email: str, lead: dict, bot_name: str = "Betty Bot"):
    if not (MJ_API_KEY and MJ_API_SECRET and to_email):
        print("[LEAD][MAILJET] Config manquante ou email vide, email non envoyé.")
        return
    subject = f"Nouveau lead qualifié via {bot_name}"
    text = (
        f"Motif        : {lead.get('reason','')}\n"
        f"Nom          : {lead.get('name','')}\n"
        f"Email        : {lead.get('email','')}\n"
        f"Téléphone    : {lead.get('phone','')}\n"
        f"Disponibilités : {lead.get('availability','')}\n"
        f"Statut       : {lead.get('stage','')}\n"
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
        print("[LEAD][MAILJET]", "OK" if r.ok else f"KO {r.status_code} {r.text[:120]}")
    except Exception as e:
        print("[LEAD][MAILJET][EXC]", type(e).__name__, e)

# =========================
# MINI-DB (démo)
# =========================
BOTS = {
    "avocat-001":  {"pack":"avocat","name":"Betty Bot (Avocat)","color":"#4F46E5","avatar_file":"avocat.jpg","profile":{},"greeting":"","buyer_email":None,"owner_name":None,"public_id":None},
    "immo-002":    {"pack":"immo","name":"Betty Bot (Immobilier)","color":"#16A34A","avatar_file":"immo.jpg","profile":{},"greeting":"","buyer_email":None,"owner_name":None,"public_id":None},
    "medecin-003": {"pack":"medecin","name":"Betty Bot (Médecin)","color":"#0284C7","avatar_file":"medecin.jpg","profile":{},"greeting":"","buyer_email":None,"owner_name":None,"public_id":None},
}

def _gen_public_id(email: str, bot_key: str) -> str:
    h = hashlib.sha1((email + "|" + bot_key).encode()).hexdigest()[:8]
    return f"{bot_key}-{h}"

def find_bot_by_public_id(public_id: str):
    if not public_id:
        return None, None
    # format attendu: "<bot_key>-<hash8>"
    parts = public_id.split("-")
    if len(parts) < 3:
        # fallback: tente de matcher directement
        for k, b in BOTS.items():
            if b.get("public_id") == public_id:
                return k, b
        return None, None
    bot_key = "-".join(parts[:2])
    bot = BOTS.get(bot_key)
    return bot_key, bot

# Mémoire de conversations côté serveur (fallback si cookies bloqués + conv_id côté client)
CONVS = {}  # key: conv_id -> list[{"role": "...", "content": "..."}]

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
        bot = BOTS[bot_id]
        bot["profile"]     = profile
        bot["greeting"]    = greet
        bot["color"]       = color
        bot["avatar_file"] = avatar
        bot["buyer_email"] = email
        local_part = (email or "").split("@")[0] or "Client"
        bot["owner_name"] = local_part.title()
        bot["public_id"]  = _gen_public_id(email or str(uuid.uuid4()), bot_id)

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
    bot_key = "avocat-001" if pack=="avocat" else ("medecin-003" if pack=="medecin" else "immo-002")
    bot = BOTS.get(bot_key, {})

    public_id = bot.get("public_id")
    owner     = bot.get("owner_name") or ""
    display   = bot.get("name") or "Betty Bot"

    # overrides (dev)
    public_id = request.args.get("public_id", public_id)
    owner     = request.args.get("owner", owner)

    return render_template(
        "recap.html",
        base_url=BASE_URL,
        pack=pack,
        public_id=public_id or "",
        full_name=(f"{display} — {owner}" if owner else display),
        title="Récapitulatif"
    )

@app.route("/chat")
def chat_page():
    # Iframe embarqué : /chat?public_id=...&embed=1
    public_id = (request.args.get("public_id") or "").strip()
    embed     = request.args.get("embed", "0") == "1"
    _, bot = find_bot_by_public_id(public_id)
    if not bot:
        # fallback: première dispo
        bot = BOTS["avocat-001"]
        public_id = bot.get("public_id") or "avocat-001-demo"
    display_name = bot.get("name") or "Betty Bot"
    owner = bot.get("owner_name") or "Client"
    full_name = f"{display_name} — {owner}" if owner else display_name
    return render_template(
        "chat.html",
        title="Betty — Chat",
        base_url=BASE_URL,
        public_id=public_id,
        full_name=full_name,
        color=bot.get("color") or "#4F46E5",
        avatar_url=static_url(bot.get("avatar_file") or "avocat.jpg"),
        greeting=bot.get("greeting") or "Bonjour, je suis Betty. Comment puis-je vous aider ?",
        embed=embed
    )

# =========================
# API
# =========================
@app.route("/api/bettybot", methods=["POST"])
def bettybot_reply():
    payload    = request.get_json(force=True, silent=True) or {}
    user_input = (payload.get("message") or "").strip()
    public_id  = (payload.get("bot_id") or payload.get("public_id") or "").strip()
    conv_id    = (payload.get("conv_id") or "").strip()

    if not user_input:
        return jsonify({"response": "Dites-moi ce dont vous avez besoin 🙂"}), 200

    bot_key, bot = find_bot_by_public_id(public_id)
    if not bot:
        # fallback par défaut
        bot_key = "avocat-001"
        bot = BOTS[bot_key]

    # Historique : conv_id (localStorage) > cookies Flask
    if conv_id:
        history = CONVS.get(conv_id, [])
    else:
        key = f"conv_{public_id or bot_key}"
        history = session.get(key, [])
    history = history[-6:]

    system_prompt = build_system_prompt(bot["pack"], bot.get("profile", {}), bot.get("greeting", ""))
    full_text = call_llm_with_history(system_prompt=system_prompt, history=history, user_input=user_input)
    response_text, lead = extract_lead_json(full_text)

    # maj historique
    history.append({"role": "user", "content": user_input})
    history.append({"role": "assistant", "content": response_text})
    if conv_id:
        CONVS[conv_id] = history
    else:
        session[f"conv_{public_id or bot_key}"] = history

    # Envoi lead quand ready
    if lead and isinstance(lead, dict) and lead.get("stage") == "ready":
        buyer_email = bot.get("buyer_email")
        if buyer_email:
            send_lead_email(buyer_email, lead, bot_name=bot["name"])

    return jsonify({"response": response_text})

@app.route("/api/embed_meta")
def embed_meta():
    public_id = (request.args.get("public_id") or "").strip()
    if not public_id:
        return jsonify({"error":"missing public_id"}), 400
    _, bot = find_bot_by_public_id(public_id)
    if not bot:
        return jsonify({"error":"bot_not_found"}), 404
    return jsonify({
        "bot_id": public_id,
        "owner_name": bot.get("owner_name") or "Client",
        "display_name": bot.get("name") or "Betty Bot",
        "color_hex": bot.get("color") or "#4F46E5",
        "avatar_url": static_url(bot.get("avatar_file") or "avocat.jpg"),
        "greeting": bot.get("greeting") or "Bonjour, je suis Betty. Comment puis-je vous aider ?"
    })

@app.route("/healthz")
def healthz():
    return "ok", 200

@app.route("/api/reset", methods=["POST"])
def reset_conv():
    key = (request.get_json(silent=True) or {}).get("key")
    if key and key in CONVS:
        CONVS.pop(key, None)
    return jsonify({"ok": True})

if __name__ == "__main__":
    # Dev local : mettre SESSION_SECURE=False pour autoriser cookies non-HTTPS
    app.run(host="0.0.0.0", port=5000, debug=True)
