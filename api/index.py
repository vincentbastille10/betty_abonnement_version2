from vercel_wsgi import handle
from app import app  # doit exister: app.py à la racine avec app = Flask(__name__)

def handler(event, context):
    return handle(app, event, context)
