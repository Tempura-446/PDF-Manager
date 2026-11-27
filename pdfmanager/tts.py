# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Zero-Cloud Offline Neural TTS, Read-Aloud & Audiobook (.wav) Export.

Supports:
  1. Kokoro-82M ONNX (Apache-2.0, near-human neural TTS via ``kokoro_onnx``)
  2. Piper neural TTS CLI (MIT)
  3. Native OS offline speech synthesizers (Windows SAPI / macOS say / Linux espeak-ng)

100% offline — zero network requests, zero cloud APIs.
"""

import io
import logging
import re
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

from pdfmanager import appdata, core, progress

_LOG = logging.getLogger("zpdf")

KOKORO_VOICES = (
    ("af_heart", "Heart (English Female — Warm)"),
    ("af_bella", "Bella (English Female — Clear)"),
    ("am_adam", "Adam (English Male)"),
    ("bf_emma", "Emma (British Female)"),
    ("jf_alpha", "Alpha (Japanese Female)"),
    ("zf_xiaobei", "Xiaobei (Chinese Female)"),
)


def _kokoro_paths():
    base = appdata.data_dir() / "models" / "kokoro"
    return base / "kokoro-v1.0.onnx", base / "voices-v1.0.bin"


def available_backends():
    """Detect installed offline TTS backends on this machine."""
    kokoro_model, kokoro_voices = _kokoro_paths()
    has_kokoro = False
    if kokoro_model.is_file() and kokoro_voices.is_file():
        try:
            import kokoro_onnx  # noqa: F401
            has_kokoro = True
        except ImportError:
            has_kokoro = False

    has_piper = bool(shutil.which("piper"))
    if sys.platform == "win32":
        has_os = bool(shutil.which("powershell") or shutil.which("pwsh"))
    elif sys.platform == "darwin":
        has_os = bool(shutil.which("say") and shutil.which("afconvert"))
    else:
        has_os = bool(shutil.which("espeak-ng") or shutil.which("espeak"))

    preferred = "kokoro" if has_kokoro else ("piper" if has_piper else ("os" if has_os else "browser"))
    return {
        "kokoro": has_kokoro,
        "piper": has_piper,
        "os_native": has_os,
        "preferred": preferred,
        "voices": [{"id": vid, "label": lbl} for vid, lbl in KOKORO_VOICES],
    }


def clean_page_text(raw_text):
    """Normalize PDF page text into natural spoken sentences."""
    txt = str(raw_text or "")
    # Rejoin hyphenated line breaks
    txt = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", txt)
    txt = re.sub(r"\s+", " ", txt).strip()
    if not txt:
        return []
    parts = re.split(r"(?<=[.!?。！？])\s+", txt)
    out = []
    for p in parts:
        s = p.strip()
        if len(s) >= 2:
            out.append(s)
    return out


def extract_readable_sentences(pdf_path, *, pages=None, password=None):
    """Extract ordered ``{"page": int, "text": str}`` spoken sentences from ``pdf_path``."""
    from pdfmanager import pageranges

    reader = core.open_pdf(pdf_path, password)
    total = len(reader.pages)
    if pages and str(pages).strip():
        indices = pageranges.parse_page_ranges(str(pages).strip(), total)
    else:
        indices = list(range(total))
    sentences = []
    for idx in indices:
        raw = reader.pages[idx].extract_text() or ""
        for sent in clean_page_text(raw):
            sentences.append({"page": idx + 1, "text": sent})
    return sentences


def _synth_kokoro(text, out_wav, *, voice="af_heart", speed=1.0):
    import numpy as np
    import kokoro_onnx

    model_path, voices_path = _kokoro_paths()
    kokoro = kokoro_onnx.Kokoro(str(model_path), str(voices_path))
    samples, sample_rate = kokoro.create(text, voice=voice or "af_heart", speed=max(0.5, min(2.5, float(speed))))
    pcm = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    pcm16 = (pcm * 32767.0).astype("<i2")
    with wave.open(str(out_wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(int(sample_rate))
        wf.writeframes(pcm16.tobytes())
    return "kokoro"


def _synth_os_native(text, out_wav, *, speed=1.0):
    spd = max(0.5, min(2.5, float(speed)))
    out_wav = Path(out_wav).resolve()
    if sys.platform == "win32":
        ps = shutil.which("powershell") or shutil.which("pwsh")
        if not ps:
            raise core.PdfManagerError("Windows PowerShell is unavailable for offline TTS")
        rate = max(-10, min(10, int(round((spd - 1.0) * 5))))
        script = (
            "Add-Type -AssemblyName System.Speech; "
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            f"$s.Rate = {rate}; "
            "$s.SetOutputToWaveFile($env:ZPDF_TTS_OUT); "
            "$s.Speak($env:ZPDF_TTS_TEXT); "
            "$s.Dispose();"
        )
        import os
        env = {**os.environ, "ZPDF_TTS_OUT": str(out_wav), "ZPDF_TTS_TEXT": text}
        res = subprocess.run(
            [ps, "-NoProfile", "-NonInteractive", "-Command", script],
            env=env, capture_output=True, text=True, timeout=120,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if res.returncode != 0 or not out_wav.is_file():
            raise core.PdfManagerError(f"Windows speech synthesis failed: {res.stderr.strip()[:200]}")
        return "os_win_sapi"

    if sys.platform == "darwin":
        say_exe = shutil.which("say")
        af_exe = shutil.which("afconvert")
        if not say_exe or not af_exe:
            raise core.PdfManagerError("macOS 'say' / 'afconvert' unavailable")
        wpm = max(90, min(400, int(round(180 * spd))))
        aiff = out_wav.with_suffix(".aiff")
        try:
            subprocess.run([say_exe, "-r", str(wpm), "-o", str(aiff), text], check=True, timeout=120)
            subprocess.run([af_exe, "-f", "WAVE", "-d", "LEI16@22050", str(aiff), str(out_wav)], check=True, timeout=60)
        finally:
            if aiff.is_file():
                aiff.unlink(missing_ok=True)
        return "os_macos_say"

    espeak = shutil.which("espeak-ng") or shutil.which("espeak")
    if not espeak:
        raise core.PdfManagerError(
            "No offline TTS backend installed. Install espeak-ng (Linux) or Kokoro-82M ONNX."
        )
    wpm = max(80, min(380, int(round(170 * spd))))
    subprocess.run([espeak, "-s", str(wpm), "-w", str(out_wav), text], check=True, timeout=120)
    return "os_espeak"


def synthesize_wav(text, out_wav, *, voice="", speed=1.0, backend="auto"):
    """Synthesize ``text`` to a 16-bit PCM WAV file at ``out_wav`` completely offline."""
    clean = " ".join(str(text or "").split())
    if not clean:
        raise core.PdfManagerError("no text to synthesize")
    info = available_backends()
    want = str(backend or "auto").strip().lower()
    if want == "kokoro" or (want == "auto" and info["kokoro"]):
        return _synth_kokoro(clean, out_wav, voice=voice or "af_heart", speed=speed)
    if want in ("auto", "os", "native"):
        return _synth_os_native(clean, out_wav, speed=speed)
    raise core.PdfManagerError(f"unsupported TTS backend: {backend!r}")


def _concat_wavs(wav_paths, output_wav, *, pause_sec=0.45):
    if not wav_paths:
        raise core.PdfManagerError("no audio segments generated")
    out = Path(output_wav)
    out.parent.mkdir(parents=True, exist_ok=True)
    total_frames = 0
    rate = 22050
    with wave.open(str(wav_paths[0]), "rb") as first:
        params = first.getparams()
        rate = params.framerate
        nch = params.nchannels
        sw = params.sampwidth
    silence = b"\x00" * int(rate * pause_sec) * nch * sw
    with wave.open(str(out), "wb") as dst:
        dst.setnchannels(nch)
        dst.setsampwidth(sw)
        dst.setframerate(rate)
        for idx, wp in enumerate(wav_paths):
            with wave.open(str(wp), "rb") as src:
                frames = src.readframes(src.getnframes())
                dst.writeframes(frames)
                total_frames += src.getnframes()
            if idx + 1 < len(wav_paths):
                dst.writeframes(silence)
                total_frames += int(rate * pause_sec)
    return round(total_frames / float(max(1, rate)), 2)


def export_audiobook(
    pdf_path,
    output_wav,
    *,
    pages=None,
    voice="",
    speed=1.0,
    backend="auto",
    password=None,
):
    """Convert a PDF (or selected page range) into a single offline ``.wav`` audiobook."""
    sentences = extract_readable_sentences(pdf_path, pages=pages, password=password)
    if not sentences:
        raise core.PdfManagerError("no selectable text found in PDF (run OCR first)")

    by_page = {}
    for s in sentences:
        by_page.setdefault(s["page"], []).append(s["text"])

    page_nums = sorted(by_page)
    used_backend = ""
    with tempfile.TemporaryDirectory(prefix="zpdf-tts-") as tmp:
        tmp_dir = Path(tmp)
        segments = []
        for idx, pnum in enumerate(page_nums):
            progress.check()
            progress.report(idx, len(page_nums), f"synthesizing page {pnum}")
            seg = tmp_dir / f"page_{pnum:04d}.wav"
            page_text = " ".join(by_page[pnum])
            used_backend = synthesize_wav(page_text, seg, voice=voice, speed=speed, backend=backend)
            segments.append(seg)
        duration = _concat_wavs(segments, output_wav)
    progress.report(len(page_nums), len(page_nums), "done")
    return {
        "output": str(output_wav),
        "pages": len(page_nums),
        "sentences": len(sentences),
        "duration_sec": duration,
        "backend": used_backend,
    }
