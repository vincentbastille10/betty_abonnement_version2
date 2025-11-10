from vercel_wsgi import handle
from app import app  # ton app Flask (app = Flask(__name__) dans app.py)

def handler(event, context):
    return handle(app, event, context)
