# api/index.py
# Handler Flask pour Vercel — version stable (Betty Abonnement v2)

import os
from app import app as application  # <-- importe ton instance Flask

# S'assure que Flask tourne en mode production sur Vercel
os.environ.setdefault("FLASK_ENV", "production")

# Vercel s’attend à trouver une variable WSGI nommée "application"
# Aucune exécution directe ici : tout passe par Vercel Serverless
