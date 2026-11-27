# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Scanner, watch-folder, and settings HTTP route handlers."""

import io
import json
import sys
import time
import urllib.parse
from http import HTTPStatus

from pdfmanager import applog, privacy, progress, update
from pdfmanager.server.common import (
    UserError, _bool, _num, _parse_multipart, gui_hook, lazy_module,
)

background = lazy_module("pdfmanager.background")
ocr = lazy_module("pdfmanager.ocr")
recipes = lazy_module("pdfmanager.recipes")
scanner = lazy_module("pdfmanager.scanner")
scanpdf = lazy_module("pdfmanager.scanpdf")
watchfolders = lazy_module("pdfmanager.watchfolders")


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
        if not isinstance(wanted, list) or not wanted or not all(isinstance(p, dict) for p in wanted):
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

    def _filing_rules_get(self):
        from pdfmanager import filing

        return self._send(HTTPStatus.OK, {
            "rules": filing.load_app_rules(),
            "starters": filing.STARTER_RULES,
        })

    def _filing_rules_save(self, body):
        from pdfmanager import filing

        data = json.loads(body or b"{}")
        rules = data.get("rules") if isinstance(data, dict) else data
        filing.save_app_rules(rules)
        return self._send(HTTPStatus.OK, {"rules": filing.load_app_rules()})

    def _filing_preview(self, body):
        import tempfile
        from pathlib import Path
        from pdfmanager import filing

        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("choose a sample PDF to preview filing rules")
        raw_rules = fields.get("rules")
        rules = json.loads(raw_rules) if raw_rules else filing.load_app_rules()
        from pdfmanager.server.common import _safe_name
        sample_name = _safe_name(uploads[0][1] or "sample.pdf")
        if not sample_name.lower().endswith(".pdf"):
            sample_name += ".pdf"
        with tempfile.TemporaryDirectory(prefix="pdfm-filing-") as tmp:
            sample = Path(tmp) / sample_name
            sample.write_bytes(uploads[0][2])
            plan = filing.plan_filing([sample], rules, base_dir=Path(tmp), password=fields.get("password") or None)
            item = plan[0]
            rel_dst = str(Path(item["dst"]).relative_to(Path(tmp).resolve())) if item["matched"] else sample_name
        return self._send(HTTPStatus.OK, {
            "matched": item["matched"],
            "rule": item["rule"],
            "reason": item["reason"],
            "target": rel_dst.replace("\\", "/"),
            "variables": item["variables"],
        })

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
        if "translate_engine" in data:
            eng = str(data["translate_engine"] or "offline").strip().lower()
            if eng not in ("offline", "llm"):
                raise UserError(f"unknown translation engine {eng!r}")
            changes["translate_engine"] = None if eng == "offline" else eng
        if "translate_api_url" in data:
            url = str(data["translate_api_url"] or "").strip().rstrip("/")
            if url:
                parsed = urllib.parse.urlsplit(url)
                host = (parsed.hostname or "").lower()
                is_local = host in ("localhost", "127.0.0.1", "::1")
                if parsed.scheme not in ("https", "http") or (parsed.scheme == "http" and not is_local):
                    raise UserError("translation API URL must use https:// (or http:// for localhost)")
            changes["translate_api_url"] = url or None
        if "translate_model" in data:
            changes["translate_model"] = str(data["translate_model"] or "").strip() or None
        if data.get("translate_api_key"):
            changes["translate_api_key"] = str(data["translate_api_key"]).strip()
        if data.get("clear_translate_key"):
            changes["translate_api_key"] = ""
        if "translate_glossary" in data:
            gloss = str(data["translate_glossary"] or "").strip()[:16384]
            changes["translate_glossary"] = gloss or None
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

    def _i18n_get(self):
        from pdfmanager import i18n

        lang = i18n.get_language()
        effective = i18n.DEFAULT_LANG if lang == "auto" else lang
        return self._send(HTTPStatus.OK, {
            "setting": lang,
            "lang": effective,
            "dir": i18n.language_dir(effective),
            "strings": i18n.load_catalog(effective),
            "packs": i18n.list_installed_packs(),
            "languages": i18n.list_available_languages(),
        })

    def _i18n_set_language(self, body):
        from pdfmanager import i18n

        data = json.loads(body or b"{}")
        i18n.set_language(data.get("language") or i18n.DEFAULT_LANG)
        return self._i18n_get()

    def _i18n_pack_install(self, body):
        import tempfile
        from pathlib import Path
        from pdfmanager import i18n

        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("choose a language pack .zip file to install")
        with tempfile.TemporaryDirectory(prefix="pdfm-lang-") as tmp:
            pack_zip = Path(tmp) / "pack.zip"
            pack_zip.write_bytes(uploads[0][2])
            installed = i18n.install_pack_from_zip(pack_zip, expected_sha256=fields.get("sha256") or None)
        return self._send(HTTPStatus.OK, {"installed": installed, "packs": i18n.list_installed_packs()})

    def _i18n_pack_remove(self, body):
        from pdfmanager import i18n

        data = json.loads(body or b"{}")
        res = i18n.remove_pack(data.get("code") or "")
        return self._send(HTTPStatus.OK, {**res, "packs": i18n.list_installed_packs(), "lang": i18n.get_language()})

    def _i18n_catalog(self):
        from pdfmanager import i18n

        return self._send(HTTPStatus.OK, i18n.list_catalog_packs(fetch=True))

    def _i18n_pack_download(self, body):
        from pdfmanager import i18n

        data = json.loads(body or b"{}")
        code = str(data.get("code") or "").strip()
        pair = str(data.get("pair") or "").strip() or None
        source = str(data.get("source") or "").strip() or None
        if not code and not pair:
            raise UserError("specify a language code or translation pair to download")

        def download_job():
            res = i18n.download_and_install_pack(code or pair.split("-")[0], pair=pair, source=source)
            return {"json": {
                **res,
                "packs": i18n.list_installed_packs(),
                "languages": i18n.list_available_languages(),
            }}

        return self._job_or_now(download_job)

    def _translate_test(self, body):
        from pdfmanager import translate

        data = json.loads(body or b"{}")
        cfg = update.settings()
        saved_url = str(cfg.get("translate_api_url") or "http://localhost:11434/v1").strip()
        req_url = str(data.get("translate_api_url") or "").strip()
        api_url = req_url or saved_url
        model = str(data.get("translate_model") or cfg.get("translate_model") or "qwen2.5:7b").strip()
        req_key = str(data.get("translate_api_key") or "").strip()
        # Only fall back to the stored key when testing the saved endpoint (prevents key exfiltration).
        if req_key:
            api_key = req_key
        elif not req_url or req_url.rstrip("/") == saved_url.rstrip("/"):
            api_key = str(cfg.get("translate_api_key") or "").strip()
        else:
            api_key = ""
        t0 = time.monotonic()
        fn = translate._load_llm_translator("en", "es", api_url=api_url, model=model, api_key=api_key)
        sample = fn(["Hello world"])[0]
        ms = int((time.monotonic() - t0) * 1000)
        return self._send(HTTPStatus.OK, {"ok": True, "model": model, "sample": sample, "ms": ms})

    def _translate_unload(self):
        result = {"unloaded": 0, "freed_bytes": 0}
        mod = sys.modules.get("pdfmanager.translate")
        if mod is not None:
            result = dict(mod.unload_cached_models())
        mai = sys.modules.get("pdfmanager.translate.manga_ai")
        if mai is not None and hasattr(mai, "reset"):
            mai.reset()
        ocr_mod = sys.modules.get("pdfmanager.ocr.models")
        if ocr_mod is not None and hasattr(ocr_mod, "reset"):
            ocr_mod.reset()
        return self._send(HTTPStatus.OK, result)


