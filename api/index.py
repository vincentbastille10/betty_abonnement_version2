# api/index.py
# Point d'entrée Vercel -> réutilise ton app Flask définie dans app.py

from app import app as flask_app

# Vercel attend une variable `app` compatible WSGI
app = flask_app
