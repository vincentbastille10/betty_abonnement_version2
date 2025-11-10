# api/index.py
# Mini app Flask AUTONOME pour valider le runtime Vercel.
# AUCUN import depuis app.py.

from flask import Flask, jsonify, request

app = Flask(__name__)

@app.get("/")
def ping():
    return "OK: /api/ répond ✅"

@app.get("/debug")
def debug():
    return jsonify(
        method=request.method,
        headers={k: v for k, v in request.headers.items()},
        path=request.path,
        query=request.args
    )

# Expose les deux noms au cas où
handler = app
