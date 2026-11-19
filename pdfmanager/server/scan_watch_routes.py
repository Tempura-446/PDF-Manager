# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Scanner, watch-folder, and settings HTTP route handlers."""

import io
import json
import sys
import time
import urllib.parse
from http import HTTPStatus

from pdfmanager import applog, background, ocr, privacy, progress, recipes, scanner, scanpdf, update, watchfolders
from pdfmanager.server.common import UserError, _bool, _num, _parse_multipart, gui_hook


def _shell_menu_available():
    """The right-click actions run the installed app's Python: Windows, and
    a copy installed with the installer (or pip)."""
    platform = getattr(gui_hook("sys", sys), "platform", sys.platform)
    return platform == "win32" and update.how_installed() == "pip"


def _settings_for_page(mode=None):
    """Settings as the page shows them, with what's remembered and the
    occasions ZPDF goes online."""
    return {**update.public_settings(), "remember": privacy.remembering(), "history": privacy.history(),
            "online": privacy.ONLINE, "background": background.state(mode)}


class ScanWatchRoutesMixin:
    """Scanner discovery/capture, watch folder, and settings endpoints."""

    def _scan_query(self):
        return {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).items()}

    def _scan_get(self, what):
        """GET /api/scan/status, /devices, /pages?box=, /thumb?box=&page=&look=."""
        query = self._scan_query()
        if what == "status":
            # (A scanner can always be tried: network ones need nothing from Windows.)
            wia = scanner.status()
            from pdfmanager import naps2

            return self._send(HTTPStatus.OK, {"scanner": {"available": True, "reason": "", "wia": wia},
                                              "naps2": naps2.installed(),
                                              "ocr": scanpdf.ocr_engine(),
                                              "upright": scanpdf.can_turn_upright(),
                                              "modes": list(scanner.MODES),
                                              "resolutions": list(scanner.RESOLUTIONS)})
        if what == "devices":
            return self._send(HTTPStatus.OK, {"devices": scanner.all_scanners()})
        box = self.scans.get(query.get("box"), create=False)
        if what == "pages":
            return self._send(HTTPStatus.OK, {"box": box.id, "pages": box.list()})
        if what == "thumb":
            look = query.get("look") or "document"
            if look not in scanpdf.LOOKS:
                raise UserError("unknown look")
            return self._send(HTTPStatus.OK, box.thumbnail(query.get("page", ""), look), "image/jpeg")
        return self._error(HTTPStatus.NOT_FOUND, "not found")

    def _scan_post(self, what, body):
        """POST /api/scan/box (a new set of pages), /acquire (from the
        scanner), /photos (phone photos), /make (the PDF), /close."""
        if what == "photos":
            return self._scan_photos(body)
        data = json.loads(body or b"{}")
        if what == "box":
            box = self.scans.get(None)
            return self._send(HTTPStatus.OK, {"box": box.id, "pages": []})
        if what == "close":
            self.scans.drop(str(data.get("box", "")))
            return self._send(HTTPStatus.OK, {"ok": True})
        if what in ("add-network", "forget-network"):   # a network scanner by its address (escl.py)
            from pdfmanager import escl

            if what == "forget-network":
                escl.forget(str(data.get("device") or ""))
                return self._send(HTTPStatus.OK, {"ok": True})
            device = escl.add(data.get("address"))
            return self._send(HTTPStatus.OK, {"device": {k: device[k] for k in
                                                         ("id", "name", "feeder", "flatbed", "duplex", "network", "found")}})
        box = self.scans.get(str(data.get("box", "")), create=False)
        if what == "acquire":
            mode = data.get("mode") or scanner.DEFAULT_MODE
            dpi = _num(data.get("dpi"), int, scanner.DEFAULT_DPI)
            source = data.get("source") or "flatbed"
            device = str(data.get("device") or "")
            if not device:
                raise UserError("choose a scanner first")

            def acquire_job():
                added = []
                scanner.scan_any(device, mode=mode, dpi=dpi, source=source, duplex=_bool(data.get("duplex")),
                                 on_page=lambda image, d: added.append(box.add(image, d)))
                return {"json": {"box": box.id, "pages": added}}

            return self._job_or_now(acquire_job)
        if what == "make":
            return self._scan_make(box, data)
        return self._error(HTTPStatus.NOT_FOUND, "not found")

    def _scan_photos(self, body):
        """Phone photos: each page found, flattened and kept in the box."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        box = self.scans.get(fields.get("box"), create=False)
        straighten = _bool(fields.get("straighten", True))
        photos = [(name, bytes(data)) for field, name, data in uploads if field == "files"]
        if not photos:
            raise UserError("choose one or more photos")

        def photos_job():
            from PIL import Image, UnidentifiedImageError

            added, skipped = [], []
            for n, (name, data) in enumerate(photos):
                progress.report(n, len(photos), "photo")
                try:
                    with Image.open(io.BytesIO(data)) as im:
                        im.load()
                        page, found = scanpdf.photo_page(im, straighten)
                except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
                    skipped.append(name)
                    continue
                added.append({**box.add(page.image, None, photo=True, found=found.found), "name": name})
            return {"json": {"box": box.id, "pages": added, "skipped": skipped}}

        return self._job_or_now(photos_job)

    def _scan_make(self, box, data):
        """The PDF from the box's pages, in the order and turned as asked."""
        wanted = data.get("pages") or []
        if not isinstance(wanted, list) or not wanted:
            raise UserError("there are no pages yet: scan or add some first")
        pages = [box.page(str(p.get("id", "")), _num(p.get("turn"), int, 0)) for p in wanted]
        options = {"straighten": _bool(data.get("straighten", True)),
                   "remove_blank": _bool(data.get("remove_blank", True)),
                   "upright": _bool(data.get("upright", True)),
                   "recognise": _bool(data.get("ocr", True)),
                   "look": data.get("look") or "document", "paper": data.get("paper") or "auto"}
        keep = self.headers.get("X-Pdfm-Keep") == "1"

        def make_job():
            work = self.work.make()
            notes, started, outcome = [], gui_hook("time", time).monotonic(), "failed"
            try:
                name = time.strftime("Scan %Y-%m-%d %H.%M.pdf")
                out = scanpdf.make_pdf(pages, work / "scan.pdf", notes=notes, **options)
                outcome = "done"
                return {"file": (name, out.read_bytes(), "application/pdf", notes, keep)}
            except progress.Cancelled:
                outcome = "cancelled"
                raise
            finally:
                applog.tool_run("scan", [], gui_hook("time", time).monotonic() - started, outcome)
                self.work.remove(work)

        return self._job_or_now(make_job)

    def _watch_list(self):
        """Settings > Watch folders: the folders, what each is doing, and the choices."""
        _, last = self.watcher.news_after(10**9)
        return self._send(HTTPStatus.OK, {
            "folders": self.watcher.list(), "last": last,
            "tools": [{"id": k, "label": v} for k, v in watchfolders.TOOLS.items()],
            "recipes": list(recipes.load_all())})

    def _save_settings(self, body):
        data = json.loads(body or b"{}")
        changes = {}
        if "oda_converter" in data:
            changes["oda_converter"] = str(data["oda_converter"] or "").strip()
        if "repo" in data:
            repo = str(data["repo"] or "").strip().strip("/")
            if repo and repo not in (update.RELEASES, update.REPO):
                # Updates install whatever the repository publishes: only ZPDF's own.
                raise UserError(f"updates come from {update.RELEASES} (or {update.REPO} for development)")
            changes["update_repo"] = "" if repo == update.RELEASES else repo
        if data.get("token"):
            changes["github_token"] = str(data["token"]).strip()
        if data.get("clear_token"):
            changes["github_token"] = ""
        if "check" in data:
            changes["check_updates"] = None if data["check"] else False
        if "ocr_quality" in data:
            q = str(data["ocr_quality"] or "").strip()
            if q not in ocr.QUALITIES:
                raise UserError(f"unknown OCR accuracy {q!r}")
            changes["ocr_quality"] = None if q == "standard" else q
        if "ocr_gpu" in data:
            changes["ocr_gpu"] = True if data["ocr_gpu"] else None
        update.save_settings(**changes)
        if "remember" in data:
            privacy.set_remembering(bool(data["remember"]))  # (turning it off clears what's kept)
        if "background" in data or "login" in data:  # watching with the window closed (background.py)
            ok, why = background.supported(type(self).instance_mode)
            if not ok:
                raise UserError(why)
            if "background" in data:
                background.set_keep(str(data["background"]))
            if "login" in data:
                background.set_start_at_login(bool(data["login"]))
        return self._send(HTTPStatus.OK, _settings_for_page(type(self).instance_mode))
