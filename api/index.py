# api/index.py
def handler(request):
    return (
        200,
        { "Content-Type": "text/html; charset=utf-8" },
        b"<!doctype html><meta charset='utf-8'><title>Betty</title><h1>Betty Bots</h1><p>Home OK</p>"
    )
