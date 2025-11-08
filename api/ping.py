# api/ping.py
def handler(request):
    return 200, { "Content-Type": "text/plain" }, b"OK"
