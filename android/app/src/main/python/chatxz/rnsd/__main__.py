"""Entry point: python -m chatxz.rnsd

Headless Reticulum transport daemon. Exposes no HTTP server — only a local
TCP IPC socket for the Rust ``chatxz`` application (default port 8744).
"""

from chatxz.rnsd.service import main

if __name__ == "__main__":
    main()