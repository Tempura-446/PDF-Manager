# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Optional ONNX Manga AI Pack: neural bubble & borderless text detector
(RT-DETR v2), Baberu OCR v1.2 (115M multilingual ja/zh/en manga & comic
recognizer), LaMa-Manga neural inpainting, and page-context LLM manga script
translation.

All models are Apache-2.0 ONNX weights downloaded on demand into ZPDF's
per-user data folder with SHA-256 verification and live MB progress. Zero
PyTorch dependency — runs on the same ONNX Runtime already shipped by ZPDF.
"""

import hashlib
import json
import logging
import os
import re
import threading
import unicodedata
from pathlib import Path

from pdfmanager import core, progress

MANGA_AI_MODELS = {
    "detector": {
        "label": "manga bubble & text detector",
        "size_mb": 11,
        "files": [
            {
                "name": "detector-v4-s_int8.onnx",
                "url": "https://huggingface.co/ogkalu/comic-text-and-bubble-detector/resolve/main/detector-v4-s_int8.onnx",
                "sha256": "5fe9e4f576e49d4e7e8b0e029d6d3cdc252abd4694113e1cae120e62c931ea79",
            }
        ],
    },
    "ocr": {
        "label": "Baberu OCR v1.2 (ja/zh/en manga recognizer)",
        "size_mb": 122,
        "files": [
            {
                "name": "vision_int4.onnx",
                "url": "https://huggingface.co/genshiai-daichi/baberu-ocr/resolve/main/onnx/vision_int4.onnx",
                "sha256": "1967ecef502e4c1e1e7c512ea0a2e06396b39ded87a8a9cc77bd7db2e981a361",
            },
            {
                "name": "decoder_prefill_int8.onnx",
                "url": "https://huggingface.co/genshiai-daichi/baberu-ocr/resolve/main/onnx/decoder_prefill_int8.onnx",
                "sha256": "187a59cb61d3870f1f97c9eb027eb34ff19d023fba801cda113a13870be0b724",
            },
            {
                "name": "decoder_step_int8.onnx",
                "url": "https://huggingface.co/genshiai-daichi/baberu-ocr/resolve/main/onnx/decoder_step_int8.onnx",
                "sha256": "46640b0b3db77f0f0eef660bbe5dc9126a5e0fb49eb2786b2edba7b0371d9769",
            },
            {
                "name": "vocab.json",
                "url": "https://huggingface.co/genshiai-daichi/baberu-ocr/resolve/main/tokenizer/vocab.json",
                "sha256": "",  # plain UTF-8 JSON character table (verified non-empty)
            },
        ],
    },
    "inpaint": {
        "label": "LaMa-Manga neural inpainter",
        "size_mb": 60,
        "files": [
            {
                "name": "lama-manga_int8.onnx",
                "url": "https://huggingface.co/Liiesl/lama-manga-onnx-quant/resolve/main/lama-manga_int8.onnx",
                "sha256": "502ce98fbd8d030501040d4505daea0616003266f4eb82b1d27fd4fca5b55f4a",
            }
        ],
    },
}

_DETECTOR_LABELS = {0: "bubble", 1: "text_bubble", 2: "text_free"}
_SESSION_LOCK = threading.Lock()
_SESSIONS = {}


def manga_ai_dir():
    """Per-user folder where optional Manga AI ONNX models are stored."""
    from pdfmanager import appdata

    return appdata.data_dir() / "manga-ai"


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fetch_file(url, target, label=None):
    import urllib.request
    from pdfmanager import policy

    if not str(url or "").startswith("https://"):
        raise core.PdfManagerError(f"Manga AI model URL must use https:// (got {url!r})")
    try:
        policy.check_network_allowed("Manga AI model download")
    except policy.PolicyError as exc:
        raise core.PdfManagerError(str(exc)) from None

    req = urllib.request.Request(url, headers={"User-Agent": "ZPDF"})
    with urllib.request.urlopen(req, timeout=60) as resp, open(target, "wb") as out:
        raw_total = resp.headers.get("Content-Length") if resp.headers else None
        total = int(raw_total) if raw_total and str(raw_total).isdigit() else 0
        done = 0
        last_report = 0
        while True:
            progress.check()
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            out.write(chunk)
            done += len(chunk)
            if label and (done - last_report >= 512 * 1024 or (total and done >= total)):
                last_report = done
                done_mb = done / (1024 * 1024)
                if total > 0:
                    total_mb = total / (1024 * 1024)
                    pct = min(100, int(round(done * 100 / total)))
                    progress.say(f"getting {label} ({done_mb:.1f} / {total_mb:.0f} MB, {pct}%)")
                else:
                    progress.say(f"getting {label} ({done_mb:.1f} MB)")


def _component_paths(component):
    spec = MANGA_AI_MODELS[component]
    root = manga_ai_dir() / component
    return [root / f["name"] for f in spec["files"]]


def is_component_installed(component):
    """Whether all files for ``component`` ('detector', 'ocr', 'inpaint') exist."""
    if component not in MANGA_AI_MODELS:
        return False
    return all(p.is_file() and p.stat().st_size > 0 for p in _component_paths(component))


def manga_ai_status():
    """Return install status and metadata for every Manga AI Pack component."""
    out = {}
    for comp, spec in MANGA_AI_MODELS.items():
        out[comp] = {
            "installed": is_component_installed(comp),
            "size_mb": spec["size_mb"],
            "label": spec["label"],
        }
    return out


def ensure_manga_ai(components=("detector", "ocr", "inpaint"), *, download=False):
    """Return a dict mapping each requested component to its primary model Path
    (or directory for multi-file ``ocr``), downloading missing files when
    ``download=True`` with SHA-256 verification."""
    out = {}
    for comp in components:
        if comp not in MANGA_AI_MODELS:
            raise core.PdfManagerError(f"unknown Manga AI component {comp!r}")
        spec = MANGA_AI_MODELS[comp]
        root = manga_ai_dir() / comp
        paths = []
        for fspec in spec["files"]:
            target = root / fspec["name"]
            expected = (fspec.get("sha256") or "").lower()
            if target.is_file() and target.stat().st_size > 0:
                if not expected or _sha256(target) == expected:
                    paths.append(target)
                    continue
            if not download:
                out[comp] = None
                paths = None
                break
            root.mkdir(parents=True, exist_ok=True)
            part = target.with_name(target.name + ".part")
            label = f"{spec['label']} ({fspec['name']})"
            progress.say(f"getting {label} (once, ~{spec['size_mb']} MB)")
            try:
                _fetch_file(fspec["url"], part, label=label)
            except progress.Cancelled:
                part.unlink(missing_ok=True)
                raise
            except Exception as exc:
                part.unlink(missing_ok=True)
                raise core.PdfManagerError(
                    f"couldn't download {label} ({exc}); connect to the internet once or disable Enhanced Manga AI"
                ) from None
            if expected and _sha256(part) != expected:
                part.unlink(missing_ok=True)
                raise core.PdfManagerError(f"the {label} came down damaged; try again")
            if part.stat().st_size == 0:
                part.unlink(missing_ok=True)
                raise core.PdfManagerError(f"the {label} download was empty; try again")
            os.replace(part, target)
            paths.append(target)
        if paths is not None:
            out[comp] = root if len(paths) > 1 else paths[0]
    return out


def _ort_session(model_path):
    """Create an ONNX Runtime InferenceSession with bounded CPU threads."""
    import onnxruntime as ort

    opts = ort.SessionOptions()
    opts.inter_op_num_threads = 1
    cores = os.cpu_count() or 2
    opts.intra_op_num_threads = max(1, min(4, cores // 2 if cores > 2 else cores))
    avail = ort.get_available_providers() or ()
    providers = []
    if "CUDAExecutionProvider" in avail:
        providers.append("CUDAExecutionProvider")
    providers.append("CPUExecutionProvider")
    return ort.InferenceSession(str(model_path), sess_options=opts, providers=providers)


def _get_detector_session(model_path=None):
    path = Path(model_path) if model_path else ensure_manga_ai(("detector",), download=False).get("detector")
    if path is None or not path.is_file():
        return None
    key = ("detector", str(path))
    with _SESSION_LOCK:
        if key not in _SESSIONS:
            _SESSIONS[key] = _ort_session(path)
        return _SESSIONS[key]


def _get_ocr_sessions(model_dir=None):
    mdir = Path(model_dir) if model_dir else ensure_manga_ai(("ocr",), download=False).get("ocr")
    if mdir is None or not mdir.is_dir():
        return None
    # Preferred: Baberu OCR v1.2 (DINOv2 + 6-layer GQA KV-cache decoder, ja/zh/en)
    vis_p = mdir / "vision_int4.onnx"
    if not vis_p.is_file():
        vis_p = mdir / "vision_fp16.onnx"
    pre_p = mdir / "decoder_prefill_int8.onnx"
    stp_p = mdir / "decoder_step_int8.onnx"
    bvocab_p = mdir / "vocab.json"
    if vis_p.is_file() and pre_p.is_file() and stp_p.is_file() and bvocab_p.is_file():
        key = ("baberu_ocr", str(mdir), vis_p.name)
        with _SESSION_LOCK:
            if key not in _SESSIONS:
                _SESSIONS[key] = (
                    "baberu",
                    _ort_session(vis_p),
                    _ort_session(pre_p),
                    _ort_session(stp_p),
                    _BaberuVocab(bvocab_p),
                )
            return _SESSIONS[key]

    # Legacy fallback: manga-ocr ViT encoder/decoder if already cached locally
    enc_p = mdir / "encoder_model.onnx"
    dec_p = mdir / "decoder_model.onnx"
    voc_p = mdir / "vocab.txt"
    if not (enc_p.is_file() and dec_p.is_file() and voc_p.is_file()):
        return None
    key = ("ocr", str(mdir))
    with _SESSION_LOCK:
        if key not in _SESSIONS:
            _SESSIONS[key] = (_ort_session(enc_p), _ort_session(dec_p), _load_vocab(voc_p))
        return _SESSIONS[key]


def _get_inpaint_session(model_path=None):
    path = Path(model_path) if model_path else ensure_manga_ai(("inpaint",), download=False).get("inpaint")
    if path is None or not path.is_file():
        return None
    key = ("inpaint", str(path))
    with _SESSION_LOCK:
        if key not in _SESSIONS:
            _SESSIONS[key] = _ort_session(path)
        return _SESSIONS[key]


# ---------------------------------------------------------------------------
# 1. Neural Bubble & Borderless Text Detector (RT-DETR v2 ONNX)
# ---------------------------------------------------------------------------

def _box_iou(a, b):
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
    if inter <= 0:
        return 0.0
    area_a = max(1, (a[2] - a[0]) * (a[3] - a[1]))
    area_b = max(1, (b[2] - b[0]) * (b[3] - b[1]))
    return inter / max(1.0, area_a + area_b - inter)


def _sample_region_colors(rgb_arr, box):
    """Estimate background color, text color, and inverted polarity for ``box``."""
    import numpy as np

    h, w = rgb_arr.shape[:2]
    x0, y0, x1, y1 = max(0, box[0]), max(0, box[1]), min(w, box[2]), min(h, box[3])
    patch = rgb_arr[y0:y1, x0:x1]
    if patch.size == 0:
        return (1.0, 1.0, 1.0), (0.0, 0.0, 0.0), False
    # Sample outer 3px border ring to estimate paper/bubble fill
    ring = np.concatenate([
        patch[:3, :, :].reshape(-1, 3),
        patch[-3:, :, :].reshape(-1, 3),
        patch[:, :3, :].reshape(-1, 3),
        patch[:, -3:, :].reshape(-1, 3),
    ], axis=0)
    med = np.median(ring, axis=0)
    luma = 0.299 * med[0] + 0.587 * med[1] + 0.114 * med[2]
    inverted = bool(luma < 115.0)
    bg = (float(med[0]) / 255.0, float(med[1]) / 255.0, float(med[2]) / 255.0)
    fg = (0.98, 0.98, 0.98) if inverted else (0.05, 0.05, 0.05)
    return bg, fg, inverted


def detect_bubbles_neural(pil_img, *, reading_dir="rtl", score_threshold=0.35, model_path=None):
    """Run the RT-DETR v2 ONNX comic-text-and-bubble-detector on ``pil_img``.
    Returns a list of bubble dicts compatible with ``manga.detect_speech_bubbles()``
    (or ``None`` if the detector model is not installed)."""
    import numpy as np
    from PIL import Image

    sess = _get_detector_session(model_path=model_path)
    if sess is None:
        return None

    rgb = pil_img.convert("RGB")
    orig_w, orig_h = rgb.size
    rgb_arr = np.asarray(rgb, dtype=np.uint8)
    resized = rgb.resize((640, 640), Image.BILINEAR)
    arr = np.asarray(resized, dtype=np.float32) / 255.0
    inp = np.transpose(arr, (2, 0, 1))[None, ...]  # (1, 3, 640, 640)

    inputs = sess.get_inputs()
    feeds = {inputs[0].name: inp}
    if len(inputs) >= 2:
        # RT-DETR orig_target_sizes expects [batch, 2] int64 (height, width)
        feeds[inputs[1].name] = np.array([[orig_h, orig_w]], dtype=np.int64)

    outputs = sess.run(None, feeds)
    out_names = [o.name.lower() for o in sess.get_outputs()]

    raw_dets = []  # (score, label_id, x0, y0, x1, y1)
    if len(outputs) >= 3 and ("label" in out_names[0] or outputs[0].dtype in (np.int64, np.int32)):
        labels = np.asarray(outputs[0]).reshape(-1)
        boxes = np.asarray(outputs[1]).reshape(-1, 4)
        scores = np.asarray(outputs[2]).reshape(-1)
        for lbl, box, sc in zip(labels, boxes, scores):
            if float(sc) < score_threshold:
                continue
            bx0, by0, bx1, by1 = [float(v) for v in box]
            # Handle normalized [0..1] boxes if exported without orig_target_sizes
            if max(bx0, by0, bx1, by1) <= 1.5:
                bx0, bx1 = bx0 * orig_w, bx1 * orig_w
                by0, by1 = by0 * orig_h, by1 * orig_h
            raw_dets.append((float(sc), int(lbl), bx0, by0, bx1, by1))
    elif len(outputs) >= 2:
        # HuggingFace RTDetrV2 export: (logits [1, N, C], pred_boxes [1, N, 4] in cx,cy,w,h normalized)
        logits = np.asarray(outputs[0])[0]
        pred_boxes = np.asarray(outputs[1])[0]
        probs = 1.0 / (1.0 + np.exp(-np.clip(logits, -30.0, 30.0)))
        best_cls = np.argmax(probs, axis=-1)
        best_sc = probs[np.arange(len(probs)), best_cls]
        for lbl, box, sc in zip(best_cls, pred_boxes, best_sc):
            if float(sc) < score_threshold:
                continue
            cx, cy, bw, bh = [float(v) for v in box]
            bx0 = (cx - bw / 2.0) * orig_w
            by0 = (cy - bh / 2.0) * orig_h
            bx1 = (cx + bw / 2.0) * orig_w
            by1 = (cy + bh / 2.0) * orig_h
            raw_dets.append((float(sc), int(lbl), bx0, by0, bx1, by1))

    # Prefer enclosed 'bubble' (0) and 'text_free' (2); keep 'text_bubble' (1) only if
    # not already covered by a parent 'bubble' box.
    raw_dets.sort(key=lambda d: (0 if d[1] in (0, 2) else 1, -d[0]))
    kept = []
    for sc, lbl, bx0, by0, bx1, by1 in raw_dets:
        x0 = max(0, min(orig_w - 1, int(round(bx0))))
        y0 = max(0, min(orig_h - 1, int(round(by0))))
        x1 = max(x0 + 1, min(orig_w, int(round(bx1))))
        y1 = max(y0 + 1, min(orig_h, int(round(by1))))
        if (x1 - x0) < 16 or (y1 - y0) < 16:
            continue
        box = (x0, y0, x1, y1)
        if any(_box_iou(box, k["box"]) > 0.45 for k in kept):
            continue
        kind = _DETECTOR_LABELS.get(lbl, "bubble")
        bg_color, text_color, inverted = _sample_region_colors(rgb_arr, box)
        shape = "rect" if kind == "text_free" else "oval"
        bw, bh = x1 - x0, y1 - y0
        bubble = {
            "x0": x0,
            "y0": y0,
            "x1": x1,
            "y1": y1,
            "w": bw,
            "h": bh,
            "cx": (x0 + x1) / 2.0,
            "cy": (y0 + y1) / 2.0,
            "box": box,
            "shape": shape,
            "bg_rgb": bg_color,
            "text_rgb": text_color,
            "bg_color": bg_color,
            "text_color": text_color,
            "inverted": inverted,
            "mask": None,
            "kind": kind,
            "score": sc,
        }
        bubble["needs_inpaint"] = needs_inpainting(rgb, bubble)
        kept.append(bubble)

    from pdfmanager.translate.manga import order_manga_bubbles

    return order_manga_bubbles(kept, reading_dir=reading_dir)


# ---------------------------------------------------------------------------
# 2. Multilingual Manga OCR (Baberu OCR v1.2 ONNX + legacy manga-ocr fallback)
# ---------------------------------------------------------------------------

_BABERU_PAST_NAMES = tuple([f"past_k{i}" for i in range(6)] + [f"past_v{i}" for i in range(6)])


class _BaberuVocab:
    """Character-level vocabulary for Baberu OCR (ids 0..3 = pad/bos/eos/unk;
    id >= 4 -> charset[id - 4])."""

    def __init__(self, vocab_json):
        charset = json.loads(Path(vocab_json).read_text(encoding="utf-8"))
        self.id2ch = {0: "", 1: "", 2: "", 3: ""}
        for i, ch in enumerate(charset):
            self.id2ch[i + 4] = ch
        self.bos = 1
        self.eos = 2
        self.content_ids = {
            i + 4
            for i, ch in enumerate(charset)
            if len(ch) == 1 and ch not in "ーｰ〜~" and unicodedata.category(ch)[0] in "LN"
        }

    def decode(self, ids):
        return "".join(self.id2ch.get(i, "") for i in ids if i >= 4).strip()


def _load_vocab(vocab_path):
    return [line.rstrip("\r\n") for line in Path(vocab_path).read_text(encoding="utf-8").splitlines()]


def _decode_manga_tokens(token_ids, vocab):
    pieces = []
    for tid in token_ids:
        if tid < 5 or tid >= len(vocab):
            continue
        tok = vocab[tid]
        if tok.startswith("[") and tok.endswith("]"):
            continue
        if tok.startswith("##"):
            tok = tok[2:]
        pieces.append(tok)
    text = "".join(pieces)
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", "", text).strip()


def _ocr_baberu_crop(
    pil_crop,
    vis_sess,
    pre_sess,
    stp_sess,
    vocab,
    *,
    max_length=256,
    repetition_penalty=1.2,
    max_content_run=12,
    max_symbol_run=16,
):
    """Run Baberu OCR v1.2 three-graph ONNX inference (DINOv2 vision ->
    decoder prefill -> KV-cached decoder step) matching upstream onnx_infer.py."""
    import numpy as np
    from PIL import Image

    mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
    img = pil_crop.convert("RGB").resize((224, 224), Image.BICUBIC)
    arr = (np.asarray(img, dtype=np.float32) / 255.0 - mean) / std
    pixel_values = arr.transpose(2, 0, 1)[None, ...]  # (1, 3, 224, 224)

    vis_embeds = vis_sess.run(["vision_embeds"], {"pixel_values": pixel_values})[0]
    out = pre_sess.run(
        None,
        {"vision_embeds": vis_embeds, "input_ids": np.array([[vocab.bos]], dtype=np.int64)},
    )
    logits = np.array(out[0][0, -1], dtype=np.float64, copy=True)
    present = out[1:]
    seq = [vocab.bos]
    toks = []
    pos = int(vis_embeds.shape[1]) + 1

    for _ in range(max_length):
        progress.check()
        if repetition_penalty != 1.0:
            for tid in set(seq):
                s = logits[tid]
                logits[tid] = s * repetition_penalty if s < 0 else s / repetition_penalty
        last = toks[-1] if toks else 0
        cap = max_content_run if last in vocab.content_ids else (max_symbol_run if last > 3 else 0)
        if cap:
            run = 0
            for t in reversed(toks):
                if t == last:
                    run += 1
                else:
                    break
            if run >= cap:
                logits[last] = -np.inf
        nxt = int(np.argmax(logits))
        if nxt == vocab.eos:
            break
        toks.append(nxt)
        seq.append(nxt)
        if len(toks) >= max_length:
            break
        feed = {
            "input_ids": np.array([[nxt]], dtype=np.int64),
            "position_ids": np.array([[pos]], dtype=np.int64),
        }
        for name, tensor in zip(_BABERU_PAST_NAMES, present):
            feed[name] = tensor
        out = stp_sess.run(None, feed)
        logits = np.array(out[0][0, -1], dtype=np.float64, copy=True)
        present = out[1:]
        pos += 1

    return vocab.decode(toks)


def ocr_manga_crop(pil_crop, *, model_dir=None, max_length=256):
    """Recognise manga/comic text (Japanese, Chinese, or English) from a cropped
    bubble image using Baberu OCR v1.2 ONNX (or legacy ``manga-ocr`` if cached).
    Returns ``None`` if no manga OCR model is installed."""
    import numpy as np
    from PIL import Image

    loaded = _get_ocr_sessions(model_dir=model_dir)
    if loaded is None:
        return None
    if isinstance(loaded, tuple) and len(loaded) == 5 and loaded[0] == "baberu":
        _, vis_sess, pre_sess, stp_sess, bvocab = loaded
        return _ocr_baberu_crop(pil_crop, vis_sess, pre_sess, stp_sess, bvocab, max_length=max_length)

    enc_sess, dec_sess, vocab = loaded

    # Legacy manga-ocr ViT preprocessing: grayscale -> RGB -> 224x224 -> [-1, 1]
    img = pil_crop.convert("L").convert("RGB").resize((224, 224), Image.BILINEAR)
    arr = (np.asarray(img, dtype=np.float32) / 255.0 - 0.5) / 0.5
    pixel_values = np.transpose(arr, (2, 0, 1))[None, ...]  # (1, 3, 224, 224)

    enc_in = enc_sess.get_inputs()[0].name
    enc_hidden = enc_sess.run(None, {enc_in: pixel_values})[0]

    dec_inputs = {i.name: i for i in dec_sess.get_inputs()}
    ids_name = "input_ids" if "input_ids" in dec_inputs else dec_sess.get_inputs()[0].name
    hid_name = "encoder_hidden_states" if "encoder_hidden_states" in dec_inputs else dec_sess.get_inputs()[1].name

    token_ids = [2]  # [CLS] decoder_start_token_id
    eos_id = 3       # [SEP]
    for _ in range(min(max_length, 128)):
        progress.check()
        feeds = {
            ids_name: np.array([token_ids], dtype=np.int64),
            hid_name: enc_hidden,
        }
        if "attention_mask" in dec_inputs:
            feeds["attention_mask"] = np.ones((1, len(token_ids)), dtype=np.int64)
        logits = dec_sess.run(None, feeds)[0]
        next_logits = logits[0, -1]
        next_id = int(np.argmax(next_logits))
        if next_id == eos_id:
            break
        # Simple 3-gram repeat guard against degenerate loops
        if len(token_ids) >= 6 and token_ids[-3:] == token_ids[-6:-3]:
            break
        token_ids.append(next_id)
    return _decode_manga_tokens(token_ids, vocab)


# ---------------------------------------------------------------------------
# 3. Neural Manga Inpainting (LaMa-Manga INT8 ONNX)
# ---------------------------------------------------------------------------

def needs_inpainting(pil_img, bubble):
    """Return True when ``bubble`` sits over non-uniform background (borderless
    ``text_free``, screentones, gradients, or artwork) where flat solid fill
    would look patchy; False for clean flat white/black speech bubbles."""
    import numpy as np

    if bubble.get("kind") == "text_free":
        return True
    x0, y0, x1, y1 = bubble["box"]
    rgb = pil_img.convert("RGB")
    w, h = rgb.size
    # Sample a thin 4px ring just inside/around the box edges
    ox0, oy0 = max(0, x0 - 2), max(0, y0 - 2)
    ox1, oy1 = min(w, x1 + 2), min(h, y1 + 2)
    patch = np.asarray(rgb.crop((ox0, oy0, ox1, oy1)), dtype=np.float32)
    if patch.size == 0:
        return False
    ring = np.concatenate([
        patch[:4, :, :].reshape(-1, 3),
        patch[-4:, :, :].reshape(-1, 3),
        patch[:, :4, :].reshape(-1, 3),
        patch[:, -4:, :].reshape(-1, 3),
    ], axis=0)
    gray_ring = 0.299 * ring[:, 0] + 0.587 * ring[:, 1] + 0.114 * ring[:, 2]
    # Flat white/black bubble interior edges have std < 22; screentones/art are much higher
    return float(np.std(gray_ring)) > 28.0


def inpaint_manga_region(pil_img, box, *, mask=None, model_path=None):
    """Erase text inside ``box`` using LaMa-Manga ONNX (512x512 4-channel input)
    with surrounding context padding. Returns a cleaned RGB PIL image sized to
    ``box`` (or ``None`` if the inpainting model is not installed)."""
    import numpy as np
    from PIL import Image

    sess = _get_inpaint_session(model_path=model_path)
    if sess is None:
        return None

    rgb = pil_img.convert("RGB")
    img_w, img_h = rgb.size
    x0, y0, x1, y1 = [int(round(v)) for v in box]
    bw, bh = max(1, x1 - x0), max(1, y1 - y0)

    # Context window around the box so LaMa sees surrounding screentones/line art
    pad = max(32, min(96, max(bw, bh) // 3))
    cx0, cy0 = max(0, x0 - pad), max(0, y0 - pad)
    cx1, cy1 = min(img_w, x1 + pad), min(img_h, y1 + pad)
    cw, ch = max(1, cx1 - cx0), max(1, cy1 - cy0)

    ctx_img = rgb.crop((cx0, cy0, cx1, cy1))
    ctx_mask = Image.new("L", (cw, ch), 0)
    if mask is not None:
        m = mask.convert("L").resize((bw, bh), Image.NEAREST)
        ctx_mask.paste(m, (x0 - cx0, y0 - cy0))
    else:
        fill_rect = Image.new("L", (bw, bh), 255)
        ctx_mask.paste(fill_rect, (x0 - cx0, y0 - cy0))

    size = 512
    img_512 = np.asarray(ctx_img.resize((size, size), Image.LANCZOS), dtype=np.float32) / 255.0
    mask_512 = (np.asarray(ctx_mask.resize((size, size), Image.NEAREST), dtype=np.float32) > 127.0).astype(np.float32)
    masked = img_512 * (1.0 - mask_512[..., None])
    inp = np.transpose(np.concatenate([masked, mask_512[..., None]], axis=2), (2, 0, 1))[None, ...].astype(np.float32)

    iname = sess.get_inputs()[0].name
    out = sess.run(None, {iname: inp})[0]
    if out.ndim == 4:
        out = out[0]
    if out.shape[0] == 3:
        out = np.transpose(out, (1, 2, 0))
    if float(np.max(out)) <= 1.5:
        out = out * 255.0
    out_u8 = np.clip(out, 0, 255).astype(np.uint8)
    restored_ctx = Image.fromarray(out_u8, "RGB").resize((cw, ch), Image.LANCZOS)
    return restored_ctx.crop((x0 - cx0, y0 - cy0, x0 - cx0 + bw, y0 - cy0 + bh))


# ---------------------------------------------------------------------------
# 4. Page-Context LLM Manga Script Translation
# ---------------------------------------------------------------------------

def translate_manga_pages(page_texts_list, src_lang, dst_lang, *, translator=None, glossary=None):
    """Translate manga dialogue page-by-page. When ``translator`` is an LLM
    callable (``_llm = True``), sends all ordered bubbles on each page together
    as one script batch (with 2-bubble trailing context from the previous page
    when supported) so pronouns, tone, and speaker continuity stay consistent.
    Falls back cleanly to ``translate_texts`` for offline neural MT."""
    from pdfmanager.translate.text import translate_texts

    if not page_texts_list:
        return []
    if translator is None or not getattr(translator, "_llm", False):
        flat = [t for page in page_texts_list for t in page]
        if not flat:
            return [[] for _ in page_texts_list]
        translated_flat = translate_texts(flat, src_lang, dst_lang, translator=translator, glossary=glossary)
        out, pos = [], 0
        for page in page_texts_list:
            n = len(page)
            out.append(translated_flat[pos : pos + n])
            pos += n
        return out

    results = []
    prev_tail = []
    for page_texts in page_texts_list:
        progress.check()
        if not page_texts:
            results.append([])
            continue
        if prev_tail and getattr(translator, "_supports_context", False):
            translated_page = translator(list(page_texts), context=prev_tail[-2:])
        else:
            translated_page = translate_texts(
                list(page_texts),
                src_lang,
                dst_lang,
                translator=translator,
                batch_size=max(len(page_texts), 16),
                glossary=glossary,
            )
        results.append(list(translated_page))
        prev_tail = list(translated_page[-2:])
    return results
