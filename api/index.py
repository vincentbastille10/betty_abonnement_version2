# api/index.py
# WSGI "hello world" ultra-minimal — zéro dépendance.
# Si ceci renvoie 500, le souci vient du routage Vercel, pas de ton code.

def app(environ, start_response):
    body = b"WSGI OK"
    start_response(
        "200 OK",
        [("Content-Type", "text/plain; charset=utf-8"),
         ("Content-Length", str(len(body)))]
    )
    return [body]

handler = app  # certains runtimes regardent aussi "handler"
