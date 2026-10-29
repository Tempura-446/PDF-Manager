"""Progress reporting and cancelling for long jobs.

Code that loops over files, steps or pages reports where it is; when it runs
inside a job (the app's Run button), the job's progress bar moves and a
cancelled job stops at the next report. Outside a job, reporting costs
nothing, so the same functions serve the command line and the library.

    with progress.section(i, len(files), name):   # file i of n
        for n, page in enumerate(pages):
            progress.report(n, len(pages), "page")  # raises Cancelled if cancelled
"""

import contextvars
import itertools
import threading
import time
from contextlib import contextmanager


class Cancelled(Exception):
    """The job was cancelled."""


class Job:
    _ids = itertools.count(1)

    def __init__(self):
        self.id = f"{next(self._ids)}-{int(time.time() * 1000) % 10**8}"
        self.lock = threading.Lock()
        self.state = "running"   # running, done, failed, cancelled
        self.fraction = 0.0
        self.message = ""
        self.cancelled = False
        self.result = None       # what the job returned
        self.error = None
        self.finished_at = None
        self.thread = None       # set by start()

    def cancel(self):
        self.cancelled = True

    def snapshot(self):
        with self.lock:
            return {"state": self.state, "fraction": round(self.fraction, 4),
                    "message": self.message, "error": self.error}

    # Exceptions whose message is meant for the user as it is; others are
    # reported with their type (they are bugs or unexpected conditions).
    expected = ()

    def run(self, func, *args, **kwargs):
        """Run ``func`` as this job (in the calling thread)."""
        token = _job.set(self)
        try:
            result = func(*args, **kwargs)
            with self.lock:
                self.result, self.state, self.fraction = result, "done", 1.0
        except Cancelled:
            with self.lock:
                self.state = "cancelled"
        except Exception as exc:  # reported to the page
            message = str(exc) if isinstance(exc, self.expected) else f"{type(exc).__name__}: {exc}"
            with self.lock:
                self.state, self.error = "failed", message
        finally:
            self.finished_at = time.monotonic()
            _job.reset(token)

    def start(self, func, *args, **kwargs):
        """Run ``func`` as this job in a background thread."""
        context = contextvars.copy_context()
        self.thread = threading.Thread(target=context.run, args=(self.run, func, *args),
                                       kwargs=kwargs, daemon=True)
        self.thread.start()
        return self


_job = contextvars.ContextVar("pdfm_job", default=None)
# The current part of the job: (start, end) of the bar and a label.
_part = contextvars.ContextVar("pdfm_part", default=(0.0, 1.0, ""))


def current():
    return _job.get()


def check():
    job = _job.get()
    if job is not None and job.cancelled:
        raise Cancelled()


@contextmanager
def section(index, count, label=""):
    """Part ``index`` (0-based) of ``count`` equal parts of the current part,
    e.g. one file of several, or one step of a recipe."""
    start, end, outer = _part.get()
    width = (end - start) / max(count, 1)
    begin = start + width * index
    full = " — ".join(x for x in (outer, label) if x)
    token = _part.set((begin, begin + width, full))
    report(message=full, fraction_in_part=0.0)
    try:
        yield
    finally:
        _part.reset(token)


def report(done=None, total=None, what=None, message=None, fraction_in_part=None):
    """Where the current part is: ``done`` of ``total`` (e.g. pages), with
    ``what`` naming them ("page"). Raises Cancelled if the job was cancelled."""
    job = _job.get()
    if job is None:
        return
    if job.cancelled:
        raise Cancelled()
    start, end, label = _part.get()
    if fraction_in_part is None and total:
        fraction_in_part = min(1.0, max(0.0, (done or 0) / total))
    if message is None and what and total:
        message = " — ".join(x for x in (label, f"{what} {min((done or 0) + 1, total)} of {total}") if x)
    with job.lock:
        if fraction_in_part is not None:
            job.fraction = max(job.fraction, start + (end - start) * fraction_in_part)
        if message is not None:
            job.message = message


def say(text):
    """Show ``text`` (after the current part's label) as the job's message."""
    label = _part.get()[2]
    report(message=" — ".join(x for x in (label, text) if x))


class JobStore:
    """The jobs of one server, forgotten a while after they finish."""

    def __init__(self, keep_seconds=600):
        self.jobs = {}
        self.keep = keep_seconds
        self.lock = threading.Lock()

    def _prune(self):
        # Called with the lock held. A finished job nobody collected (its
        # page was closed) holds its whole result, so it goes after a while
        # whether or not another job comes along.
        now = time.monotonic()
        for key in [k for k, j in self.jobs.items()
                    if j.finished_at and now - j.finished_at > self.keep]:
            del self.jobs[key]

    def add(self, job):
        with self.lock:
            self._prune()
            self.jobs[job.id] = job
        return job

    def get(self, job_id):
        with self.lock:
            self._prune()
            return self.jobs.get(job_id)

    def pop(self, job_id):
        with self.lock:
            return self.jobs.pop(job_id, None)

    def stop(self, timeout=3.0):
        """Cancel the jobs still running and wait up to ``timeout`` seconds
        in all for them to stop (the app is closing)."""
        with self.lock:
            running = [j for j in self.jobs.values() if j.state == "running"]
        deadline = time.monotonic() + timeout
        for job in running:
            job.cancel()
        for job in running:
            if job.thread is not None:
                job.thread.join(max(0.0, deadline - time.monotonic()))
