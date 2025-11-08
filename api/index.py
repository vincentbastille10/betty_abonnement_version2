# api/index.py — adaptateur Vercel -> Flask (WSGI)

try:
    from app import app as app  # WSGI callable attendu par Vercel
except Exception as e:
    import traceback
    print("[BOOT][ERROR] Import app.py failed:", e)
    traceback.print_exc()
    def app(environ, start_response):
        start_response('500 Internal Server Error', [('Content-Type', 'text/plain; charset=utf-8')])
        return [b"App failed to load. Check Vercel function logs for stack trace."]
