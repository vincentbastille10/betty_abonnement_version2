from flask import Flask, render_template, request, jsonify, redirect, url_for
import os, yaml, requests

app = Flask(__name__)

# --- Configuration ---
TOGETHER_API_URL = "https://api.together.xyz/v1/chat/completions"
TOGETHER_API_KEY = os.getenv("TOGETHER_API_KEY") or "TA_CLE_API_ICI"

# Simulation d'une base de données minimale (à remplacer par une vraie table)
BOTS = {
    "avocat-001": {"pack": "avocat", "name": "Betty (Avocat)", "color": "#4F46E5", "avatar": "/static/img/avocat.png"},
    "immo-002": {"pack": "immo", "name": "Betty (Immobilier)", "color": "#16A34A", "avatar": "/static/img/immo.png"},
    "medecin-003": {"pack": "medecin", "name": "Betty (Médecin)", "color": "#0284C7", "avatar": "/static/img/medecin.png"},
}


# --- Fonctions utilitaires ---
def load_pack_prompt(pack_name):
    pack_path = f"data/packs/{pack_name}.yaml"
    if not os.path.exists(pack_path):
        return "Tu es une assistante AI professionnelle. Réponds avec clarté et concision."
    with open(pack_path, "r") as f:
        return yaml.safe_load(f).get("prompt", "")


def query_llm(user_input, pack_name):
    """Appel API Together pour générer une réponse LLM."""
    prompt = load_pack_prompt(pack_name)
    headers = {"Authorization": f"Bearer {TOGETHER_API_KEY}", "Content-Type": "application/json"}
    data = {
        "model": "meta-llama/Meta-Llama-3-8B-Instruct",
        "max_tokens": 90,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_input}
        ]
    }
    try:
        r = requests.post(TOGETHER_API_URL, headers=headers, json=data, timeout=20)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print("[LLM ERROR]", e)
        return "Désolé, une erreur est survenue lors de la génération de la réponse."


# --- Routes principales (pages HTML) ---

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/config", methods=["GET", "POST"])
def config_page():
    if request.method == "POST":
        # Sauvegarde temporaire dans la session (ou DB plus tard)
        pack = request.form.get("pack")
        color = request.form.get("color")
        avatar = request.form.get("avatar")
        return redirect(url_for("inscription_page", pack=pack, color=color, avatar=avatar))
    return render_template("config.html")

@app.route("/inscription", methods=["GET", "POST"])
def inscription_page():
    if request.method == "POST":
        email = request.form.get("email")
        pack = request.args.get("pack")
        color = request.args.get("color")
        avatar = request.args.get("avatar")
        # Ici tu pourrais envoyer un mail à ton Gmail / ajouter en DB
        print(f"[NEW SUBSCRIBER] {email} -> {pack}")
        return redirect(url_for("recap_page", pack=pack))
    return render_template("inscription.html")

@app.route("/recap")
def recap_page():
    pack = request.args.get("pack", "avocat")
    return render_template("recap.html", pack=pack)


# --- Routes API (AJAX) ---

@app.route("/api/bettybot", methods=["POST"])
def bettybot_reply():
    data = request.get_json()
    user_input = data.get("message", "")
    bot_id = data.get("bot_id", "avocat-001")

    bot_info = BOTS.get(bot_id, BOTS["avocat-001"])
    pack = bot_info["pack"]

    response = query_llm(user_input, pack)
    return jsonify({"response": response})


@app.route("/api/bot_meta")
def bot_meta():
    bot_id = request.args.get("bot_id", "avocat-001")
    bot = BOTS.get(bot_id)
    if not bot:
        return jsonify({"error": "bot inconnu"}), 404
    return jsonify({
        "name": bot["name"],
        "avatar_url": bot["avatar"],
        "color_hex": bot["color"]
    })


# --- Lancement ---
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
