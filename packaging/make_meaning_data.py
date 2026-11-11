"""Build pdfmanager/static/meaning/words.npz, the data behind search by meaning.

It is made from two files of the WordLlama project (MIT licence,
https://github.com/dleemiller/WordLlama), version 0.4.0: the 256-number
vectors of its "l2_supercat" model and that model's vocabulary. ZPDF keeps
the first 128 numbers of each vector (the model is trained so that they
work alone), stored as one byte each with a scale per row, and the
vocabulary's pieces and joining rules.

    python packaging/make_meaning_data.py l2_supercat_256.safetensors l2_supercat_tokenizer_config.json
"""

import json
import struct
import sys
from pathlib import Path

import numpy as np

DIMS = 128
OUT = Path(__file__).resolve().parent.parent / "pdfmanager" / "static" / "meaning" / "words.npz"


def main(weights, tokenizer):
    with open(weights, "rb") as f:
        header = json.loads(f.read(struct.unpack("<Q", f.read(8))[0]))
        info = header["embedding.weight"]
        rows, width = info["shape"]
        assert info["dtype"] == "F16"
        full = np.frombuffer(f.read(), dtype=np.float16).reshape(rows, width)
    kept = full[:, :DIMS].astype(np.float32)
    scale = np.abs(kept).max(axis=1, keepdims=True)
    scale[scale == 0] = 1
    vectors = np.round(kept / scale * 127).astype(np.int8)

    model = json.loads(Path(tokenizer).read_text(encoding="utf-8"))["model"]
    pieces = [None] * rows
    for piece, number in model["vocab"].items():
        pieces[number] = piece
    assert all(p is not None for p in pieces)

    def packed(value):
        return np.frombuffer(json.dumps(value, ensure_ascii=False).encode("utf-8"), dtype=np.uint8)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(OUT, vectors=vectors, scale=scale[:, 0].astype(np.float16),
                        pieces=packed(pieces), joins=packed(model["merges"]))
    print(f"{OUT}: {OUT.stat().st_size / 2**20:.1f} MB, {rows} pieces, {len(model['merges'])} joins")


if __name__ == "__main__":
    main(*sys.argv[1:3])
