from vercel_wsgi import handle
from app import app  # app = Flask(__name__) dans app.py

def handler(event, context):
    return handle(app, event, context)
