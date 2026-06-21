import threading, time, traceback

_diag = []

def _log(msg):
    _diag.append(str(msg))
    print(msg)

def start_server():
    _log("Python started")
    host = "127.0.0.1"
    port = 8742

    try:
        import RNS
        _log("import RNS OK")
    except Exception as e:
        _log(f"import RNS FAIL: {type(e).__name__}: {e}\n{traceback.format_exc()}")
        return "None", "\n".join(_diag)

    try:
        from aiohttp import web
        _log("import aiohttp OK")
    except Exception as e:
        _log(f"import aiohttp FAIL: {type(e).__name__}: {e}\n{traceback.format_exc()}")
        return "None", "\n".join(_diag)

    try:
        app = web.Application()
        async def hello(request):
            return web.Response(text="chatxz Android")
        app.router.add_get("/", hello)
        t = threading.Thread(target=lambda: web.run_app(app, host=host, port=port, print=lambda _: None), daemon=True)
        t.start()
        time.sleep(2)
        _log(f"Server running on {host}:{port}")
        return host, str(port)
    except Exception as e:
        _log(f"Server start FAIL: {type(e).__name__}: {e}\n{traceback.format_exc()}")
        return "None", "\n".join(_diag)
