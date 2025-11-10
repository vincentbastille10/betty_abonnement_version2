# api/index.py
# WSGI fallback pour Vercel : expose une appli WSGI nommée `app`.
# - Si l'import de `app.py` réussit, on sert ton Flask normalement.
# - Sinon, on renvoie une réponse 500 *lisible* avec la vraie exception d'import.

def _error_app(exc: Exception):
    def wsgi(environ, start_response):
        msg = f"Import error in app.py: {type(exc).__name__}: {exc}".encode("utf-8", "ignore")
        start_response("500 Internal Server Error", [("Content-Type", "text/plain; charset=utf-8"),
                                                     ("Content-Length", str(len(msg)))])
        return [msg]
    return wsgi

try:
    from app import app as _flask_app   # <- DOIT exister dans app.py
    app = _flask_app                    # WSGI callable exporté pour Vercel
    handler = _flask_app                # Au cas où Vercel cherche `handler`
except Exception as e:
    app = _error_app(e)
    handler = app
