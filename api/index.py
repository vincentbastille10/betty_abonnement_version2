# api/index.py
from vercel_wsgi import handle
from app import app  # ton app Flask principale (app = Flask(__name__))

def handler(event, context):
    return handle(app, event, context)
