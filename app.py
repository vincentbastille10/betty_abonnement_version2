# app.py — version propre (Demo vs Acheté, LLM des deux côtés, garde-fous + e-mail)

from __future__ import annotations

# Standard library
import os
import re
import json
import uuid
import time
import base64
import sqlite3
import hashlib
from pathlib import Path
from contextlib import contextmanager
from urllib.parse import urlencode
import sys
import traceback

# Third-party
from flask import (
    Flask, render_template, request, jsonify, redirect,
    url_for, session, send_from_directory, Response
)
import requests
import stripe
import yaml
from jinja2 import TemplateNotFound

# --- Gestion globale des exceptions non interceptées (log) ---
sys.excepthook = lambda t, v, tb: traceback.print_exception(t, v, tb)

# ==== APP FLASK ====
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")

# ---- Cookies / sécurité iframe ----
SESSION_SECURE = os.getenv("SESSION_SECURE", "true").lower() == "true"
app.config.update(
    SESSION_COOKIE_SAMESITE="None",
    SESSION_COOKIE_SECURE=SESSION_SECURE,
)

# ---- Config LLM / Stripe / Mailjet / Base ----
TOGETHER_API_KEY = os.getenv("TOGETHER_API_KEY", "").strip()
TOGETHER_API_URL = "https://api.together.xyz/v1/chat/completions"
LLM_MODEL = os.getenv("LLM_MODEL", "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo").strip()
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "180"))

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "").strip()
PRICE_ID = os.getenv("STRIPE_PRICE_ID", "").strip()

BASE_URL = (os.getenv("BASE_URL", "http://127.0.0.1:5000")).rstrip("/")

MJ_API_KEY    = os.getenv("MJ_API_KEY", "").strip()
MJ_API_SECRET = os.getenv("MJ_API_SECRET", "").strip()
MJ_FROM_EMAIL = os.getenv("MJ_FROM_EMAIL", "no-reply@spectramedia.online").strip()
MJ_FROM_NAME  = os.getenv("MJ_FROM_NAME", "Spectra Media AI").strip()

# ➕ Nouveaux env pour routage des leads en démo
DEMO_LEAD_EMAIL = os.getenv("DEMO_LEAD_EMAIL", "").strip()

app.jinja_env.globals["BASE_URL"] = BASE_URL

# ==== Base de données ====
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
    con = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA journal_mode=WAL;")
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

db_init()

# ==== Favicons & manifest (anti 404->500) ====
@app.route("/favicon.ico")
def favicon_root():
    p = os.path.join(app.root_path, "static", "favicon.ico")
    if os.path.exists(p):
        return send_from_directory(os.path.dirname(p), os.path.basename(p))
    return "", 204

@app.route("/favicon.png")
def favicon_png():
    p = os.path.join(app.root_path, "static", "favicon.png")
    if os.path.exists(p):
        return send_from_directory(os.path.dirname(p), os.path.basename(p))
    return "", 204

@app.route("/favicon-16x16.png")
def fav16():
    p = os.path.join(app.root_path, "static", "favicon-16x16.png")
    if os.path.exists(p):
        return send_from_directory(os.path.dirname(p), os.path.basename(p))
    return "", 204

@app.route("/favicon-32x32.png")
def fav32():
    p = os.path.join(app.root_path, "static", "favicon-32x32.png")
    if os.path.exists(p):
        return send_from_directory(os.path.dirname(p), os.path.basename(p))
    return "", 204

@app.route("/site.webmanifest")
def site_manifest():
    p = os.path.join(app.root_path, "static", "site.webmanifest")
    if os.path.exists(p):
        return send_from_directory(os.path.dirname(p), os.path.basename(p))
    return jsonify({"name":"Betty Bots","short_name":"Betty","icons":[]}), 200

# ==== Helpers ====
def static_url(filename: str) -> str:
    return url_for("static", filename=filename)

def parse_contact_info(raw: str) -> dict:
    raw = (raw or "").strip()
    if not raw:
        return {"raw": "", "name": "", "email": "", "phone": "", "address": "", "hours": ""}
    m_email = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', raw)
    email = m_email.group(0) if m_email else ""
    m_phone = re.search(r'(\+?\d[\d \.\-]{6,})', raw)
    phone = m_phone.group(1).strip() if m_phone else ""
    m_hours = re.search(r'(horaire|heures?|ouvertures?)\s*[:\-]?\s*(.+)', raw, re.I)
    hours = m_hours.group(2).strip() if m_hours else ""
    m_name = re.search(r'(?:nom|entreprise|cabinet)\s*[:\-]?\s*(.+)', raw, re.I)
    name = m_name.group(1).strip() if m_name else ""
    m_addr = re.search(r'(?:adresse|address)\s*[:\-]?\s*(.+)', raw, re.I)
    address = m_addr.group(1).strip() if m_addr else ""
    return {"raw": raw, "name": name, "email": email, "phone": phone, "address": address, "hours": hours}

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
    path = f"data/packs/{pack_name}.yaml"
    base = (
        "Tu es l'assistante AI du professionnel. Ta mission prioritaire est de QUALIFIER TRÈS VITE "
        "(2 échanges maximum avant de demander les coordonnées), puis de proposer un rappel."
    )
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                data = yaml.safe_load(f) or {}
            base = data.get("prompt", base)
        except Exception:
            pass
    biz  = build_business_block(profile)
    guide = """
RÈGLES OBLIGATOIRES (communes à TOUS les métiers) :
- Style : clair, 1 à 2 phrases max par message. Une seule question à la fois.
- Après 1–2 phrases de mise en contexte maximum, collecte IMMÉDIATEMENT dans cet ordre :
  1) « Quel est votre nom et prénom complets ? »
  2) « Quel est votre numéro de téléphone ? »
  3) « Quelle est votre adresse e-mail ? »
- Dès que téléphone + nom complet + e-mail sont collectés, écris :
  « Parfait, je transmets vos coordonnées. Vous serez rappelé rapidement. »
- N’affiche jamais de variables (pas de {{...}}) ni de JSON à l’écran.

BALISE TECHNIQUE (dernière ligne, une seule ligne, sans markdown) :
<LEAD_JSON>{"reason":"", "name":"", "email":"", "phone":"", "availability":"", "stage":"collecting|ready"}</LEAD_JSON>

RAPPEL :
- Le JSON doit tenir sur UNE ligne.
- Passe "stage" à "ready" UNIQUEMENT quand téléphone + nom complet + email sont présents (peu importe le métier).
"""

    greet = f"\nMessage d'accueil recommandé : {greeting}\n" if greeting else ""
    return f"{base}\n{biz}\n{guide}\n{greet}"

# ==== LLM ====
def call_llm_with_history(system_prompt: str, history: list, user_input: str) -> str:
    if not TOGETHER_API_KEY:
        return ""
    headers = {"Authorization": f"Bearer {TOGETHER_API_KEY}", "Content-Type": "application/json"}
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history or [])
    messages.append({"role": "user", "content": user_input})
    payload = {
        "model": LLM_MODEL,
        "max_tokens": LLM_MAX_TOKENS,
        "temperature": 0.3,
        "messages": messages
    }
    backoffs = [0.4, 0.8, 1.6]

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
    return ""

# ==== LEAD JSON helpers ====
LEAD_TAG_RE = re.compile(
    r"<\s*LEAD_?JSON\s*>\s*(\{.*?\})\s*</\s*LEAD_?JSON\s*>",
    re.IGNORECASE | re.DOTALL
)

def extract_lead_json(text: str):
    """Renvoie (message_sans_balises, lead_dict_ou_None). Prend la DERNIÈRE balise."""
    if not text:
        return text, None
    matches = list(LEAD_TAG_RE.finditer(text))
    lead = None
    if matches:
        m = matches[-1]
        lead_raw = (m.group(1) or "").strip()
        try:
            lead = json.loads(lead_raw)
        except Exception:
            lead = None
        text = LEAD_TAG_RE.sub("", text)
    return (text or "").strip(), lead

def _lead_from_history(history: list) -> dict:
    """
    Extraction robuste à partir de l'historique utilisateur (LLM + texte brut).
    Tolère : MAJUSCULES, tirets, apostrophes, 'nom prénom' sans intro, etc.
    Ne dépend plus du 'dernier message' pour le nom : fouille tout l'historique.
    """
    user_msgs = [ (m.get("content") or "").strip() for m in history if m.get("role") == "user" ]
    user_text = " ".join(user_msgs)
    d = {"reason": "", "email": "", "phone": "", "name": "", "availability": "", "stage": "collecting"}

    if not user_msgs:
        return d

    # Email — premier match trouvé dans tout l'historique (on lit depuis la fin)
    m_email = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', user_text, flags=re.I)
    if m_email:
        d["email"] = m_email.group(0)

    # Téléphone — simple mais tolérant (on normalise en enlevant espaces/().-)
    m_phone = re.search(r'(\+?\d[\d \.\-\(\)]{6,})', user_text)
    if m_phone:
        d["phone"] = re.sub(r'[^0-9\+]', '', m_phone.group(1)).strip()

    # Nom — 1) formulations explicites
    m_name = re.search(r"(?:je m(?:'|e)appelle|nom\s*:?)\s*([A-Za-zÀ-ÖØ-öø-ÿ'\-\s]{2,80})", user_text, re.I)
    if m_name:
        d["name"] = m_name.group(1).strip()

    # Nom — 2) fallback robuste : balaye l'historique à l'envers et prend
    # le PREMIER message "propre" comportant 2 à 3 tokens alphabétiques (pas de chiffres, pas d'@)
    if not d["name"]:
        for msg in reversed(user_msgs):
            txt = msg.strip()
            if not txt or '@' in txt or re.search(r'\d', txt):
                continue
            # uniquement lettres/accent/apostrophes/tirets/espaces, et longueur raisonnable
            if not re.fullmatch(r"[A-Za-zÀ-ÖØ-öø-ÿ' \-]{3,80}", txt):
                continue
            tokens = [t for t in re.split(r"\s+", txt) if t]
            if 2 <= len(tokens) <= 3:
                # Évite des faux positifs super génériques (ex: "bonjour", "oui merci")
                lower = txt.lower()
                if any(w in lower for w in ["bonjour", "bonsoir", "merci", "svp", "rdv", "rendez", "appel", "mail"]):
                    continue
                d["name"] = " ".join(t.capitalize() for t in tokens)
                break

    # Motif / disponibilité (indicatif)
    m_reason = re.search(r'(?:souhaite|veux|voudrais|besoin|motif|pour)\s*:?(.{5,140})', user_text, re.I)
    if m_reason:
        d["reason"] = m_reason.group(1).strip()
    m_avail = re.search(r'(demain|matin|après-midi|soir|lundi|mardi|mercredi|jeudi|vendredi)[^\.!?]{0,60}', user_text, re.I)
    if m_avail:
        d["availability"] = m_avail.group(0).strip()

    if d["phone"] and d["name"] and d["email"]:
        d["stage"] = "ready"
    return d

# Limitation à 1 question / 2 phrases
SENT_SPLIT_RE = re.compile(r"(?<=[\.\!\?])\s+")
def enforce_single_question(text: str, max_sentences: int = 2) -> str:
    if not text:
        return text
    if "?" in text:
        first_q_idx = text.index("?") + 1
        text = text[:first_q_idx]
    sentences = SENT_SPLIT_RE.split(text)
    sentences = [s.strip() for s in sentences if s.strip()]
    text = " ".join(sentences[:max_sentences])
    text = re.sub(r"\s+", " ", text).strip()
    return text

# ==== Intent & consentement ====
INTENT_RDV_RE = re.compile(r"\b(rendez[- ]?vous|rdv|prise? (de )?rendez[- ]?vous|prendre un rdv|booking|appointment)\b", re.I)
CONSENT_RE    = re.compile(r"\b(oui|ok|okay|yes|si|d['’ ]?accord|vas[- ]?y|go|let.?s go|ça marche|ca marche)\b", re.I)

def guardrailed_reply(history: list, user_input: str, llm_text: str, pack: str) -> tuple[str, dict, bool, str]:
    """
    Retourne (response_text, lead_dict, should_send_now, stage)
    Séquence déterministe : Nom -> Téléphone -> Email (+ consentement optionnel).
    Envoi autorisé si stage=ready OU consentement explicite avec au moins 1 info utile.
    """
    augmented_history = history + ([{"role":"user","content": user_input}] if user_input else [])
    lead = _lead_from_history(augmented_history)

    # 1) Premier tour : ouverture imposée
    if len(history) == 0:
        msg = "Bonjour, qu’est-ce que je peux faire pour vous ?"
        return enforce_single_question(msg), lead, False, "collecting"

    # 2) Intent & consent
    INTENT_RDV_RE = re.compile(r"\b(rendez[- ]?vous|rdv|prise? (de )?rendez[- ]?vous|prendre un rdv|booking|appointment)\b", re.I)
    CONSENT_RE    = re.compile(r"\b(oui|ok|okay|yes|si|d['’ ]?accord|vas[- ]?y|go|let.?s go|ça marche|ca marche)\b", re.I)
    text_window = " ".join([m["content"] for m in augmented_history if m.get("role") in ("user","assistant")]) or ""
    intent_rdv  = bool(INTENT_RDV_RE.search(text_window))
    consent     = bool(CONSENT_RE.search(user_input or ""))

    # 3) Nettoyage texte LLM
    response_text_llm, _ = extract_lead_json(llm_text or "")
    response_text_llm = re.sub(r"<\s*LEAD_?JSON\s*>.*?</\s*LEAD_?JSON\s*>", "", response_text_llm or "", flags=re.DOTALL|re.IGNORECASE).strip()
    must_take_control = intent_rdv or not response_text_llm or (len(response_text_llm) < 6)

    # Helper pour enchaîner proprement une question après le texte LLM
    def llm_plus(q: str) -> str:
        base = response_text_llm if response_text_llm else ""
        txt = f"{base} {q}".strip()
        return enforce_single_question(txt)

    # 4) Contrôle (ordre = NOM → TÉLÉPHONE → EMAIL)
    if must_take_control:
        if not lead["name"]:
            q = "Pour commencer, quel est votre nom et prénom complets ?"
            return enforce_single_question(q), lead, consent, "collecting"
        if not lead["phone"]:
            q = "Merci. Quel est votre numéro de téléphone ?"
            return enforce_single_question(q), lead, consent, "collecting"
        if not lead["email"]:
            q = "Parfait. Quelle est votre adresse e-mail ?"
            return enforce_single_question(q), lead, consent, "collecting"
        ok = "Parfait, je transmets vos coordonnées pour vous proposer un rendez-vous."
        return enforce_single_question(ok), {**lead, "stage":"ready"}, True, "ready"

    # 5) Sinon on conserve le LLM mais on impose la prochaine question manquante
    if not lead["name"]:
        return enforce_single_question("Pour commencer, quel est votre nom et prénom complets ?"), lead, consent, "collecting"
    if not lead["phone"]:
        return enforce_single_question("Merci. Quel est votre numéro de téléphone ?"), lead, consent, "collecting"
    if not lead["email"]:
        return enforce_single_question("Parfait. Quelle est votre adresse e-mail ?"), lead, consent, "collecting"

    ok = "Parfait, je transmets vos coordonnées pour vous proposer un rendez-vous."
    return enforce_single_question(ok), {**lead, "stage":"ready"}, True, "ready"

def rule_based_next_question(pack: str, history: list) -> str:
    lead = _lead_from_history(history)
    if not lead["phone"]:
        msg = "Quel est votre numéro de téléphone ?"
    elif not lead["name"]:
        msg = "Quel est votre nom et prénom complets ?"
    elif not lead["email"]:
        msg = "Quelle est votre adresse e-mail ?"
    else:
        msg = "Parfait, je transmets vos coordonnées. Vous serez rappelé rapidement."
        lead["stage"] = "ready"
    return f"{msg}\n<LEAD_JSON>{json.dumps(lead, ensure_ascii=False)}</LEAD_JSON>"

# ==== Email lead (Mailjet) ====
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

# ==== Bots en mémoire ====
BOTS = {
    "avocat-001":  {"pack":"avocat","name":"Betty Bot (Avocat)","color":"#4F46E5","avatar_file":"avocat.jpg","profile":{},"greeting":"","buyer_email":None,"owner_name":None,"public_id":None},
    "immo-002":    {"pack":"immo","name":"Betty Bot (Immobilier)","color":"#16A34A","avatar_file":"immo.jpg","profile":{},"greeting":"","buyer_email":None,"owner_name":None,"public_id":None},
    "medecin-003": {"pack":"medecin","name":"Betty Bot (Médecin)","color":"#0284C7","avatar_file":"medecin.jpg","profile":{},"greeting":"","buyer_email":None,"owner_name":None,"public_id":None},
    "spectra-demo": {
        "pack": "avocat",
        "name": "Betty Bot (Spectra Media)",
        "color": "#4F46E5",
        "avatar_file": "avocat.jpg",
        "profile": {},
        "greeting": "Bonjour et bienvenue chez Spectra Media. Souhaitez-vous créer votre Betty Bot métier ?",
        "buyer_email": None,
        "owner_name": "Spectra Media",
        "public_id": "spectra-demo"
    },
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

# ==== Mémoire conversations ====
CONVS = {}

# ==== Pages ====
@app.get("/api")
def health():
    return "OK Betty via /api"

@app.route("/")
def index():
    try:
        return render_template("index.html", title="Découvrez Betty")
    except TemplateNotFound:
        return """
        <!doctype html><meta charset="utf-8">
        <style>
          body{margin:0;background:#0b0f1e;color:#e8ecff;font:16px/1.5 system-ui,Segoe UI,Roboto,Inter,sans-serif;display:grid;place-items:center;height:100vh}
          .card{background:#12172a;border:1px solid rgba(255,255,255,.08);border-radius:16px;padding:28px;max-width:720px;box-shadow:0 10px 30px rgba(0,0,0,.25)}
          h1{margin:0 0 12px;font-size:22px}
          code{background:#0b1222;padding:2px 6px;border-radius:6px}
          a{color:#8ab4ff;text-decoration:none}
          .muted{color:#a8b2c8}
        </style>
        <div class="card">
          <h1>Betty Bots — déploiement minimal</h1>
          <p class="muted">Le template <code>templates/index.html</code> n’a pas été trouvé.<br>Le fallback s’affiche pour éviter une erreur.</p>
          <p>Vérifie que le fichier est bien <b>commit</b> et <b>push</b> : <code>templates/index.html</code>.</p>
          <p>Interface config : <a href="/config">/config</a></p>
        </div>
        """, 200

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
    try:
        return render_template("config.html", title="Configurer votre bot")
    except TemplateNotFound:
        return """
        <!doctype html><meta charset="utf-8">
        <h1>Configurer votre bot</h1>
        <form method="post">
          <label>Pack <select name="pack">
            <option value="avocat">Avocat</option>
            <option value="medecin">Médecin</option>
            <option value="immo">Immobilier</option>
          </select></label><br><br>
          <label>Couleur <input name="color" value="#4F46E5"></label><br><br>
          <label>Avatar <input name="avatar" value="avocat.jpg"></label><br><br>
          <label>Message d'accueil <input name="greeting" value=""></label><br><br>
          <label>Infos contact (nom, email, tel, horaires...)<br>
            <textarea name="contact_info" rows="4" cols="50"></textarea>
          </label><br><br>
          <button type="submit">Continuer</button>
        </form>
        """, 200

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

        try:
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
        except Exception as e:
            app.logger.exception(f"[STRIPE] {e}")
            return redirect(f"{BASE_URL}/recap?pack={pack}&public_id={public_id}&session_id=fake_checkout_dev", code=303)

    return render_template("inscription.html", title="Inscription")

def _slug_from_pack(pack: str) -> str:
    pack = (pack or "").lower()
    return {"agent_immobilier":"immo", "immobilier":"immo", "avocat":"avocat", "medecin":"medecin"}.get(pack, "immo")

@app.route("/recap")
def recap_page():
    pack = (request.args.get("pack") or "").strip().lower() or "avocat"
    public_id = (request.args.get("public_id") or "").strip()

    bot = db_get_bot(public_id) if public_id else None
    if not bot:
        key = "avocat-001" if pack == "avocat" else ("medecin-003" if pack == "medecin" else "immo-002")
        base = BOTS[key]
        bot = {
            "public_id": public_id or f"{key}-demo",
            "name": base["name"],
            "owner_name": "Client",
            "buyer_email": "",
            "pack": base["pack"],
            "color": base["color"],
            "avatar_file": base["avatar_file"],
            "greeting": ""
        }

    display_name = bot.get("name") or "Betty Bot"
    owner        = bot.get("owner_name") or ""
    full_name    = f"{display_name} — {owner}" if owner else display_name

    slug = _slug_from_pack(bot.get("pack") or pack)
    avatar_file = bot.get("avatar_file") or f"logo-{slug}.jpg"

    params = {"public_id": bot.get("public_id"), "embed": "1"}
    buyer = (bot.get("buyer_email") or "").strip()
    if buyer:
        params["buyer_email"] = buyer
    embed_url = f"{BASE_URL}/chat?{urlencode(params)}"

    iframe_snippet = (
        '<div style="position:relative;width:100%;max-width:420px;height:620px;margin:0 auto;">\n'
        f'  <iframe src="{embed_url}" title="{full_name}" '
        'style="width:100%;height:100%;border:0;border-radius:16px;'
        'box-shadow:0 10px 30px rgba(0,0,0,.25);background:#0b0f1e;" '
        'loading="lazy" referrerpolicy="no-referrer-when-downgrade" '
        'allow="clipboard-read; clipboard-write; microphone; autoplay"></iframe>\n'
        '</div>'
    )

    cfg = {
        "pack":        bot.get("pack") or pack,
        "color":       bot.get("color") or "#4F46E5",
        "greeting":    bot.get("greeting") or "Bonjour, qu’est-ce que je peux faire pour vous ?",
        "contact":     (bot.get("profile") or {}).get("raw") or "",
        "px":          request.args.get("px") if request.args.get("px") is not None else "0.5",
        "py":          request.args.get("py") if request.args.get("py") is not None else "0.5",
        "avatar_url":  static_url(avatar_file),
        "public_id":   bot.get("public_id") or "",
        "buyer_email": bot.get("buyer_email") or "",
        "display_name": display_name,
        "owner_name":   owner,
        "full_name":    full_name,
        "embed_url":    embed_url,
        "iframe_snippet": iframe_snippet,
    }

    try:
        return render_template(
            "recap.html",
            title="Récapitulatif",
            cfg=cfg,
            info=cfg,
            base_url=BASE_URL,
            full_name=full_name
        )
    except TemplateNotFound:
        return f"<!doctype html><meta charset='utf-8'><pre>{json.dumps(cfg, ensure_ascii=False, indent=2)}</pre>", 200

@app.route("/chat")
def chat_page():
    public_id = (request.args.get("public_id") or "").strip()
    embed     = request.args.get("embed", "0") == "1"
    buyer_email = request.args.get("buyer_email", "").strip()

    bot = db_get_bot(public_id)
    if not bot:
        base = BOTS["avocat-001"]
        bot = {
            "public_id": public_id or "avocat-001-demo",
            "name": base["name"],
            "color": base["color"],
            "avatar_file": base["avatar_file"],
            "greeting": "Bonjour, qu’est-ce que je peux faire pour vous ?",
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
    full_name = display_name if "(" in display_name else (f"{display_name} ({pack_label})" if pack_label else display_name)

    try:
        return render_template(
            "chat.html",
            title="Betty — Chat",
            base_url=BASE_URL,
            public_id=bot.get("public_id") or "",
            full_name=full_name,
            header_title="Betty Bot, votre assistante AI",
            color=bot.get("color") or "#4F46E5",
            avatar_url=static_url(bot.get("avatar_file") or "avocat.jpg"),
            greeting=bot.get("greeting") or "Bonjour, qu’est-ce que je peux faire pour vous ?",
            buyer_email=buyer_email,  # passage pour back uniquement, ne pas afficher côté front
            embed=embed
        )
    except TemplateNotFound:
        return "<!doctype html><meta charset='utf-8'><h1>Chat</h1><p>Template manquant.</p>", 200

# ==== API bot ====
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

    # Historique (6 derniers messages)
    if conv_id:
        history = CONVS.get(conv_id, [])
    else:
        key = f"conv_{public_id or bot_key}"
        history = session.get(key, [])
    history = history[-6:]

    # Choix du prompt : Demo vs Acheté
    demo_mode = (public_id == "spectra-demo")
    if demo_mode:
        system_prompt = (
            "Tu es **Betty Bot**, la démo officielle de Spectra Media. "
            "Objectif : présenter le concept de « bot métier » (acquisition, qualification, RDV) et collecter les coordonnées. "
            "Ton ton est chaleureux, clair et professionnel. 1 à 2 phrases max, une seule question à la fois. "
            "Ne montre jamais de JSON. Mets en avant que l’utilisateur peut obtenir SON propre Betty métier et le code d’intégration en 2 minutes. "
            "Quand tu as nom + téléphone + e-mail, écris : « Parfait, je transmets vos coordonnées à l’équipe Spectra Media. Vous serez rappelé rapidement. »"
        )
    else:
        system_prompt = build_system_prompt(
            bot.get("pack", "avocat"),
            bot.get("profile", {}),
            bot.get("greeting", "") or "Bonjour, qu’est-ce que je peux faire pour vous ?"
        )

    # Appel LLM (réponse brute potentiellement enrichie par garde-fous)
    llm_text = call_llm_with_history(system_prompt=system_prompt, history=history, user_input=user_input)
    if not llm_text:
        llm_text = rule_based_next_question(bot.get("pack",""), history + [{"role":"user","content": user_input}])

    # Garde-fous (séquence RDV + consentement)
    response_text, lead, should_send_now, stage = guardrailed_reply(history, user_input, llm_text, bot.get("pack",""))

    # Persistance de l'historique
    history.append({"role": "user", "content": user_input})
    history.append({"role": "assistant", "content": response_text})
    if conv_id:
        CONVS[conv_id] = history
    else:
        session[f"conv_{public_id or bot_key}"] = history

    # Résolution de l'adresse de destination
    # - Démo : DEMO_LEAD_EMAIL -> DEFAULT_LEAD_EMAIL -> MJ_FROM_EMAIL
    # - Acheté : email stocké (buyer_email), sinon DEFAULT/MJ_FROM_EMAIL
    default_fallback = os.getenv("DEFAULT_LEAD_EMAIL", "").strip() or MJ_FROM_EMAIL
    if demo_mode:
        buyer_email_ctx = (DEMO_LEAD_EMAIL or default_fallback)
    else:
        buyer_email_ctx = (
            (payload.get("buyer_email") or "").strip()
            or ((db_get_bot(public_id) or {}).get("buyer_email") if public_id else "")
            or (bot or {}).get("buyer_email")
            or default_fallback
        )

    # Conditions d'envoi mail
    have_any_info = any([lead.get("name"), lead.get("email"), lead.get("phone"), lead.get("reason")])
    may_send = (stage == "ready") or (should_send_now and have_any_info)

    if may_send:
        if not buyer_email_ctx:
            app.logger.warning(f"[LEAD] buyer_email introuvable pour bot_id={public_id or 'N/A'} ; email non envoyé.")
        else:
            try:
                effective_stage = "ready" if (lead.get("phone") and lead.get("name") and lead.get("email")) else "partial"
                send_lead_email(
                    to_email=buyer_email_ctx,
                    lead={
                        "reason": lead.get("reason", ""),
                        "name": lead.get("name", ""),
                        "email": lead.get("email", ""),
                        "phone": lead.get("phone", ""),
                        "availability": lead.get("availability", ""),
                        "stage": effective_stage,
                    },
                    bot_name=(bot or {}).get("name") or ("Betty Bot (Démo)" if demo_mode else "Betty Bot"),
                )
                lead["stage"] = effective_stage
                app.logger.info(f"[LEAD] Email ({effective_stage}) envoyé à {buyer_email_ctx} pour bot {public_id or ('demo' if demo_mode else 'N/A')}")
            except Exception as e:
                app.logger.exception(f"[LEAD] Erreur envoi email -> {e}")

    return jsonify({
        "response": response_text,
        "stage": lead.get("stage") if isinstance(lead, dict) else None
    })

# ==== Meta/API utilitaires ====
@app.route("/api/embed_meta")
def embed_meta():
    public_id = (request.args.get("public_id") or "").strip()
    if not public_id:
        return jsonify({"error": "missing public_id"}), 400
    _, bot = find_bot_by_public_id(public_id)
    if not bot:
        return jsonify({"error": "bot_not_found"}), 404
    # IMPORTANT : on **n’expose pas** l’adresse acheteur ici
    return jsonify({
        "bot_id": public_id,
        "owner_name": bot.get("owner_name") or "Client",
        "display_name": bot.get("name") or "Betty Bot",
        "color_hex": bot.get("color") or "#4F46E5",
        "avatar_url": static_url(bot.get("avatar_file") or "avocat.jpg"),
        "greeting": bot.get("greeting") or "Bonjour, qu’est-ce que je peux faire pour vous ?"
    })

@app.route("/api/bot_meta")
def bot_meta():
    bot_id = (request.args.get("bot_id") or request_args.get("public_id") or "").strip() if False else (request.args.get("bot_id") or request.args.get("public_id") or "").strip()
    if bot_id == "spectra-demo":
        b = BOTS["spectra-demo"]
        return jsonify({
            "name": "Betty Bot (Spectra Media)",
            "color_hex": b.get("color") or "#4F46E5",
            "avatar_url": static_url(b.get("avatar_file") or "avocat.jpg"),
            "greeting": b.get("greeting") or "Bonjour et bienvenue chez Spectra Media. Souhaitez-vous créer votre Betty Bot métier ?"
        })
    if bot_id in BOTS:
        b = BOTS[bot_id]
        demo_greetings = {
            "avocat-001":  "Bonjour et bienvenue au cabinet Werner & Werner. Que puis-je faire pour vous ?",
            "immo-002":    "Bonjour et bienvenue à l’agence Werner Immobilier. Comment puis-je vous aider ?",
            "medecin-003": "Bonjour et bienvenue au cabinet Werner Santé. Que puis-je faire pour vous ?",
        }
        return jsonify({
            "name": b.get("name") or "Betty Bot",
            "color_hex": b.get("color") or "#4F46E5",
            "avatar_url": static_url(b.get("avatar_file") or "avocat.jpg"),
            "greeting": demo_greetings.get(bot_id, "Bonjour, qu’est-ce que je peux faire pour vous ?")
        })
    _, bot = find_bot_by_public_id(bot_id)
    if not bot:
        return jsonify({"error": "bot_not_found"}), 404
    return jsonify({
        "name": bot.get("name") or "Betty Bot",
        "color_hex": bot.get("color") or "#4F46E5",
        "avatar_url": static_url(bot.get("avatar_file") or "avocat.jpg"),
        "greeting": bot.get("greeting") or "Bonjour, qu’est-ce que je peux faire pour vous ?"
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

@app.route("/api/test_mailjet")
def test_mailjet():
    to = (request.args.get("to") or os.getenv("TEST_TO_EMAIL") or "").strip()
    if not to:
        return jsonify({"ok": False, "error": "missing 'to' param"}), 400
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

@app.route("/avatar/<slug>")
def avatar(slug: str):
    static_dir = os.path.join(app.root_path, "static")
    filename = f"logo-{slug}.jpg"
    path = os.path.join(static_dir, filename)
    if os.path.exists(path):
        return send_from_directory(static_dir, filename)
    transparent_png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+Xad8AAAAASUVORK5CYII="
    )
    return Response(transparent_png, mimetype="image/png")

# ==== Main ====
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
