import threading, time, traceback, os, socket, glob, json, subprocess, asyncio

_diag = []
def _log(msg):
    _diag.append(str(msg)); print(msg)

SERVER_READY = threading.Event()
SERVER_ERROR = []

def start_server():
    host, port = "127.0.0.1", 8742

    try:
        import RNS
        _log("RNS OK")
    except Exception as e:
        return "None", f"RNS: {type(e).__name__}: {e}"
    import RNS

    try:
        from aiohttp import web
        _log("aiohttp OK")
    except Exception as e:
        return "None", f"aiohttp: {type(e).__name__}: {e}"

    HERE = os.path.dirname(os.path.abspath(__file__))
    STATIC = os.path.join(HERE, "chatxz", "web", "static")

    app = web.Application()

    INLINE_HTML = """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><title>chatxz</title><style>body{background:#1a1a2e;color:#eee;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;flex-direction:column;gap:12px;text-align:center;padding:20px}h1{color:#e94560;font-size:24px}p{color:#aaa;font-size:14px}</style></head><body><h1>chatxz</h1><p>Starting server...</p><p style="font-size:12px;color:#666">If this page persists, check python_crash_log.txt</p></body></html>"""

    async def index(request):
        idx_path = os.path.join(STATIC, "index.html")
        if os.path.isfile(idx_path):
            try:
                return web.FileResponse(idx_path)
            except Exception:
                pass
        return web.Response(text=INLINE_HTML, content_type="text/html")

    async def static(request):
        fn = request.match_info.get("filename", "")
        if ".." in fn: raise web.HTTPNotFound()
        fp = os.path.join(STATIC, fn)
        if os.path.isfile(fp): return web.FileResponse(fp)
        raise web.HTTPNotFound()

    async def temperature(request):
        temps = {}
        def rd(p):
            try:
                with open(p) as f: return int(f.read().strip()) / 1000.0
            except: return None
        for tz in glob.glob("/sys/class/thermal/thermal_zone*/temp"):
            c = rd(tz)
            if c:
                n = os.path.basename(os.path.dirname(tz)).replace("thermal_zone", "cpu")
                temps[n] = round(c, 1)
        if not temps:
            for hw in glob.glob("/sys/class/hwmon/hwmon*/temp*_input"):
                c = rd(hw)
                if c:
                    base = os.path.dirname(hw)
                    lbl = None
                    for sfx in ["label", "name"]:
                        lp = os.path.join(base, sfx)
                        if os.path.isfile(lp):
                            try:
                                with open(lp) as f: lbl = f.read().strip(); break
                            except: pass
                    temps[lbl or os.path.basename(base)] = round(c, 1)
        if not temps:
            try:
                r = subprocess.run(["sensors", "-j"], capture_output=True, text=True, timeout=3)
                if r.returncode == 0:
                    for chip, vals in json.loads(r.stdout).items():
                        for k, v in vals.items():
                            if isinstance(v, dict):
                                for sk, sv in v.items():
                                    if sk.endswith("_input") and isinstance(sv, (int, float)):
                                        temps[k.replace("_input", "")] = round(sv, 1)
            except: pass
        if not temps:
            try:
                r = subprocess.run(["acpi", "-t"], capture_output=True, text=True, timeout=3)
                for line in r.stdout.split("\n"):
                    if "thermal" in line.lower() and "," in line:
                        for p in line.split(","):
                            if "degrees" in p:
                                try: temps["acpi"] = round(float(p.replace("degrees", "").strip()), 1)
                                except: pass
            except: pass
        return web.json_response({"temperatures": temps})

    async def cpu(request):
        try:
            # Count CPUs for loadavg fallback
            nproc = 0
            try:
                with open("/proc/cpuinfo") as f:
                    nproc = sum(1 for l in f if l.startswith("processor"))
            except:
                pass
            if nproc == 0:
                try:
                    nproc = len(os.listdir("/sys/devices/system/cpu/"))
                except:
                    pass

            # Try /proc/stat with delta
            try:
                with open("/proc/stat") as f:
                    p = [int(x) for x in f.readline().split()[1:]]
                t1, i1 = sum(p), p[3]
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, lambda: time.sleep(0.3))
                with open("/proc/stat") as f:
                    p = [int(x) for x in f.readline().split()[1:]]
                t2, i2 = sum(p), p[3]
                td, id_ = t2 - t1, i2 - i1
                pct = round(100.0 * (1.0 - id_ / td), 1) if td > 0 else 0.0
                return web.json_response({"cpu_percent": pct})
            except (PermissionError, FileNotFoundError, IndexError, ValueError) as e:
                # Fallback: estimate from loadavg
                try:
                    with open("/proc/loadavg") as f:
                        la = float(f.read().split()[0])
                    if nproc > 0:
                        pct = min(round(la / nproc * 100, 1), 100.0)
                        return web.json_response({"cpu_percent": pct, "approx": True})
                except:
                    pass
                raise  # re-raise original if loadavg fallback also fails
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            return web.json_response({"cpu_percent": None, "error": str(e), "traceback": tb})

    app.router.add_get("/", index)
    app.router.add_get("/static/{filename:.*}", static)
    app.router.add_get("/api/temperature", temperature)
    app.router.add_get("/api/cpu", cpu)

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

    async def run_server():
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host, port)
        await site.start()
        _log(f"Server listening on {host}:{port}")
        SERVER_READY.set()
        await asyncio.Event().wait()

    def server_thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(run_server())
        except Exception as e:
            SERVER_ERROR.append(f"{type(e).__name__}: {e}")
            SERVER_READY.set()

    t = threading.Thread(target=server_thread, daemon=True)
    t.start()
    _log("Server thread started")

    if SERVER_READY.wait(timeout=60):
        if SERVER_ERROR:
            return "None", SERVER_ERROR[0]
        _log("Server is ready")
        return host, str(port)
    else:
        return "None", "Server timeout (60s)"
