#!/usr/bin/env python3
from setuptools import setup, find_packages

setup(
    name="chatxz",
    version="0.3.121",
    description="Decentralized chat over Reticulum Network Stack",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[
        "rns>=0.8.0",
    ],
    extras_require={
        "voice": ["pyaudio"],
        "tui": ["textual>=0.52.0"],
        "full": ["pyaudio", "textual>=0.52.0"],
    },
    entry_points={
        "console_scripts": [
            "chatxz=chatxz.app:main",
        ],
    },
)
