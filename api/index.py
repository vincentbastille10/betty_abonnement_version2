# api/index.py
# Shim robuste pour Vercel : n'exige AUCUNE modif de app.py
# - Définit des env par défaut pour éviter les crashs à l'import
# - Importe app.py proprement
# - Expose WSGI sous les deux noms attendus: app et handler
# - Si l'import échoue, renvoie une page 500 avec la trace lisible

import os, sys, importlib, traceback

# --- Valeurs par défaut "safe" pour éviter les erreurs à l'import ---
os.environ.setdefault("FLASK_SECRET_KEY", "dev-secret-change-me")
os.environ.setdefault("SESSION_SECURE", "true")
os.environ.setdefault("DB_PATH", "/tmp/betty.db")
# Optionnel (utile si ton code l'utilise tôt)
os.environ.setdefault("BASE_URL", "https://example.com/api")
# Clés externes (ne bloquent pas si absentes, mais évitent KeyError à l'import)
os.environ.setdefault("STRIPE_SECRET_KEY", "")
os.environ.setdefault("STRIPE_PUBLISHABLE_KEY", "")
os.environ.setdefault("STRIPE_PRICE_ID", "")
os.environ.setdefault("STRIPE_WEBHOOK_SECRET", "")
os.environ.setdefault("MJ_API_KEY", "")
os.environ.setdefault("MJ_API_SECRET", "")
os.environ.setdefault("MJ_FROM_EMAIL", "no-reply@spectramedia.online")
os.environ.setdefault("MJ_FROM_NAME", "Spectra Media AI")
os.environ.setdefault("DEFAULT_LEAD_EMAIL", "vinylestorefrance@gmail.com")
os.environ.setdefault("TOGETHER_API_KEY", "")
os.environ.setdefault("LLM_MODEL", "mistralai/Mixtral-8x7B-Instruct-v0.1")
os.environ.setdefault("DEBUG", "False")

# S'assurer que le dossier racine (où se trouve app.py) est dans le path
ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

def _error_app(message: str):
    """Petite appli WSGI qui renvoie le message d'erreur lisible."""
    def wsgi(environ, start_response):
        body = message.encode("utf-8", "ignore")
        headers = [("Content-Type", "text/plain; charset=utf-8"),
                   ("Content-Length", str(len(body)))]
        start_response("500 Internal Server Error", headers)
        return [body]
    return wsgi

try:
    # Importer ton module app.py une seule fois
    app_module = importlib.import_module("app")

    # 1) priorité à l'attribut 'app' (pattern le plus courant)
    flask_app = getattr(app_module, "app", None)

    # 2) sinon, tenter une factory 'create_app()' si tu en as une
    if flask_app is None:
        create_app = getattr(app_module, "create_app", None)
        if callable(create_app):
            flask_app = create_app()

    if flask_app is None:
        raise RuntimeError(
            "app.py importé, mais ni 'app' ni 'create_app()' n'ont été trouvés."
        )

    # Exposer sous les deux noms possibles
    app = flask_app
    handler = flask_app

except Exception:
    # Renvoyer la trace complète dans la réponse HTTP (au lieu d'un 500 opaque)
    tb = traceback.format_exc()
    app = _error_app("Import error in app.py:\n" + tb)
    handler = app
