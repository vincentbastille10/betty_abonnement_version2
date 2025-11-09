cat > api/index.py <<'PY'
import os
from app import app as application  # Flask instance "app" dans app.py

# Vercel recherche une variable "application" (WSGI)
os.environ.setdefault("FLASK_ENV", "production")
PY
