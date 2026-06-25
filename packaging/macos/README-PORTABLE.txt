chatxz — macOS portable
=======================

No installer. Open the app and your browser starts automatically.

Quick start
-----------
1. Open chatxz.app (double-click, or drag to Applications).
2. Allow the Terminal window to stay open — it runs the encrypted mesh server.
3. Your browser opens to http://127.0.0.1:8742
4. If macOS Firewall asks, allow incoming connections for chatxz on your LAN.

Stop chatxz
-----------
Press Ctrl+C in the Terminal window, or close the window.

Your data
---------
Everything stays beside the app:
  chatxz-data/     identity, settings, chat history, received files

LAN access (--share is always on for the portable app)
----------------------------------------------------
Other devices on your Wi‑Fi/LAN can open:
  http://<your-mac-ip>:8742
Find your IP: System Settings → Network, or run  ifconfig  in Terminal.

From source (Arch-style)
------------------------
If you cloned the repo instead of using the portable zip:

  ./run.sh web --share

Troubleshooting
---------------
- "Port already in use": quit any other chatxz window, or run with --force from source.
- Peers not found: click Announce in the web UI; ensure both devices share the same LAN.
- Gatekeeper blocked the app: right-click chatxz.app → Open (first launch only).

More help: https://github.com/narl3yyy-svg/chatxz
