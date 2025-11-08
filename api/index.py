# api/index.py
# Adaptateur Vercel -> Flask (WSGI)
# Vercel cherche une variable 'app' (ou 'handler') qui est un WSGI callable.

try:
    from app import app as app  # WSGI callable
except Exception as e:
    # Log explicite pour voir les erreurs d'import dans les logs Vercel
    import traceback
    print("[BOOT][ERROR] Import app.py failed:", e)
    traceback.print_exc()
    # Fallback WSGI qui renvoie 500 + message clair
    def app(environ, start_response):
        start_response('500 Internal Server Error', [('Content-Type', 'text/plain; charset=utf-8')])
        return [b"App failed to load. Check Vercel function logs for stack trace."]
