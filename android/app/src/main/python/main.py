import threading, time, traceback, sys, os

def start_server():
    host = "127.0.0.1"
    port = 8742
    try:
        from chatxz.web.server import ChatWebServer
        server = ChatWebServer(host=host, port=port, verbose=False)
        t = threading.Thread(target=server.run, daemon=True)
        t.start()
        time.sleep(2)
        return host, str(port)
    except Exception as e:
        tb = traceback.format_exc()
        return "None", f"{type(e).__name__}: {e}\n{tb}"
