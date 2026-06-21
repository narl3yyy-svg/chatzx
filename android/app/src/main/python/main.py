import threading, time, traceback, os, socket, asyncio

_diag = []
def _log(msg):
    _diag.append(str(msg)); print(msg)

SERVER_READY = threading.Event()
SERVER_ERROR = []

def start_server():
    host, port = "127.0.0.1", 8742

    # Track startup phases for diagnostics
    phases = {}

    try:
        import RNS
        _log("RNS OK")
    except Exception as e:
        return "None", f"RNS: {type(e).__name__}: {e}"
    phases["rns"] = "ok"

    try:
        from aiohttp import web
        _log("aiohttp OK")
    except Exception as e:
        return "None", f"aiohttp: {type(e).__name__}: {e}"
    phases["aiohttp"] = "ok"

    HERE = os.path.dirname(os.path.abspath(__file__))
    STATIC = os.path.join(HERE, "chatxz", "web", "static")

    app = web.Application()

    INLINE_HTML = """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><title>chatxz</title><style>body{background:#1a1a2e;color:#eee;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;flex-direction:column;gap:12px;text-align:center;padding:20px}h1{color:#e94560;font-size:24px}p{color:#aaa;font-size:14px}</style></head><body><h1>chatxz</h1><p id="msg">Starting...</p><pre id="diag" style="font-size:11px;color:#888;text-align:left;max-width:90vw;overflow:auto"></pre><script>fetch('/api/diag').then(r=>r.json()).then(d=>{document.getElementById('msg').textContent=d.msg||'Error';var pre=document.getElementById('diag');pre.textContent=JSON.stringify(d,null,2)}).catch(e=>{document.getElementById('diag').textContent=String(e)})</script></body></html>"""

    async def diag(request):
        info = {"msg": "Server running", "here": HERE, "static_tried": []}
        for sp in [STATIC, os.path.join(HERE, "static"), "/data/data/com.chatzx.android/files/python/chatxz/web/static"]:
            idx = os.path.join(sp, "index.html")
            info["static_tried"].append({"path": sp, "exists": os.path.isdir(sp), "index_exists": os.path.isfile(idx)})
        try:
            info["here_listing"] = os.listdir(HERE)[:20]
        except:
            info["here_listing"] = "unlistable"
        try:
            py_dir = os.path.dirname(HERE)
            info["py_dir"] = py_dir
            info["py_dir_listing"] = os.listdir(py_dir)[:20]
        except:
            pass
        try:
            info["cwd"] = os.getcwd()
            info["cwd_listing"] = os.listdir(".")[:20]
        except:
            pass
        try:
            import zipfile, sys
            for p in sys.path:
                if "base.apk" in p or "chatxz" in p:
                    try:
                        with zipfile.ZipFile(p) as z:
                            py_files = [n for n in z.namelist() if "chatxz" in n or "main.py" in n]
                            info["apk_python_files"] = py_files[:50]
                    except:
                        pass
        except:
            pass
        return web.json_response(info)

    async def index(request):
        for sp in [STATIC, os.path.join(HERE, "static"), "/data/data/com.chatzx.android/files/python/chatxz/web/static"]:
            idx_path = os.path.join(sp, "index.html")
            if os.path.isfile(idx_path):
                try:
                    with open(idx_path, "r", encoding="utf-8") as f:
                        html = f.read()
                    return web.Response(text=html, content_type="text/html")
                except Exception as e:
                    _log(f"index read error from {idx_path}: {e}")
        return web.Response(text=INLINE_HTML, content_type="text/html")

    async def static(request):
        fn = request.match_info.get("filename", "")
        if ".." in fn: raise web.HTTPNotFound()
        fp = os.path.join(STATIC, fn)
        if os.path.isfile(fp):
            with open(fp, "rb") as f:
                data = f.read()
            ct = "text/html" if fn.endswith(".html") else "text/javascript" if fn.endswith(".js") else "text/css" if fn.endswith(".css") else "application/octet-stream"
            return web.Response(body=data, content_type=ct)
        raise web.HTTPNotFound()

    async def temperature(request):
        try:
            from chatxz.utils.system import get_avg_cpu_temperature
            avg = await asyncio.to_thread(get_avg_cpu_temperature)
            return web.json_response({"avg_celsius": avg})
        except Exception:
            return web.json_response({"avg_celsius": None})

    async def cpu(request):
        try:
            from chatxz.utils.system import get_cpu_percent
            pct = await asyncio.to_thread(get_cpu_percent)
            if pct is not None:
                return web.json_response({"cpu_percent": pct})
            return web.json_response({"cpu_percent": None})
        except Exception as e:
            return web.json_response({"cpu_percent": None, "error": str(e)})

    async def health(request):
        return web.Response(text="ok")

    app.router.add_get("/", index)
    app.router.add_get("/static/{filename:.*}", static)
    app.router.add_get("/api/temperature", temperature)
    app.router.add_get("/api/cpu", cpu)
    app.router.add_get("/api/health", health)
    app.router.add_get("/api/diag", diag)

    # Stub endpoints so frontend JS doesn't break
    async def json_stub(request):
        return web.json_response({"hash": "", "connected": None, "contacts": [], "discovered": []})
    app.router.add_get("/api/identity", json_stub)
    async def settings_get(request):
        return web.json_response({"name": "", "history_retention": "never", "received_dir": ""})
    app.router.add_get("/api/settings", settings_get)
    async def settings_post(request):
        return web.json_response({"status": "ok", "settings": {}})
    app.router.add_post("/api/settings", settings_post)
    async def discover(request):
        return web.json_response({"peers": []})
    app.router.add_get("/api/discover", discover)
    async def history(request):
        return web.json_response([])
    app.router.add_get("/api/history", history)
    async def queue(request):
        return web.json_response({"count": 0, "items": []})
    app.router.add_get("/api/queue", queue)

    _log("Routes configured")

    async def run_server():
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, host, port, reuse_address=True)
        await site.start()
        _log(f"Server listening on {host}:{port}")
        # Keep event loop alive
        while True:
            await asyncio.sleep(3600)

    def server_thread():
        try:
            asyncio.run(run_server())
        except Exception as e:
            tb = traceback.format_exc()
            _log(f"Server thread error: {e}\n{tb}")
            SERVER_ERROR.append(f"{type(e).__name__}: {e}")
            SERVER_READY.set()

    t = threading.Thread(target=server_thread, daemon=True)
    t.start()
    _log("Server thread started")

    # Wait for server ready with port polling
    deadline = time.time() + 60
    while time.time() < deadline:
        if SERVER_ERROR:
            return "None", SERVER_ERROR[0]
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.5)
            s.connect((host, port))
            s.close()
            _log("Server ready (port open)")
            SERVER_READY.set()
            return host, str(port)
        except (socket.timeout, ConnectionRefusedError, OSError):
            pass
        time.sleep(0.3)

    return "None", "Server timeout (60s)"
