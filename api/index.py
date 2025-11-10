# api/index.py
# WSGI minimal pour valider la route /api et /api/*

def app(environ, start_response):
    body = b"WSGI OK"
    start_response("200 OK", [
        ("Content-Type", "text/plain; charset=utf-8"),
        ("Content-Length", str(len(body)))
    ])
    return [body]

handler = app
