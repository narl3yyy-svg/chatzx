import threading, time, traceback, os, socket, glob, json, subprocess

_diag = []
def _log(msg):
    _diag.append(str(msg)); print(msg)

def start_server():
    host, port = "127.0.0.1", 8742

    try:
        import RNS
        _log("RNS OK")
    except Exception as e:
        return "None", f"RNS: {type(e).__name__}: {e}"

    try:
        from aiohttp import web
        _log("aiohttp OK")
    except Exception as e:
        return "None", f"aiohttp: {type(e).__name__}: {e}"

    try:
        HERE = os.path.dirname(os.path.abspath(__file__))
        STATIC = os.path.join(HERE, "chatxz", "web", "static")

        app = web.Application()

        async def index(request):
            return web.FileResponse(os.path.join(STATIC, "index.html"))

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
                with open("/proc/stat") as f:
                    p = [int(x) for x in f.readline().split()[1:]]
                t1, i1 = sum(p), p[3]
                time.sleep(0.3)
                with open("/proc/stat") as f:
                    p = [int(x) for x in f.readline().split()[1:]]
                t2, i2 = sum(p), p[3]
                td, id_ = t2 - t1, i2 - i1
                pct = round(100.0 * (1.0 - id_ / td), 1) if td > 0 else 0.0
                return web.json_response({"cpu_percent": pct})
            except Exception as e:
                return web.json_response({"cpu_percent": None, "error": str(e)})

        app.router.add_get("/", index)
        app.router.add_get("/static/{filename:.*}", static)
        app.router.add_get("/api/temperature", temperature)
        app.router.add_get("/api/cpu", cpu)

        t = threading.Thread(target=lambda: web.run_app(app, host=host, port=port, print=lambda _: None), daemon=True)
        t.start()

        for i in range(30):
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(0.5); s.connect((host, port)); s.close()
                _log(f"Ready {host}:{port}")
                return host, str(port)
            except: time.sleep(0.5)

        return "None", "Server timeout (15s)"
    except Exception as e:
        return "None", f"Init: {type(e).__name__}: {e}\n{traceback.format_exc()}"
