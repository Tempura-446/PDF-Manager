# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Explorer's right-click actions: Combine, Make searchable (OCR), Make
smaller, and Make a PDF from pictures (see winsetup.register_context_menu).

Explorer starts one copy of the verb's command per selected file, all at
once. Each copy hands its file to the running app (gui.forward_to_running_app;
the first one starts it), and the app gathers the files of one action that
arrive close together into a batch: the batch closes once no file has come
for ``QUIET`` seconds, and then runs as one job. Its results open in the app
as new, unsaved tabs.

Explorer gives the files in no reliable order (the copies race each other),
so a batch is sorted by name, numbers counted as numbers ("Scan 2" before
"Scan 10"), as Explorer lists a folder by default.
"""

import itertools
import re
import threading
import time
from pathlib import Path

ACTIONS = {
    # together: one result from all the files; else one result per file.
    "combine": {"op": "merge", "label": "Combine", "kind": "pdf", "together": True, "min": 2},
    "ocr": {"op": "ocr", "label": "Make searchable (OCR)", "kind": "pdf", "together": False, "min": 1},
    "compress": {"op": "compress", "label": "Make smaller", "kind": "pdf", "together": False, "min": 1},
    "images": {"op": "from-images", "label": "Make a PDF", "kind": "image", "together": True, "min": 1},
}
IMAGE_TYPES = (".jpg", ".jpeg", ".png", ".tif", ".tiff")
QUIET = 2.0          # seconds without a new file before a batch closes
FIRST_WAIT = 3.0     # a batch stays open at least this long (the other copies are still starting)
LONGEST_WAIT = 30.0  # and closes after this long whatever arrives
KEEP = 600           # seconds a finished batch (and its results) is kept for the page


def natural_key(path):
    """Sort key: folder, then name with numbers as numbers, ignoring case."""
    path = Path(path)
    parts = re.split(r"(\d+)", path.name.lower())
    return (str(path.parent).lower(), [int(p) if p.isdigit() else p for p in parts])


def refuse_reason(action, path):
    """Why ``path`` can't go into ``action`` (None when it can)."""
    path = Path(path)
    kind = ACTIONS[action]["kind"]
    suffix = path.suffix.lower()
    if not path.is_file():
        return "it's no longer there"
    if kind == "pdf" and suffix != ".pdf":
        return "it isn't a PDF"
    if kind == "image":
        if suffix in (".heic", ".heif"):
            return "HEIC photos can't be read yet: save them as JPG first (in Photos: … > Save as)"
        if suffix not in IMAGE_TYPES:
            return "only JPG, PNG and TIFF pictures can be made into a PDF"
    return None


class Batch:
    _ids = itertools.count(1)

    def __init__(self, action):
        self.id = f"a{next(self._ids)}-{int(time.time() * 1000) % 10**8}"
        self.action = action
        self.paths = []
        self.created = self.last = time.monotonic()
        self.state = "collecting"   # collecting, then running (its job says how it went)
        self.job = None             # progress.Job once it runs
        self.outputs = {}           # n -> (name, bytes) until the page fetches them
        self.finished_at = None

    def add(self, paths):
        for path in paths:
            path = Path(path)
            if path not in self.paths:
                self.paths.append(path)
        self.last = time.monotonic()

    def due(self, now):
        return (now - self.last >= QUIET and now - self.created >= FIRST_WAIT) \
            or now - self.created >= LONGEST_WAIT

    def snapshot(self):
        spec = ACTIONS[self.action]
        state = self.job.state if self.job is not None else self.state
        return {"id": self.id, "action": self.action, "label": spec["label"], "state": state,
                "files": [p.name for p in sorted(self.paths, key=natural_key)],
                "job": self.job.id if self.job is not None else None}


class Batches:
    """The right-click batches of one running app. ``start(batch)`` runs a
    batch once it has closed (the server makes it a job)."""

    def __init__(self, start, quiet_check=0.2):
        self.start = start
        self.items = {}
        self.lock = threading.Lock()
        self.check = quiet_check

    def add(self, action, paths):
        """Put ``paths`` into the open batch of ``action``, or a new one.
        Returns (batch, new)."""
        if action not in ACTIONS:
            raise ValueError(f"unknown action {action!r}")
        with self.lock:
            self._prune()
            batch = next((b for b in self.items.values()
                          if b.action == action and b.state == "collecting"), None)
            new = batch is None
            if new:
                batch = Batch(action)
                self.items[batch.id] = batch
            batch.add(paths)
        if new:
            threading.Thread(target=self._close_when_quiet, args=(batch,), daemon=True).start()
        return batch, new

    def _close_when_quiet(self, batch):
        while True:
            time.sleep(self.check)
            with self.lock:
                if batch.state != "collecting":
                    return
                if batch.due(time.monotonic()):
                    batch.state = "running"
                    break
        self.start(batch)

    def close(self):
        """The app is closing: batches still gathering files never run."""
        with self.lock:
            for batch in self.items.values():
                if batch.state == "collecting":
                    batch.state = "closed"

    def get(self, batch_id):
        with self.lock:
            return self.items.get(batch_id)

    def _prune(self):
        now = time.monotonic()
        for key in [k for k, b in self.items.items() if b.finished_at and now - b.finished_at > KEEP]:
            del self.items[key]
