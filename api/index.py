from vercel_wsgi import handle
from app import app

def handler(event, context):
    return handle(app, event, context)
