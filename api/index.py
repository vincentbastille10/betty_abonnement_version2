# api/index.py
# Handler natif Vercel qui redirige chaque requête vers ton app Flask
# sans dépendre de vercel-wsgi.

from http.server import BaseHTTPRequestHandler
from urllib.parse import urlsplit
from app import app as flask_app

# Utilise le client de test Flask pour rejouer la requête entrante
def _forward_to_flask(method, path, headers, body):
    # Convertit headers en dict simple attendu par Flask test_client
    hdrs = {}
    for k, v in headers.items():
        # Host est géré par Flask, on évite les conflits
        if k.lower() == "host":
            continue
        hdrs[k] = v

    with flask_app.test_client() as c:
        resp = c.open(
            path=path,
            method=method.upper(),
            headers=hdrs,
            data=body,
            buffered=True,
            follow_redirects=False,
        )
        return resp

class handler(BaseHTTPRequestHandler):
    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length > 0:
            return self.rfile.read(length)
        return b""

    def _handle(self, method):
        # Conserve le path + query string
        # (Vercel fournit déjà self.path complet)
        path = self.path
        body = self._read_body()

        # Rejoue la requête côté Flask
        resp = _forward_to_flask(method, path, self.headers, body)

        # Renvoie la réponse au client
        self.send_response(resp.status_code)
        # Copie des headers (évite header multiple pour 'Content-Length' si déjà géré)
        for k, v in resp.headers.items():
            if k.lower() in ("content-length", "transfer-encoding"):
                continue
            self.send_header(k, v)
        # Taille exacte du body
        data = resp.get_data()
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        # Body
        if data:
            self.wfile.write(data)

    # Méthodes HTTP usuelles
    def do_GET(self):        self._handle("GET")
    def do_POST(self):       self._handle("POST")
    def do_PUT(self):        self._handle("PUT")
    def do_PATCH(self):      self._handle("PATCH")
    def do_DELETE(self):     self._handle("DELETE")
    def do_OPTIONS(self):    self._handle("OPTIONS")
