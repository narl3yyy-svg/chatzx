"""Compatibility shim — the application server is Rust. See chatxz.rnsd."""

from chatxz.rnsd.service import ChatRnsDaemon as ChatWebServer, main

__all__ = ["ChatWebServer", "main"]