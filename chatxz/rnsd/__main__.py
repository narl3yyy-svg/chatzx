"""Entry point: python -m chatxz.rnsd

Runs the Reticulum networking layer on an internal port (default 8743).
The Rust ``chatxz`` binary is the application; this process is transport only.
"""

from chatxz.rnsd.service import main

if __name__ == "__main__":
    main()