#!/usr/bin/env python3
"""Render one Instagram Reel (1080x1920, 30fps) from a script JSON.

Usage: python render.py script.json out/reel.mp4
Env:   GEMINI_API_KEY (Gemini TTS), narrator voice fixed in VOICE=Charon; no fallback voice is ever used,
       SKIP_TTS=1 (quiet test tone, only for layout tests)
"""
import base64, json, os, pathlib, re, shutil, subprocess, sys, time, urllib.error, urllib.request, wave

W, H = 1080, 1920
FPS = int(os.environ.get("REEL_FPS", "30"))
HERE = pathlib.Path(__file__).resolve().parent
FONTS = {
    "Alibaba-Regular.ttf": "https://res.cloudinary.com/ezjkiau6/raw/upload/v1791370980/Alibaba-Regular.ttf",
    "Alibaba-Bold.ttf": "https://res.cloudinary.com/ezjkiau6/raw/upload/v1791370979/Alibaba-Bold.ttf",
    "Script.otf": "https://res.cloudinary.com/ezjkiau6/raw/upload/v1791371015/TchamiltonarcscriptRegular-yYXvm.otf",
}
GEMINI_TTS_MODELS = ["gemini-3.1-flash-tts-preview", "gemini-2.5-flash-preview-tts"]
VOICE = "Charon"  # fixed narrator voice (the user's choice); never replaced by another voice
