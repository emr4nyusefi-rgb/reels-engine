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
DIRECTOR = ("Read the following transcript based on the director's note.\n\n# Director's note\n"
            "Style: relaxed, warm, casual spoken Persian, like a friend telling you something in a chat. "
            "Not formal, not a news announcer, not a lecture. Natural everyday conversational rhythm and "
            "everyday pronunciation of colloquial words, with clear articulation. "
            "Calm, unhurried pace, short natural pauses between sentences, "
            "and a friendly, relaxed ending.\n\n## Transcript:\n")


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, **kw)


def duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         check=True, capture_output=True, text=True).stdout.strip()
    return float(out)


def get_fonts():
    d = HERE / "fonts"
    d.mkdir(exist_ok=True)
    for name, url in FONTS.items():
        p = d / name
        if p.exists() and p.stat().st_size > 1000:
            continue
        try:
            urllib.request.urlretrieve(url, p)
        except Exception as e:  # fallback fonts will be used
            print("font download failed:", name, e)


def pcm_to_wav(pcm, rate, path):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(pcm)


def gemini_tts(text, voice, model, key, out_wav):
    body = {"contents": [{"parts": [{"text": DIRECTOR + text}]}],
            "generationConfig": {"responseModalities": ["AUDIO"],
                                 "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}}}
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", "x-goog-api-key": key})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            res = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError("HTTP %s: %s" % (e.code, e.read().decode("utf-8", "replace")[:300]))
    part = res["candidates"][0]["content"]["parts"][0]["inlineData"]
    rate = 24000
    for piece in part.get("mimeType", "").split(";"):
        if piece.strip().startswith("rate="):
            rate = int(piece.split("=")[1])
    pcm_to_wav(base64.b64decode(part["data"]), rate, out_wav)


def make_audio(text, out_wav, work):
    """Returns the name of the engine that produced the narration."""
    if os.environ.get("SKIP_TTS"):
        run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=24000", "-af", "volume=2", "-t",
             os.environ.get("SKIP_TTS_SECONDS", "17"), str(out_wav)])
        return "test-tone"
    key, voice, errors = os.environ.get("GEMINI_API_KEY"), VOICE, []
    raw = work / "raw.wav"
    if key:
        for model in GEMINI_TTS_MODELS:
            for attempt in range(4):
                try:
                    gemini_tts(text, voice, model, key, raw)
                    d = duration(raw)
                    if not 4 <= d <= 45:
                        raise RuntimeError(f"unexpected audio length {d:.1f}s")
                    shutil.move(raw, out_wav)
                    return "gemini:" + model + ":" + voice
                except Exception as e:
                    errors.append(f"{model}: {e}")
                    time.sleep(6 * (attempt + 1))
    # The chosen voice must always narrate the reel: if Gemini TTS keeps failing, fail the job instead of using another voice.
    raise RuntimeError("Gemini TTS (voice " + voice + ") failed after retries; no other voice is used. " + (" | ".join(errors) if errors else "GEMINI_API_KEY missing"))


def trim_and_normalize(src, dst):
    # two separate passes (chaining silenceremove + loudnorm can stall ffmpeg):
    # 1) trim leading silence, 2) normalise loudness
    tmp = pathlib.Path(str(dst) + ".trim.wav")
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-af",
         "silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.05", str(tmp)], timeout=120)
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(tmp), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
         "-ar", "44100", "-ac", "1", str(dst)], timeout=120)
    tmp.unlink(missing_ok=True)


def hook_cover_ms(timed):
    """Moment (ms) when the hook slide is fully revealed: used as Buffer's metadata.thumbnailOffset
    (custom cover images are not accepted by Buffer's API; the thumbnail is a frame of the video)."""
    h = timed[0]
    n = max(1, len((h.get("screen_text") or h["narration"]).split()))
    dur = h["end"] - h["start"]
    step = min(0.22, min(1.2, dur * 0.5) / n)
    done = 0.35 + n * step + 0.35          # last word revealed
    bar = done - 0.1 + 0.5                 # underline fully drawn
    t = min(bar + 0.15, dur - 0.3)
    return int(max(0.5, t) * 1000)


def build_timeline(scenes, dur, head=0.25, tail=0.8):
    total = head + dur + tail
    weights = [max(4 if s.get("type") == "outro" else 1, len(s["narration"].split())) for s in scenes]
    acc, out = 0.0, []
    for i, s in enumerate(scenes):
        share = weights[i] / sum(weights) * dur
        start = 0.0 if i == 0 else head + acc
        acc += share
        end = total if i == len(scenes) - 1 else head + acc
        out.append({**s, "start": round(start, 3), "end": round(end, 3)})
    return out, total


def render_frames(data, total, frames, cover_path):
    from playwright.sync_api import sync_playwright
    html = (HERE / "template.html").read_text(encoding="utf-8").replace("__DATA__", json.dumps(data, ensure_ascii=False))
    page_path = HERE / "_page.html"
    page_path.write_text(html, encoding="utf-8")
    n = int(round(total * FPS))
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": W, "height": H})
        pg.goto(page_path.as_uri())
        pg.evaluate("() => document.fonts.ready.then(() => true)")
        pg.evaluate("init()")
        for i in range(n):
            pg.evaluate(f"setTime({i / FPS:.4f})")
            pg.screenshot(path=str(frames / f"{i:05d}.jpg"), type="jpeg", quality=92)
        pg.evaluate("cover()")  # clean 1080x1920 thumbnail (hook slide fully revealed)
        pg.screenshot(path=str(cover_path), type="jpeg", quality=95)
        b.close()
    return n


OUTRO = "فردا می‌بینمت"
RA = re.compile(r"(?<![\u0600-\u06FF\u200c])را(?![\u0600-\u06FF\u200c])")


def spoken_fix(scene):
    """The object marker is always spoken and written as «رو», never «را»."""
    out = dict(scene)
    for k in ("narration", "screen_text"):
        if isinstance(out.get(k), str):
            out[k] = RA.sub("رو", out[k])
    if isinstance(out.get("steps"), list):
        out["steps"] = [RA.sub("رو", x) if isinstance(x, str) else x for x in out["steps"]]
    return out


def main():
    script_path, out_path = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
    script = json.loads(script_path.read_text(encoding="utf-8"))
    scenes = [spoken_fix(s) for s in script["scenes"]]
    for w in ("درست", "غلط", "صحیح", "اشتباه"):
        if any(w in (s.get("narration", "") + s.get("screen_text", "")) for s in scenes):
            print("WARNING: judging word found in script:", w)
    # extra final slide, outside the 4 main slides: says goodbye instead of asking to save
    scenes.append({"type": "outro", "narration": OUTRO, "screen_text": OUTRO})
    narration = " ".join(s["narration"] for s in scenes)
    work = HERE / "_work"
    shutil.rmtree(work, ignore_errors=True)
    (work / "frames").mkdir(parents=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    get_fonts()
    raw_wav, wav = work / "narration_raw.wav", work / "narration.wav"
    engine = make_audio(narration, raw_wav, work)
    trim_and_normalize(raw_wav, wav)
    dur = duration(wav)
    print(f"audio: {engine}, {dur:.1f}s")

    timed, total = build_timeline(scenes, dur)
    data = {"scenes": timed, "phrase": script.get("phrase", "")}
    n = render_frames(data, total, work / "frames", out_path.parent / "cover.jpg")
    print(f"frames: {n}, total {total:.1f}s")

    run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", str(work / "frames" / "%05d.jpg"),
         "-i", str(wav), "-af", "adelay=250:all=1,apad", "-t", f"{total:.3f}",
         "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(FPS),
         "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out_path)])
    meta = {"tts": engine, "seconds": round(total, 2), "frames": n, "caption": script.get("caption", ""),
            # publishing rules: never show these Reels on the profile grid; thumbnail = hook slide frame
            "share_to_feed": False, "thumbnail_offset_ms": hook_cover_ms(timed), "cover": "cover.jpg"}
    (out_path.parent / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    print("done:", out_path, meta)


if __name__ == "__main__":
    main()
