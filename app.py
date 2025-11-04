# app.py
from __future__ import annotations
from flask import Flask, render_template, request, jsonify, redirect, url_for, session
import os, yaml, requests, re, stripe, json, uuid, hashlib, sqlite3, time
from pathlib import Path
from contextlib import contextmanager

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")

# --- Cookies compat iframe (Wix / domaines tiers) ---
SESSION_SECURE = os.getenv("SESSION_SECURE", "true").lower() == "true"
app.config.update(
    SESSION_COOKIE_SAMESITE="None",
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
PRICE_ID = os.getenv("STRIPE_PRICE_ID", "").strip()  # price_... (29,99 €/mois)

BASE_URL = (os.getenv("BASE_URL", "http://127.0.0.1:5000")).rstrip("/")

MJ_API_KEY    = os.getenv("MJ_API_KEY", "").strip()
MJ_API_SECRET = os.getenv("MJ_API_SECRET", "").strip()
MJ_FROM_EMAIL = os.getenv("MJ_FROM_EMAIL", "no-reply@spectramedia.online").strip()
MJ_FROM_NAME  = os.getenv("MJ_FROM_NAME", "Spectra Media AI").strip()

app.jinja_env.globals["BASE_URL"] = BASE_URL

# =========================
# DB SQLite (persistant) — /tmp en serverless, data/app.db en local
# =========================
def pick_db_path() -> Path:
    env_forced = os.getenv("DB_PATH")
    if env_forced:
        p = Path(env_forced)
    else:
        is_serverless = bool(os.getenv("VERCEL") or os.getenv("AWS_LAMBDA_FUNCTION_VERSION"))
        p = Path("/tmp/bots.db") if is_serverless else Path("data/app.db")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return p

DB_PATH = pick_db_path()

@contextmanager
def db_connect():
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    try:
        yield con
    finally:
        con.close()

def db_init():
    with db_connect() as con:
        con.execute("""
        CREATE TABLE IF NOT EXISTS bots (
            public_id    TEXT PRIMARY KEY,
            bot_key      TEXT NOT NULL,
            pack         TEXT NOT NULL,
            name         TEXT,
            color        TEXT,
            avatar_file  TEXT,
            greeting     TEXT,
            buyer_email  TEXT,
            owner_name   TEXT,
            profile_json TEXT
        )
        """)
        con.commit()

def db_upsert_bot(bot: dict):
    with db_connect() as con:
        con.execute("""
        INSERT INTO bots(public_id, bot_key, pack, name, color, avatar_file, greeting, buyer_email, owner_name, profile_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(public_id) DO UPDATE SET
          pack=excluded.pack,
          name=excluded.name,
          color=excluded.color,
          avatar_file=excluded.avatar_file,
          greeting=excluded.greeting,
          buyer_email=excluded.buyer_email,
          owner_name=excluded.owner_name,
          profile_json=excluded.profile_json
        """, (
            bot.get("public_id"),
            bot.get("bot_key"),
            bot.get("pack"),
            bot.get("name"),
            bot.get("color"),
            bot.get("avatar_file"),
            bot.get("greeting"),
            bot.get("buyer_email"),
            bot.get("owner_name"),
            json.dumps(bot.get("profile") or {}, ensure_ascii=False)
        ))
        con.commit()

def db_get_bot(public_id: str):
    if not public_id:
        return None
    with db_connect() as con:
        row = con.execute("SELECT * FROM bots WHERE public_id = ? LIMIT 1", (public_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["profile"] = {}
    if d.get("profile_json"):
        try:
            d["profile"] = json.loads(d["profile_json"])
        except Exception:
            d["profile"] = {}
    return d

# init DB au cold start
db_init()

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

    # Règles spécifiques : pour le pack "medecin", l'EMAIL PATIENT est OBLIGATOIRE avant stage=ready
    if (pack_name or "").lower() == "medecin":
        qualif_order = "**motif**, **email (OBLIGATOIRE)**, **téléphone**, **nom complet**, **disponibilités**"
        ready_rule   = '— `stage = "ready"` uniquement si **motif + nom + email**.'
    else:
        qualif_order = "**motif**, **téléphone** OU **email**, **nom complet**, **disponibilités**"
        ready_rule   = '— `stage = "ready"` uniquement si **motif + nom + (email ou téléphone)**.'

    guide = f"""
Tu es **Betty**, assistante {pack_name}. Objectif prioritaire : **QUALIFIER** le prospect puis **proposer un rendez-vous**.

RÈGLES DE CONVERSATION (OBLIGATOIRES) :
- Pose **UNE seule question** à la fois. 2 phrases max par message.
- Oriente la qualification dès les 1ers échanges.
- Champs à collecter (ordre conseillé) : {qualif_order}
- Dès que les conditions sont réunies, annonce : "Parfait, je transmets au cabinet pour vous proposer un créneau." et passe le stage à "ready".
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

{ready_rule}
- Le JSON doit être **une seule ligne** valide. Pas de retour à la ligne, pas de ``` ni autre balise.
"""
    greet = f"\nMessage d'accueil recommandé : {greeting}\n" if greeting else ""
    return f"{base}\n{biz}\n{guide}\n{greet}"

# ======= LLM avec retry exponentiel =======
def call_llm_with_history(system_prompt: str, history: list, user_input: str) -> str:
    """
    Appel Together avec retry exponentiel.
    Retourne une string de réponse SANS lever d'exception.
    En cas d'indisponibilité persistante, renvoie "" (pour déclencher le fallback).
    """
    if not TOGETHER_API_KEY:
        return ""  # force le fallback si pas de clé

    headers = {"Authorization": f"Bearer {TOGETHER_API_KEY}", "Content-Type": "application/json"}
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history or [])
    messages.append({"role": "user", "content": user_input})
    payload = {"model": LLM_MODEL, "max_tokens": LLM_MAX_TOKENS, "temperature": 0.4, "messages": messages}

    backoffs = [0.6, 1.2, 2.4, 4.8]  # 4 tentatives
    last_err_text = None

    for wait in backoffs:
        try:
            r = requests.post(TOGETHER_API_URL, headers=headers, json=payload, timeout=30)
            if r.ok:
                data = r.json()
                content = (data.get("choices", [{}])[0].get("message", {}).get("content", "")).strip()
                if content:
                    return content
                last_err_text = "Réponse vide du modèle."
            else:
                try:
                    err = r.json()
                    last_err_text = f"{err.get('error',{}).get('message') or err}"
                except Exception:
                    last_err_text = f"HTTP {r.status_code}: {r.text[:200]}"
        except Exception as e:
            last_err_text = f"{type(e).__name__}: {e}"
        time.sleep(wait)

    print("[LLM][Together][FAIL]", last_err_text or "unknown")
    return ""  # vide => signal au fallback

# ======= Parsing & fallback =======
LEAD_TAG_RE = re.compile(r"<LEAD_JSON>(\{.*?\})</LEAD_JSON>\s*$", re.DOTALL)

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

def _lead_from_history(history: list) -> dict:
    text = " ".join([m["content"] for m in history if m.get("role") == "user"])
    d = {"reason": None, "email": None, "phone": None, "name": None, "availability": None}
    if not text:
        return d
    m = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', text);                  d["email"] = m.group(0) if m else None
    m = re.search(r'(\+?\d[\d\s\.\-]{6,})', text);                   d["phone"] = m.group(1) if m else None
    m = re.search(r'(?:je|j’|j\')?\s*(?:souhaite|veux|voudrais|besoin|motif|pour)\s*:?(.{5,120})', text, re.I)
    d["reason"] = (m.group(1).strip() if m else None)
    m = re.search(r'(?:je m’appelle|je m\'appelle|nom\s*:?)\s*([A-Za-zÀ-ÖØ-öø-ÿ\'\-\s]{2,60})', text, re.I)
    d["name"] = (m.group(1).strip() if m else None)
    m = re.search(r'(?:disponibilités?|créneau|demain|matin|après-midi|soir|lundi|mardi|mercredi|jeudi|vendredi)\s*([^\.!?]{0,60})', text, re.I)
    d["availability"] = (m.group(0).strip() if m else None)
    return d

def rule_based_next_question(pack: str, history: list) -> str:
    lead = _lead_from_history(history)
    pack = (pack or "").lower()
    need_email_strict = (pack == "medecin")

    if not lead["reason"]:
        msg = "Merci. Pouvez-vous préciser en une phrase le motif de votre demande ?"
    elif need_email_strict and not lead["email"]:
        msg = "Merci. Pour vous recontacter, quelle est votre adresse e-mail ?"
    elif not (lead["email"] or lead["phone"]):
        msg = "Merci. Pour vous recontacter, plutôt un numéro de téléphone ou une adresse e-mail ?"
    elif not lead["name"]:
        msg = "Merci. Quel est votre nom complet ?"
    elif not lead["availability"]:
        msg = "Avez-vous des disponibilités à me suggérer (par exemple demain matin) ?"
    else:
        msg = "Parfait, je transmets au cabinet pour vous proposer un créneau."

    ready = (lead["reason"] and lead["name"] and ((lead["email"] and (not need_email_strict or need_email_strict)) or lead["phone"]))
    stage = "ready" if ready and (lead["email"] if need_email_strict else (lead["email"] or lead["phone"])) else "collecting"

    ljson = {
        "reason": lead["reason"] or "",
        "name": lead["name"] or "",
        "email": lead["email"] or "",
        "phone": lead["phone"] or "",
        "availability": lead["availability"] or "",
        "stage": stage
    }
    return f"{msg}\n<LEAD_JSON>{json.dumps(ljson, ensure_ascii=False)}</LEAD_JSON>"

# ======= Envoi e-mail lead =======
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
        print("[LEAD][MAILJET]", "OK" if r.ok else f"KO {r.status_code} {r.text[:200]}")
    except Exception as e:
        print("[LEAD][MAILJET][EXC]", type(e).__name__, e)

# =========================
# MINI-DB (seed mémoire pour defaults)
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
    bot = db_get_bot(public_id)
    if bot:
        return bot.get("bot_key"), bot
    parts = public_id.split("-")
    if len(parts) < 3:
        for k, b in BOTS.items():
            if b.get("public_id") == public_id:
                b2 = dict(b); b2["bot_key"] = k; b2["public_id"] = public_id
                return k, b2
        return None, None
    bot_key = "-".join(parts[:2])
    b = BOTS.get(bot_key)
    if not b:
        return None, None
    b2 = dict(b); b2["bot_key"] = bot_key; b2["public_id"] = public_id
    return bot_key, b2

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
        base = BOTS[bot_id]

        public_id = _gen_public_id(email or str(uuid.uuid4()), bot_id)

        bot_db = {
            "public_id": public_id,
            "bot_key": bot_id,
            "pack": base["pack"],
            "name": base["name"],
            "color": color or base["color"],
            "avatar_file": avatar or base["avatar_file"],
            "greeting": greet or "",
            "buyer_email": email,
            "owner_name": (email.split("@")[0].title() if email else "Client"),
            "profile": profile,
        }
        db_upsert_bot(bot_db)

        if not stripe.api_key or not PRICE_ID:
            return redirect(f"{BASE_URL}/recap?pack={pack}&public_id={public_id}&session_id=fake_checkout_dev", code=303)

        session_obj = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": PRICE_ID, "quantity": 1}],
            customer_email=email,
            success_url=f"{BASE_URL}/recap?pack={pack}&public_id={public_id}&session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{BASE_URL}/inscription?pack={pack}&color={color}&avatar={avatar}",
            metadata={
                "pack": pack, "color": color, "avatar": avatar,
                "greeting": greet, "contact_info": contact,
                "persona_x": px, "persona_y": py,
                "public_id": public_id
            }
        )
        return redirect(session_obj.url, code=303)
    return render_template("inscription.html", title="Inscription")

@app.route("/recap")
def recap_page():
    pack = request.args.get("pack", "avocat")
    public_id = request.args.get("public_id", "").strip()
    bot = db_get_bot(public_id) if public_id else None

    if not bot:
        key = "avocat-001" if pack == "avocat" else ("medecin-003" if pack == "medecin" else "immo-002")
        base = BOTS[key]
        bot = {
            "public_id": public_id or f"{key}-demo",
            "name": base["name"],
            "owner_name": "Client",
        }

    display   = bot.get("name") or "Betty Bot"
    owner     = bot.get("owner_name") or ""
    full_name = f"{display} — {owner}" if owner else display

    return render_template(
        "recap.html",
        base_url=BASE_URL,
        pack=pack,
        public_id=bot.get("public_id") or "",
        full_name=full_name,
        title="Récapitulatif"
    )

@app.route("/chat")
def chat_page():
    # Iframe embarqué : /chat?public_id=...&embed=1
    public_id = (request.args.get("public_id") or "").strip()
    embed     = request.args.get("embed", "0") == "1"

    bot = db_get_bot(public_id)
    if not bot:
        base = BOTS["avocat-001"]
        bot = {
            "public_id": public_id or "avocat-001-demo",
            "name": base["name"],
            "color": base["color"],
            "avatar_file": base["avatar_file"],
            "greeting": "Bonjour, je suis Betty. Comment puis-je vous aider ?",
            "owner_name": "Client",
            "profile": {},
            "pack": base["pack"]
        }

    display_name = bot.get("name") or "Betty Bot"
    pack_code = (bot.get("pack") or "").lower()
    pack_label_map = {
        "medecin": "Médecin",
        "avocat": "Avocat",
        "immo": "Immobilier",
        "immobilier": "Immobilier",
        "notaire": "Notaire",
    }
    pack_label = pack_label_map.get(pack_code, "")
    full_name = f"{display_name} ({pack_label})" if pack_label else display_name

    return render_template(
        "chat.html",
        title="Betty — Chat",
        base_url=BASE_URL,
        public_id=bot.get("public_id") or "",
        full_name=full_name,  # SANS email acheteur
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
        bot_key = "avocat-001"
        bot = BOTS[bot_key]

    # Historique : conv_id (localStorage) > cookies Flask
    if conv_id:
        history = CONVS.get(conv_id, [])
    else:
        key = f"conv_{public_id or bot_key}"
        history = session.get(key, [])
    history = history[-6:]
    # L.556-L.562 — récupère buyer_email depuis payload OU depuis l'URL du chat (Referer)
from urllib.parse import urlparse, parse_qs  # idempotent si déjà importé plus haut

referrer = request.referrer or ""
q = parse_qs(urlparse(referrer).query) if referrer else {}
buyer_from_ref = (q.get("buyer_email", [None])[0] or "")  # valeur dans l'URL du /chat
payload_buyer_email = (payload.get("buyer_email") or buyer_from_ref or "").strip()
app.logger.info(f"[DBG] buyer_email payload='{payload.get('buyer_email')}' ref='{buyer_from_ref}' public_id='{public_id}'")

    # L.556 — debug pour vérifier ce que le front envoie
    payload_buyer_email = (payload.get("buyer_email") or "").strip()
    app.logger.info(f"[DBG] payload_keys={list(payload.keys())} buyer_email_in='{payload_buyer_email}' public_id='{public_id}'")

    system_prompt = build_system_prompt(bot.get("pack", "avocat"), bot.get("profile", {}), bot.get("greeting", ""))

    full_text = call_llm_with_history(system_prompt=system_prompt, history=history, user_input=user_input)
    if not full_text:  # Together KO -> fallback rule-based
        full_text = rule_based_next_question(bot.get("pack",""), history + [{"role":"user","content": user_input}])

    response_text, lead = extract_lead_json(full_text)

    # maj historique
    history.append({"role": "user", "content": user_input})
    history.append({"role": "assistant", "content": response_text})
    if conv_id:
        CONVS[conv_id] = history
    else:
        session[f"conv_{public_id or bot_key}"] = history

    # Envoi lead quand ready -> e-mail d'inscription (DB)
        # Envoi lead quand ready -> e-mail d'inscription (DB)
    if lead and isinstance(lead, dict):
        stage_ok = False
        if bot.get("pack") == "medecin":
            stage_ok = (
                lead.get("stage") == "ready"
                and bool(lead.get("email"))
                and bool(lead.get("name"))
                and bool(lead.get("reason"))
            )
        else:
            stage_ok = (
                lead.get("stage") == "ready"
                and bool(lead.get("name"))
                and bool(lead.get("reason"))
                and (lead.get("email") or lead.get("phone"))
            )

        if stage_ok:
            # L.594-L.598 — résolution définitive de l'adresse cible
        buyer_email = (
            bot.get("buyer_email")
            or ((db_get_bot(public_id) or {}).get("buyer_email") if public_id else None)
            or payload_buyer_email  # ← nouveau fallback robuste
        )

            )
            if not buyer_email:
                app.logger.warning(
                    f"[LEAD] buyer_email introuvable pour public_id={public_id} (pack={bot.get('pack')})"
                )
            else:
                app.logger.info(f"[LEAD] envoi -> {buyer_email} (public_id={public_id})")
                # L.600
app.logger.info(f"[LEAD] envoi -> {buyer_email} (public_id={public_id})")
                send_lead_email(buyer_email, lead, bot_name=bot.get("name") or "Betty Bot")

    # L.605
return jsonify({
    "response": response_text,
    "stage": (lead or {}).get("stage") if lead else None,
    "debug_to": buyer_email if (lead and lead.get("stage") == "ready") else None
})


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
        "buyer_email": bot.get("buyer_email") or "" 
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

# --- TEST MAILJET (diagnostic simple) ---
@app.route("/api/test_mailjet")
def test_mailjet():
    to = (request.args.get("to") or os.getenv("TEST_TO_EMAIL") or "").strip()
    if not to:
        return jsonify({"ok": False, "error": "missing 'to' param"}), 400

    # payload de test minimal
    lead = {
        "reason": "Test automatique",
        "name": "Lead Test",
        "email": "lead@example.com",
        "phone": "+33000000000",
        "availability": "demain 10h",
        "stage": "ready",
    }
    send_lead_email(to, lead, bot_name="Betty Bot (test)")
    return jsonify({"ok": True, "to": to})

if __name__ == "__main__":
    # Dev local : mettre SESSION_SECURE=False pour autoriser cookies non-HTTPS
    app.run(host="0.0.0.0", port=5000, debug=True)
