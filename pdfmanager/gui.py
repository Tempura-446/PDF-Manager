# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""A small local web app: ``pdfm gui`` serves a page on 127.0.0.1 and shows
it in its own window (pywebview, or else Edge/Chrome in app mode) or in the
browser. Uses only the standard library.

Uploads are processed in a temporary folder and the result is sent back as
a download. Folder tools (organize, rename, duplicates) work on paths on
this computer. Every API call must carry the random token printed at
start-up, and only localhost Host headers are accepted, so other web pages
and other machines can't drive the server.
"""

import logging
import base64
import hashlib
import hmac
import importlib.util
import inspect
import io
import ipaddress
import json
import os
import re
import secrets
import shutil
import signal
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
import webbrowser
import zipfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path

from pdfmanager import (__version__, applog, filefacts, privacy,
                        progress, quickactions, update)
from pdfmanager.appdata import data_dir, write_private
from pdfmanager.pageranges import PageRangeError
from pdfmanager.server.common import (
    EXPECTED_ERRORS, MARKUPS_IN_HEADER, MAX_UPLOAD, STATIC_TYPES, _STATIC_CACHE,
    Events, HandlerBase, OpenFiles, RecentFiles, ResultCache, SavedOver,
    UnsavedWork, UserError, WorkFolders, _bool, _byte_range, _download_headers,
    _file_mentions, _file_stamp, _log, _num, _page_csp, _pages, _parse_multipart,
    _proof, _replace_file, _safe_name, _thread_stacks, lazy_module,
    reveal_in_file_manager,
)

# Heavy backend modules are loaded lazily on first use to keep startup fast.
accessibility = lazy_module("pdfmanager.accessibility")
archive = lazy_module("pdfmanager.archive")
background = lazy_module("pdfmanager.background")
certcrypt = lazy_module("pdfmanager.certcrypt")
certsign = lazy_module("pdfmanager.certsign")
comments = lazy_module("pdfmanager.comments")
compare = lazy_module("pdfmanager.compare")
core = lazy_module("pdfmanager.core")
filetools = lazy_module("pdfmanager.filetools")
formdetect = lazy_module("pdfmanager.formdetect")
forms = lazy_module("pdfmanager.forms")
layers = lazy_module("pdfmanager.layers")
markup = lazy_module("pdfmanager.markup")
ocr = lazy_module("pdfmanager.ocr")
office = lazy_module("pdfmanager.office")
organize = lazy_module("pdfmanager.organize")
outline = lazy_module("pdfmanager.outline")
pageimages = lazy_module("pdfmanager.pageimages")
pdfacheck = lazy_module("pdfmanager.pdfacheck")
protection = lazy_module("pdfmanager.protection")
recipes = lazy_module("pdfmanager.recipes")
redact = lazy_module("pdfmanager.redact")
risks = lazy_module("pdfmanager.risks")
scan = lazy_module("pdfmanager.scan")
scanner = lazy_module("pdfmanager.scanner")
scanpdf = lazy_module("pdfmanager.scanpdf")
search = lazy_module("pdfmanager.search")
textedit = lazy_module("pdfmanager.textedit")
viewports = lazy_module("pdfmanager.viewports")
watchfolders = lazy_module("pdfmanager.watchfolders")
from pdfmanager.server.form_routes import FormRoutesMixin
from pdfmanager.server.reader_routes import ReaderRoutesMixin
from pdfmanager.server.scan_watch_routes import (
    ScanWatchRoutesMixin, _settings_for_page, _shell_menu_available,
)
from pdfmanager.server.tool_routes import ToolRoutesMixin

# One run's uploads. Parsing a form upload takes several times its size in
# memory, so this stays well inside what a laptop has.

from pdfmanager.server.operations import (
    OPERATIONS, OPS, PAPER, PREVIEW_OPS, PREVIEW_PAGES, SUFFIX, THUMB_WIDTH,
    _check_pages, _clean_note, _damaged_errors, _f, _flatten_outputs, _form_op,
    _inspect_pdf, _mend_inputs, _needs_mending, _pages_list, _plainly,
    _preview_operation, _protect, _rebuildable, _redact_boxes, _redact_extra,
    _redact_request, _run_file_op, _run_files, _run_operation, _sign_cert_op,
    _start_action, _tidy, _to_excel, _was_protected, default_options,
    inspect_pdf, preview_operation, run_action, run_folder_operation,
    run_operation,
)

class Handler(ReaderRoutesMixin, FormRoutesMixin, ToolRoutesMixin, ScanWatchRoutesMixin, HandlerBase):
    # pywebview's JavaScript bridge needs eval(); only allowed in the desktop window.


    # -- helpers -----------------------------------------------------------






    # -- routes ------------------------------------------------------------

    def do_GET(self):
        if not self._host_ok():
            return self._error(HTTPStatus.FORBIDDEN, "bad host")
        try:
            return self._do_get()
        except (UserError, core.PdfManagerError) as exc:
            return self._error(HTTPStatus.BAD_REQUEST, str(exc))

    def _do_get(self):
        path = self.path.split("?", 1)[0]
        if path == "/":
            page = resources.files("pdfmanager").joinpath("gui.html").read_text("utf-8")
            return self._send(HTTPStatus.OK, page, "text/html; charset=utf-8",
                              {"Content-Security-Policy": _page_csp(self.allow_eval)})
        if path.startswith("/static/"):
            return self._static(path[len("/static/"):])
        if path == "/manifest.webmanifest":
            return self._static("manifest.webmanifest")
        if path == "/sw.js":
            return self._static("sw.js")
        if not self._token_ok():
            return self._error(HTTPStatus.FORBIDDEN, "missing or wrong token")
        if path == "/api/operations":
            return self._send(HTTPStatus.OK, {"operations": OPERATIONS, "version": __version__})
        if path == "/api/recipes":
            return self._send(HTTPStatus.OK, {"recipes": list(recipes.load_all().values()),
                                              "step_ops": list(recipes.STEP_OPS)})
        if path.startswith("/api/file/"):
            return self._open_file(path[len("/api/file/"):])
        if path.startswith("/api/job/"):
            return self._job(path, "GET")
        if path.startswith("/api/facts/"):
            target = self.opened.get(path[len("/api/facts/"):]) if self.opened is not None else None
            if target is None or not target.is_file():
                return self._error(HTTPStatus.NOT_FOUND, "that file is no longer available")
            found = filefacts.remembered(target)
            made_by = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("made_by", [None])[0]
            return self._send(HTTPStatus.OK, {**found, "suggestions": filefacts.suggestions(found, made_by)})
        if path == "/api/recent":
            return self._recent()
        if path == "/api/sessions":
            return self._sessions_list()
        if path == "/api/companion/info":
            port = self.server.server_address[1]
            lan_on = bool(getattr(self, "lan_enabled", False))
            host = self.headers.get("Host") or f"127.0.0.1:{port}"
            if lan_on and host.startswith(("127.0.0.1", "localhost")):
                lan_ip = _detect_lan_ip()
                if lan_ip:
                    host = f"{lan_ip}:{port}"
            return self._send(HTTPStatus.OK, {
                "lan_enabled": lan_on,
                "url": f"http://{host}/#token={self.token}",
                "version": __version__,
                "pwa_manifest": "/manifest.webmanifest",
            })
        if path.startswith("/api/scan/"):
            try:
                return self._scan_get(path[len("/api/scan/"):])
            except EXPECTED_ERRORS as exc:
                return self._error(HTTPStatus.BAD_REQUEST, str(exc))
        if path == "/api/events":
            return self._events()
        if path.startswith("/api/action/"):
            return self._action(path[len("/api/action/"):])
        if path == "/api/watch":
            return self._watch_list()
        if path == "/api/filing/rules":
            return self._filing_rules_get()
        if path == "/api/watch/news":
            query = urllib.parse.parse_qs(self.path.partition("?")[2])
            try:
                after = int(query.get("after", ["0"])[0])
            except ValueError:
                after = 0
            news, last = self.watcher.news_after(after)
            # A result can be opened in the reader from its toast.
            news = [{**item, "id": self.opened.add(item["result"])
                     if item["result"] and Path(item["result"]).is_file() else None} for item in news]
            return self._send(HTTPStatus.OK, {"news": news, "last": last})
        if path == "/api/i18n":
            return self._i18n_get()
        if path == "/api/i18n/catalog":
            return self._i18n_catalog()
        if path == "/api/shellmenu":
            from pdfmanager import winsetup

            available = _shell_menu_available()
            return self._send(HTTPStatus.OK, {"available": available,
                                              "on": available and winsetup.context_menu_registered()})
        if path == "/api/settings":
            return self._send(HTTPStatus.OK, _settings_for_page(type(self).instance_mode))
        if path == "/api/models/status":
            from pdfmanager import models_manager

            return self._send(HTTPStatus.OK, {"models": models_manager.list_models()})
        if path == "/api/library":
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("q", [""])[0]
            return self._library_list(query=q)
        if path == "/api/library/progress":
            fid = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("file_id", [""])[0]
            return self._library_progress_get(file_id=fid)
        if path == "/api/library/search":
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            return self._library_search(query=qs.get("q", [""])[0], limit=qs.get("limit", ["50"])[0])
        if path.startswith("/api/library/thumb/"):
            return self._library_thumb(path.split("/api/library/thumb/", 1)[1])
        if path == "/api/stamps":  # the stamps of your own (stamps.py)
            from pdfmanager import stamps

            return self._send(HTTPStatus.OK, {"stamps": stamps.saved(), "most": stamps.MOST})
        if path == "/api/update":
            return self._send(HTTPStatus.OK, {**update.check(), "can_restart": self._can_restart()})
        if path == "/api/diagnostics":
            return self._send(HTTPStatus.OK, applog.diagnostics(), "text/plain; charset=utf-8")
        if path == "/api/licence":
            from pdfmanager import notices

            return self._send(HTTPStatus.OK, notices.licence_text(), "text/plain; charset=utf-8")
        if path == "/api/notices":
            from pdfmanager import notices

            return self._send(HTTPStatus.OK, notices.third_party_notices(), "text/plain; charset=utf-8")
        if path == "/api/audit":
            from pdfmanager import auditlog

            return self._send(HTTPStatus.OK, {"entries": auditlog.read_entries(limit=100), "integrity": auditlog.verify_chain()})
        if path == "/api/policy":
            from pdfmanager import policy

            return self._send(HTTPStatus.OK, {"policies": policy.snapshot()})
        if path == "/api/debug/stacks" and os.environ.get("PDFM_LOG"):
            return self._send(HTTPStatus.OK, _thread_stacks(), "text/plain; charset=utf-8")
        self._error(HTTPStatus.NOT_FOUND, "not found")










    def do_POST(self):
        if not self._host_ok():
            return self._error(HTTPStatus.FORBIDDEN, "bad host")
        if self.path.split("?", 1)[0] == "/api/bye":
            return self._bye()
        if self.path.split("?", 1)[0] == "/api/instance":
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return self._error(HTTPStatus.BAD_REQUEST, "invalid Content-Length")
            if length < 0:
                return self._error(HTTPStatus.BAD_REQUEST, "invalid Content-Length")
            if length > 1_000_000:
                return self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "too large")
            try:
                return self._instance(self.rfile.read(length))
            except ValueError:
                return self._error(HTTPStatus.BAD_REQUEST, "bad request")
        if not self._token_ok():
            # (A small body is read and dropped first: answering while it's
            # still being sent can reach the sender as a broken connection
            # instead of this refusal, as Windows does.)
            try:
                unread = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                unread = 0
            if 0 < unread <= 1_000_000:
                self.rfile.read(unread)
            return self._error(HTTPStatus.FORBIDDEN, "missing or wrong token")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._error(HTTPStatus.BAD_REQUEST, "invalid Content-Length")
        if length < 0:
            return self._error(HTTPStatus.BAD_REQUEST, "invalid Content-Length")
        if length > MAX_UPLOAD:
            return self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                               f"upload too large ({MAX_UPLOAD // 2**20} MB max)")
        body = self.rfile.read(length)
        path = self.path.split("?", 1)[0]
        try:
            if path == "/api/ping":  # sent regularly while the page is open
                return self._send(HTTPStatus.OK, {"ok": True})
            if path == "/api/unsaved":  # the reader's unsaved tabs changed (see UnsavedWork)
                return self._unsaved(body)
            if path == "/api/run":
                return self._run(body)
            if path == "/api/inspect":
                return self._inspect(body)
            if path == "/api/facts":
                return self._facts(body)
            if path == "/api/preview":
                fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
                if not uploads:
                    raise UserError("no file")
                op = fields.pop("op", "")
                password = fields.pop("password", "") or None
                png = preview_operation(op, uploads[0][2], uploads[0][1], fields, password)
                return self._send(HTTPStatus.OK, png, "image/png")
            if path == "/api/keep":
                return self._keep(body)
            if path == "/api/quantities":
                return self._quantities(body)
            if path == "/api/markup/describe":
                return self._markup_describe(body)
            if path == "/api/markup":
                return self._markup(body)
            if path == "/api/images/list":
                return self._images_list(body)
            if path == "/api/images/edit":
                return self._images_edit(body)
            if path == "/api/scales":
                return self._scales(body)
            if path == "/api/layers":
                return self._layers(body)
            if path in ("/api/layers/save", "/api/layers/flatten"):
                return self._layers_change(path.rsplit("/", 1)[1], body)
            if path.startswith("/api/save/"):
                return self._save_in_place(path[len("/api/save/"):], body)
            if path == "/api/unsave":  # Undo after Save wrote over a file
                return self._unsave(json.loads(body or b"{}"))
            if path == "/api/reveal":
                return self._reveal(json.loads(body or b"{}"))
            if path == "/api/outline":
                return self._outline(body)
            if path == "/api/redact/find":
                return self._redact_find(body)
            # -- true redaction --
            if path == "/api/redact/apply":
                return self._redact_apply(body)
            # -- end of true redaction --
            if path == "/api/password/strength":
                # How hard a password being typed is to guess (worked out here, on this computer).
                data = json.loads(body or b"{}")
                return self._send(HTTPStatus.OK, protection.strength(str(data.get("password") or "")[:500]))
            if path == "/api/form/fields":
                return self._form_fields(body)
            if path == "/api/form/detect":
                return self._form_detect(body)
            if path == "/api/form/fillable":
                return self._form_fillable(body)
            if path == "/api/form/add":
                return self._form_add(body)
            if path == "/api/form/edit":
                return self._form_edit(body)
            if path == "/api/form/copy":
                return self._form_copy(body)
            if path == "/api/form/table":
                return self._form_table(body)
            if path == "/api/form/reorder":
                return self._form_reorder(body)
            if path == "/api/form/keep-defaults":
                return self._form_keep_defaults(body)
            if path == "/api/form/check":
                return self._form_check(body)
            if path == "/api/form/clear":
                return self._form_clear(body)
            if path == "/api/form/export":
                return self._form_export(body)
            if path == "/api/form/import":
                return self._form_import(body)
            if path == "/api/drawing/open":
                return self._drawing_open(body)
            if path == "/api/cbz/open":
                return self._cbz_open(body)
            if path == "/api/epub/open":
                return self._epub_open(body)
            if path == "/api/cbr/open":
                return self._cbr_open(body)
            if path == "/api/images/open":
                return self._images_open(body)
            if path == "/api/library/folder":
                return self._library_folder(body)
            if path == "/api/library/open":
                return self._library_open(body)
            if path == "/api/library/neighbors":
                return self._library_neighbors(body)
            if path == "/api/library/progress":
                return self._library_progress_save(body)
            if path == "/api/translate/manga-batch":
                return self._translate_manga_batch(body)
            if path in ("/api/comments/summary", "/api/comments/markdown", "/api/comments/export", "/api/comments/import"):
                return self._comments(path.rsplit("/", 1)[1], body)
            if path == "/api/text/edit":
                return self._text_edit(body)
            # -- text reflow --
            if path == "/api/text/fonts":
                return self._text_fonts(body)
            if path == "/api/text/runs":
                return self._text_runs(body)
            # -- end of text reflow --
            if path == "/api/ocr/unsure":
                return self._ocr_unsure(body)
            if path == "/api/ocr/words":
                return self._ocr_words(body)
            if path == "/api/ocr/fix":
                return self._ocr_fix(body)
            if path == "/api/ocr/replace":
                return self._ocr_replace(body)
            if path == "/api/sessions":
                return self._sessions_save(body)
            if path == "/api/sessions/delete":
                return self._sessions_delete(body)
            if path == "/api/pages/arrange":
                return self._arrange(body)
            if path == "/api/sign/cert":
                return self._sign_cert(body)
            if path == "/api/sign/verify":
                return self._sign_verify(body)
            if path.startswith("/api/scan/"):
                return self._scan_post(path[len("/api/scan/"):], body)
            if path == "/api/accessibility/check":  # -- accessibility --
                return self._access_check(body)
            if path == "/api/preflight/check":
                return self._preflight_check(body)
            if path == "/api/preflight/fix":
                return self._preflight_fix(body)
            if path == "/api/preflight/preview":
                return self._preflight_preview(body)
            if path == "/api/preflight/inkmap":
                return self._preflight_inkmap(body)
            if path == "/api/crop":
                return self._crop(body)
            if path == "/api/reader/compare":
                return self._reader_compare(body)
            if path == "/api/attachments/update":
                return self._attachments_update(body)
            if path == "/api/translate/page":
                return self._translate_page(body)
            if path == "/api/translate/manga-page":
                return self._translate_manga_page(body)
            if path == "/api/translate/manga-page/apply":
                return self._apply_manga_page(body)
            if path == "/api/ai/ask":
                return self._ai_ask(body)
            if path == "/api/ai/summarize":
                return self._ai_summarize(body)
            if path == "/api/ai/extract":
                return self._ai_extract(body)
            if path == "/api/ai/vlm-ocr":
                return self._ai_vlm_ocr(body)
            if path == "/api/models/download":
                from pdfmanager import models_manager

                pack_id = str(json.loads(body or b"{}").get("id") or "").strip()
                return self._job_or_now(lambda: {"json": models_manager.download_model(pack_id)})
            if path == "/api/models/delete":
                from pdfmanager import models_manager

                pack_id = str(json.loads(body or b"{}").get("id") or "").strip()
                return self._send(HTTPStatus.OK, models_manager.delete_model(pack_id))
            if path == "/api/settings":
                return self._save_settings(body)
            if path == "/api/stamps":
                from pdfmanager import stamps

                return self._send(HTTPStatus.OK, {"stamps": stamps.save(json.loads(body or b"{}").get("stamps")),
                                                  "most": stamps.MOST})
            if path == "/api/quit":  # Settings: quit for good, also while folders are watched
                behind = type(self).background
                if behind is None:
                    raise UserError("close the window to quit ZPDF")
                threading.Timer(0.4, behind.quit).start()  # once this answer has been sent
                return self._send(HTTPStatus.OK, {"quitting": True})
            if path == "/api/history/clear":
                return self._send(HTTPStatus.OK, {"cleared": privacy.clear_history(),
                                                  **_settings_for_page(type(self).instance_mode)})
            if path == "/api/filing/rules":
                return self._filing_rules_save(body)
            if path == "/api/filing/preview":
                return self._filing_preview(body)
            if path in ("/api/watch/add", "/api/watch/pause", "/api/watch/remove"):
                data = json.loads(body or b"{}")
                if path == "/api/watch/add":
                    do = {"recipe": data["recipe"]} if data.get("recipe") else {"tool": data.get("tool")}
                    self.watcher.add(data.get("folder"), do, data.get("results") or "done",
                                     data.get("originals") or "keep", images=_bool(data.get("images")),
                                     batch=_bool(data.get("batch")), auto_file=_bool(data.get("auto_file")))
                elif path == "/api/watch/pause":
                    self.watcher.set_paused(str(data.get("id")), data.get("paused"))
                else:
                    self.watcher.remove(str(data.get("id")))
                return self._watch_list()
            if path == "/api/i18n/language":
                return self._i18n_set_language(body)
            if path in ("/api/i18n/pack/install", "/api/i18n/packs/install"):
                return self._i18n_pack_install(body)
            if path in ("/api/i18n/pack/remove", "/api/i18n/packs/remove"):
                return self._i18n_pack_remove(body)
            if path in ("/api/i18n/pack/download", "/api/i18n/packs/download"):
                return self._i18n_pack_download(body)
            if path == "/api/translate/test":
                return self._translate_test(body)
            if path == "/api/translate/unload":
                return self._translate_unload()
            if path == "/api/shellmenu":
                from pdfmanager import winsetup

                if not _shell_menu_available():
                    raise UserError("the right-click menu is only for ZPDF installed on Windows with its installer")
                on = winsetup.set_context_menu(bool(json.loads(body or b"{}").get("on")))
                return self._send(HTTPStatus.OK, {"available": True, "on": on})
            if path == "/api/update/check":
                return self._send(HTTPStatus.OK, {**update.check(force=True),
                                                  "can_restart": self._can_restart()})
            if path == "/api/update/install":
                def install():
                    notes = []
                    return {"json": {"version": update.install(notes=notes), "note": " ".join(notes)}}

                return self._job_or_now(install)
            if path == "/api/update/restart":
                return self._restart()
            if path == "/api/recipes/save":
                data = json.loads(body or b"{}")
                saved = recipes.save(data.get("name"), data.get("steps"))
                return self._send(HTTPStatus.OK, {"recipe": saved,
                                                  "recipes": list(recipes.load_all().values())})
            if path == "/api/recipes/delete":
                recipes.delete(json.loads(body or b"{}").get("name"))
                return self._send(HTTPStatus.OK, {"recipes": list(recipes.load_all().values())})
            if path == "/api/folder":
                data = json.loads(body or b"{}")

                def folder_job():
                    result = run_folder_operation(data.get("op"), data.get("options") or {},
                                                  bool(data.get("apply")))
                    # Search results can be opened in the reader.
                    for match in result.get("matches", []):
                        match["id"] = self.opened.add(match["path"])
                    return {"json": result}

                return self._job_or_now(folder_job)
            if path.startswith("/api/job/"):
                return self._job(path, "POST")
            if path == "/api/logs/open":
                applog.open_folder()
                return self._send(HTTPStatus.OK, {"ok": True})
            if path == "/api/recent/forget":
                data = json.loads(body or b"{}")
                removed = RecentFiles.forget(data.get("path") if not data.get("all") else None)
                return self._send(HTTPStatus.OK, {"ok": True, "removed": removed})
            if path == "/api/recent/failed":  # the reader couldn't open it
                data = json.loads(body or b"{}")
                target = self.opened.get(str(data.get("id", ""))) if self.opened is not None else None
                if target is None:
                    raise UserError("that file is no longer available")
                # (A file protected for particular people isn't one that failed:
                # it opens with a Digital ID.)
                if not certcrypt.is_for_people(target):
                    RecentFiles.failed(target)
                return self._send(HTTPStatus.OK, {"ok": True})
            if path == "/api/recent/restore":  # Undo after a forget
                items = json.loads(body or b"{}").get("items")
                RecentFiles.restore(items if isinstance(items, list) else [])
                return self._send(HTTPStatus.OK, {"ok": True})
            return self._error(HTTPStatus.NOT_FOUND, "not found")
        except EXPECTED_ERRORS as exc:
            return self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:  # report, don't kill the server
            return self._error(HTTPStatus.INTERNAL_SERVER_ERROR, applog.unexpected(exc))





    # -- Scan: pages from a scanner or phone photos, then a PDF (scanner.py, scanpdf.py) --
















    # -- true redaction --
    # -- end of true redaction --





















    # -- text reflow --

    # -- end of text reflow --










    # -- accessibility --
    # -- end of accessibility --





# Static files (the reader and pdf.js), read once: rel path -> (bytes, ETag).

# The app uses this port when it's free, so the page keeps the same address
# between launches and the window's cache and settings (theme, reading
# positions, a saved signature) carry over. Another free port is used if
# it's taken, e.g. by a second copy of the app.
APP_PORT = 47821


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR would let a second copy of the app take the same
    # port; ask for the port exclusively there instead.
    allow_reuse_address = sys.platform != "win32"

    def server_bind(self):
        if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        # HTTPServer.server_bind looks up the computer's full name, which can
        # take seconds on some Windows networks; it isn't needed here.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]

    def handle_error(self, request, client_address):
        # The page cancels questions it no longer needs (a document closed, a
        # tab switched) and the window may close mid-answer: a reader that has
        # gone is not a fault worth a traceback in the log.
        if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
            return
        super().handle_error(request, client_address)

    def server_close(self):
        super().server_close()
        # Every way of ending the app comes through here. Jobs still running
        # are cancelled and given a moment to tidy up; whatever work folder is
        # then left (a job stuck in a long step) is removed all the same.
        handler = self.RequestHandlerClass
        jobs, work = getattr(handler, "jobs", None), getattr(handler, "work", None)
        if jobs is not None:
            jobs.stop()
        if work is not None:
            work.clear()
        scans = getattr(handler, "scans", None)
        if scans is not None:
            scans.clear()
        actions = getattr(handler, "actions", None)
        if actions is not None:
            actions.close()
        watcher = getattr(handler, "watcher", None)
        if watcher is not None:
            watcher.stop()






def _detect_lan_ip():
    """Return a non-loopback IPv4 address for this host (preferring RFC-1918
    private LAN addresses) without opening any outbound socket."""
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        return None
    fallback = None
    for info in infos:
        ip_str = info[4][0]
        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if addr.is_loopback or addr.is_unspecified or addr.is_link_local:
            continue
        if addr.is_private:
            return ip_str
        if fallback is None:
            fallback = ip_str
    return fallback


def make_server(port=0, desktop=False, open_paths=(), prefer=None, action=None, lan=False):
    """Create (but don't start) the server. Returns (server, url). The
    server's result cache is ``server.RequestHandlerClass.results`` and its
    openable files ``server.RequestHandlerClass.opened``. ``open_paths``
    are PDFs to show in the reader straight away. With ``port`` 0, the
    ``prefer``red port is tried first, then any free port. ``action``:
    (right-click action, its files) for the page to run when it opens."""
    applog.setup()
    applog.install()
    token = secrets.token_urlsafe(24)
    opened = OpenFiles()
    open_ids = [opened.add(p) for p in open_paths]
    handler = type("PdfmHandler", (Handler,), {"token": token, "results": ResultCache(),
                                               "opened": opened, "allow_eval": desktop,
                                               "last_seen": time.monotonic(),
                                               "closed_at": None, "jobs": progress.JobStore(),
                                               "pages": {}, "pages_lock": threading.Lock(),
                                               "events": Events(),
                                               "saved_over": SavedOver(), "unsaved": UnsavedWork(),
                                               "work": WorkFolders(), "scans": scanpdf.ScanBoxes()})
    handler.actions = quickactions.Batches(lambda batch: _start_action(handler, batch))
    handler.watcher = watchfolders.WatchFolders(log=_log)
    handler.watcher.start()  # (only once there's a folder to watch)
    bind_host = "0.0.0.0" if lan else "127.0.0.1"
    handler.lan_enabled = bool(lan)
    server = None
    if not port and prefer:
        try:
            server = _Server((bind_host, prefer), handler)
        except OSError:
            server = None  # in use
    if server is None:
        server = _Server((bind_host, port), handler)
    server.token = token
    real_port = server.server_address[1]
    handler.allowed_hosts = {f"127.0.0.1:{real_port}", f"localhost:{real_port}"}
    url = f"http://127.0.0.1:{real_port}/#token={token}"
    if open_ids:
        url += "&open=" + ",".join(open_ids)
    if action:
        batch, _ = handler.actions.add(action[0], action[1])
        url += "&action=" + batch.id
    return server, url


def serve(port=0, open_browser=True, open_paths=(), exit_when_closed=False, action=None, lan=False):
    """Run in the browser: print the link, open it, serve until Ctrl+C (or,
    with ``exit_when_closed``, until the page has been closed)."""
    privacy.begin_run()
    server, url = make_server(port, open_paths=() if action else open_paths, prefer=APP_PORT, action=action, lan=lan)
    print(f"ZPDF is running at {url}")
    if lan:
        lan_ip = _detect_lan_ip()
        if lan_ip:
            print(f"LAN companion URL: http://{lan_ip}:{server.server_address[1]}/#token={server.token}")
    print("Keep this window open while you use it; press Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    # Served from a thread in both cases, so that a restart after an update
    # (which asks this copy to quit) ends this one instead of leaving it
    # running next to the new copy.
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        if exit_when_closed:
            _register_instance(server, "browser")
            wait_until_closed(server.RequestHandlerClass, grace=5)
            _unregister_instance(server)
        else:
            server.RequestHandlerClass.instance_mode = "browser"  # no events to wait for
            while not server.RequestHandlerClass.quit_requested:
                time.sleep(0.5)
            _log("closing: restart asked for")
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
        privacy.end_run()


def wait_until_closed(handler, proc=None, idle=300, idle_handed_off=90, poll=0.5, grace=2.0,
                      handoff=5.0):
    """Block until the app's pages are gone.

    Each page says when its window closes (/api/bye) and otherwise pings the
    server every 15 seconds (less often while hidden). When the last page
    has said goodbye and no page has been heard from for ``grace`` seconds
    (a reload asks for the page again straight away), the app is closed.
    A page not heard from for ``idle`` seconds counts as gone (e.g. its
    browser was killed).

    With ``proc`` (the browser started for an app window): if it exits after
    the first ``handoff`` seconds, the window was closed. If it exits sooner,
    the browser handed the window to a copy that was already running, so only
    the pages' signals count. A browser that keeps running in the background
    after its window is closed (Edge does) doesn't keep the app alive.

    Time the computer spent asleep doesn't count: on Windows the clock
    keeps running through sleep, but the pages can't have pinged, so a
    poll that comes back much later than asked moves their timestamps on
    by the gap, and they get the usual ``idle`` seconds to be heard from."""
    started = last_poll = time.monotonic()
    exited_at = None
    pages = getattr(handler, "pages", None)
    if pages is None:
        pages = {}
    pages_lock = getattr(handler, "pages_lock", None)
    while True:
        time.sleep(poll)
        if getattr(handler, "quit_requested", False):
            _log("closing: restart asked for")
            return
        now = time.monotonic()
        gap, last_poll = now - last_poll - poll, now
        if gap > 5:
            _log(f"a poll took {gap:.0f}s longer than asked (asleep?); not counting it")
            handler.last_seen = min(handler.last_seen + gap, now)
            if handler.closed_at is not None:
                handler.closed_at = min(handler.closed_at + gap, now)
            if pages_lock is not None:
                with pages_lock:
                    for page, seen in list(pages.items()):
                        pages[page] = min(seen + gap, now)
            else:
                for page, seen in list(pages.items()):
                    if page in pages:  # not one that said goodbye just now
                        pages[page] = min(seen + gap, now)
        if pages_lock is not None:
            with pages_lock:
                for page, seen in list(pages.items()):
                    if now - seen > idle:
                        pages.pop(page, None)
                no_pages = not pages
        else:
            for page, seen in list(pages.items()):
                if now - seen > idle:
                    pages.pop(page, None)
            no_pages = not pages
        closed = handler.closed_at
        if (closed is not None and no_pages and handler.last_seen <= closed
                and now - closed > grace):
            _log("closing: the last page said goodbye")
            return
        quiet = now - handler.last_seen
        if proc is not None and proc.poll() is not None:
            if exited_at is None:
                exited_at = now
            if exited_at - started > handoff + poll or quiet > idle_handed_off:
                _log(f"closing: the browser exited (after {exited_at - started:.1f}s; quiet {quiet:.0f}s)")
                return
        elif quiet > idle:
            _log(f"closing: no page heard from for {quiet:.0f}s")
            return


# --------------------------------------------------------------------------
# One running copy
# --------------------------------------------------------------------------

def _instance_file():
    return data_dir() / "instance.json"


class _StartupLock:
    """Only one launch at a time looks for a running copy and registers
    itself, so files opened together (e.g. selected in Explorer and opened
    at once) all land in one app rather than each starting its own."""

    def __init__(self):
        self.fh = None

    def acquire(self, timeout=30):
        try:
            path = data_dir() / "launch.lock"
            path.parent.mkdir(parents=True, exist_ok=True)
            fh = open(path, "a+b")
        except OSError:
            return
        deadline = time.monotonic() + timeout
        while True:
            try:
                if sys.platform == "win32":
                    import msvcrt
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.fh = fh
                return
            except OSError:
                if time.monotonic() > deadline:  # carry on without it
                    fh.close()
                    return
                time.sleep(0.1)

    def release(self):
        fh, self.fh = self.fh, None
        if fh is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh, fcntl.LOCK_UN)
        except OSError:
            pass
        fh.close()


_startup = _StartupLock()


def _register_instance(server, mode, **extra):
    """Let later launches find this one (see forward_to_running_app)."""
    handler = server.RequestHandlerClass
    handler.instance_secret = secrets.token_urlsafe(32)
    handler.instance_mode = mode
    handler.instance_extra = {k: str(v) for k, v in extra.items()}
    _log(f"registered as {mode} on port {server.server_address[1]}")
    record = {"port": server.server_address[1], "pid": os.getpid(),
              "secret": handler.instance_secret, "mode": mode}
    try:
        write_private(_instance_file(), json.dumps(record))
    except OSError:
        pass
    _startup.release()  # later launches can find this copy now


def _unregister_instance(server):
    target = _instance_file()
    try:
        record = json.loads(target.read_text(encoding="utf-8"))
        if record.get("pid") == os.getpid() and record.get("port") == server.server_address[1]:
            target.unlink()
    except (OSError, ValueError):
        pass


def _allow_foreground():
    """Let the running copy bring its window forward (Windows): only the
    program in front, which this launch is when a PDF was double-clicked,
    can allow another to take the foreground."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
    except Exception as _exc:  # noqa: BLE001 - a courtesy; the file is handed over all the same
        logging.getLogger("zpdf").debug("gui._allow_foreground: %s", _exc)


def forward_to_running_app(paths=(), action=None, quiet=False):
    """If ZPDF is already running, hand it ``paths`` to open (or, with
    ``action``, to run that right-click action on) and return True, so this
    launch can end: there's only ever one copy.

    The running app either opens them itself (its own window, which comes
    to the front) or answers with an address to open, as a new window in
    its Edge/Chrome copy or a new browser tab. ``quiet``: only find out
    whether a copy is running (a start at sign-in when one already is):
    nothing is opened or brought forward."""
    try:
        record = json.loads(_instance_file().read_text(encoding="utf-8"))
        port, secret = int(record["port"]), str(record["secret"])
    except (OSError, ValueError, KeyError, TypeError):
        return False
    # Neither side sends the secret: each proves it knows it (anything else
    # could be listening on that port after a crash left instance.json behind).
    nonce = secrets.token_urlsafe(16)
    message = {"nonce": nonce, "proof": _proof(secret, "open", nonce),
               "paths": [str(Path(p).resolve()) for p in paths]}
    if action:
        message["action"] = action
    if quiet:
        message["quiet"] = True
    body = json.dumps(message).encode()
    request = urllib.request.Request(f"http://127.0.0.1:{port}/api/instance", data=body,
                                     headers={"Content-Type": "application/json",
                                              "Host": f"127.0.0.1:{port}"})
    # Straight to this computer: a system or http_proxy proxy must not see it.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    _allow_foreground()
    try:
        with opener.open(request, timeout=5) as res:
            answer = json.loads(res.read())
    except (OSError, ValueError):
        return False  # not running any more (or something else has the port)
    if not isinstance(answer, dict) or not secrets.compare_digest(
            str(answer.get("proof", "")), _proof(secret, "answer", nonce)):
        return False  # not ZPDF
    if answer.get("handled") or quiet:
        return True
    url = str(answer.get("url") or "")
    if not url.startswith(f"http://127.0.0.1:{port}/#token="):
        return False
    if answer.get("mode") == "app-window":
        # The same kind of window, with this computer's browser and the
        # running copy's profile (both found here, not taken from the answer).
        browser = find_app_browser()
        if browser:
            try:
                subprocess.Popen([browser, f"--app={url}", f"--user-data-dir={_window_profile(port)}",
                                  "--no-first-run", "--no-default-browser-check"],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
                return True
            except OSError:
                pass
    webbrowser.open(url)
    return True




def _window_profile(port):
    """The browser profile of the app window served on ``port``."""
    return data_dir() / ("window" if port == APP_PORT else f"window-{port}")


class DesktopApi:
    """Functions the page can call in the desktop window (window.pywebview.api)."""

    def __init__(self, results, opened=None):
        self._results = results
        self._opened = opened
        self._window = None

    def open_pdf(self):
        """Pick a PDF with the native Open dialog for the reader. Returns
        {"id", "name"} or None if cancelled."""
        import webview

        chosen = self._window.create_file_dialog(
            webview.FileDialog.OPEN, directory=str(Path.home()),
            file_types=("PDF, E-Books & Comics (*.pdf;*.epub;*.cbz;*.cbr)", "PDF files (*.pdf)",
                        "E-Books (*.epub)", "Comic / Manga archives (*.cbz;*.cbr)", "All files (*.*)"))
        if not chosen:
            return None
        path = Path(chosen if isinstance(chosen, str) else chosen[0])
        return {"id": self._opened.add(path), "name": path.name}

    def save_result(self, key, suggested_name, near=None):
        """Ask where to save a result and write it. Returns the path, or
        None if the user cancelled. The dialog starts in the folder of the
        opened file ``near`` (its id), if given, else the home folder."""
        import webview

        item = self._results.get(key)
        if item is None:
            raise ValueError("that result has expired; run the tool again")
        name, data = item
        beside = self._opened.get(near) if near else None
        folder = beside.parent if beside is not None and beside.parent.is_dir() else Path.home()
        filename = suggested_name or name
        chosen = self._window.create_file_dialog(
            webview.FileDialog.SAVE, directory=str(folder), save_filename=filename,
            file_types=_save_types(filename))
        if not chosen:
            return None
        target = Path(chosen if isinstance(chosen, str) else chosen[0])
        target.write_bytes(data)
        if self._opened is not None:
            self._opened.add(target)  # (so it can be shown in its folder)
        return str(target)

    def pick_folder(self):
        import webview

        chosen = self._window.create_file_dialog(webview.FileDialog.FOLDER,
                                                 directory=str(Path.home()))
        return (chosen if isinstance(chosen, str) else chosen[0]) if chosen else None

    def reveal(self, path):
        """Show a saved file in Explorer / Finder / the file manager (one
        the app has open or has saved)."""
        if self._opened is not None and not self._opened.knows(path):
            raise ValueError("not a file ZPDF has open")
        reveal_in_file_manager(path)






def desktop_available():
    return importlib.util.find_spec("webview") is not None


def _pywebview_version():
    try:
        from importlib.metadata import version

        return version("pywebview")
    except Exception:
        return "unknown"


def _window_note():
    return data_dir() / "window-error.txt"


def _os_prefers_dark():
    """Best-effort detection of OS dark mode so the desktop window does not
    flash bright white before CSS loads."""
    try:
        if sys.platform == "win32":
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            ) as key:
                val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
                return int(val) == 0
        if sys.platform == "darwin":
            out = subprocess.run(
                ["defaults", "read", "-g", "AppleInterfaceStyle"],
                capture_output=True, text=True, timeout=0.5,
            )
            return out.returncode == 0 and "dark" in out.stdout.lower()
        gtk = os.environ.get("GTK_THEME", "")
        if "dark" in gtk.lower():
            return True
        if shutil.which("gsettings"):
            out = subprocess.run(
                ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
                capture_output=True, text=True, timeout=0.5,
            )
            if out.returncode == 0 and "dark" in out.stdout.lower():
                return True
    except Exception as _exc:  # noqa: BLE001 - fallback to light theme on any OS probe error
        logging.getLogger("zpdf").debug("gui._os_prefers_dark: %s", _exc)
    return False


def _startup_background_color():
    return "#0f1114" if _os_prefers_dark() else "#f6f6f3"


def run_desktop(port=0, open_paths=(), action=None, hidden=False, lan=False):
    """Open ZPDF in its own window (needs pywebview). Returns False if
    no window could be opened, so the caller can fall back to the browser.

    If the window can't open (for example Windows Smart App Control blocks
    a library pywebview needs), the reason is saved and this pywebview
    version isn't tried again, so the user doesn't see the error at every
    start. Installing another pywebview version tries again."""
    if not desktop_available():
        return False
    note, version = _window_note(), _pywebview_version()
    try:
        if note.read_text(encoding="utf-8").splitlines()[0] == version:
            return False
    except (OSError, IndexError):
        pass
    server, url = make_server(port, desktop=True, open_paths=() if action else open_paths, prefer=APP_PORT,
                              action=action, lan=lan)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    handler = server.RequestHandlerClass
    api = DesktopApi(handler.results, handler.opened)
    _register_instance(server, "desktop")
    try:
        import webview

        shape = {"hidden": True} if hidden and "hidden" in inspect.signature(webview.create_window).parameters else {}
        window = webview.create_window("ZPDF", url, js_api=api, width=1180, height=820,
                                       min_size=(400, 560), background_color=_startup_background_color(), **shape)
        api._window = window
        _no_autofill(window)
        # Closing with the window's X: while a folder is watched ZPDF can stay
        # in the background (background.py); otherwise ask first when anything
        # is unsaved (WebView2 fires no beforeunload when the window itself closes).
        behind = background.InBackground(
            handler, window, may_close=lambda: may_close(handler, lambda text: _ask_to_close(window, text)),
            ask=lambda text: _ask_to_close(window, text, warning=False),
            bring_forward=lambda: _raise_window(window), log=_log)
        handler.background = behind
        handler.bring_to_front = behind.open
        handler.quit_app = lambda: _close_on_purpose(handler, window)
        try:
            window.events.closing += behind.closing
        except AttributeError as exc:  # pywebview before 4 has no closing event
            _log(f"launch: no closing event to warn about unsaved work: {exc!r}")
        if shape:
            # (On its own thread: if no icon can be shown the window is shown
            # instead, which pywebview only does once the window exists, and
            # that happens in webview.start() below.)
            threading.Thread(target=behind.started_hidden, name="start-hidden", daemon=True).start()
        # Keep the window's cache and settings between launches (pywebview
        # otherwise starts every time like a private browser window).
        options = {}
        debug_port = _debug_port()
        if debug_port:  # for tests (pywebview 5.3+; its settings aren't a plain dict)
            try:
                webview.settings["REMOTE_DEBUGGING_PORT"] = debug_port
            except (AttributeError, KeyError, TypeError):
                pass
        if "storage_path" in inspect.signature(webview.start).parameters:
            storage = data_dir() / "webview"
            storage.mkdir(parents=True, exist_ok=True)
            options = {"private_mode": False, "storage_path": str(storage)}
        webview.start(**options)
        behind.stop()
        _unregister_instance(server)
    except Exception as exc:  # e.g. a missing or blocked web engine
        print(f"Could not open the app window ({exc}); trying another way.")
        _unregister_instance(server)
        try:
            note.parent.mkdir(parents=True, exist_ok=True)
            note.write_text(f"{version}\n{type(exc).__name__}: {exc}\n", encoding="utf-8")
        except OSError:
            pass
        server.shutdown()
        server.server_close()
        return False
    server.shutdown()
    server.server_close()
    return True


def close_question(names):
    """What closing ZPDF asks with the files ``names`` unsaved (None: nothing to ask)."""
    if not names:
        return None
    if len(names) == 1:
        which = f"Your changes to “{names[0]}” haven't been saved."
    elif len(names) <= 3:
        which = f"Your changes to {len(names)} files haven't been saved: " + ", ".join(names) + "."
    else:
        which = f"Your changes to {len(names)} files haven't been saved."
    return "Close ZPDF? " + which


def may_close(handler, ask):
    """pywebview's closing event (False keeps the window open). Closing on
    purpose (to start again after an update, which asked about unsaved work
    itself) never asks. Otherwise, with anything unsaved (as the page last
    told the server), ``ask(text)``: True closes anyway. Whatever goes wrong
    asking, the window closes (it must never be impossible to close)."""
    if handler.quit_requested:
        return True
    question = close_question(handler.unsaved.names() if handler.unsaved is not None else [])
    if not question:
        return True
    try:
        answer = bool(ask(question))
    except Exception as exc:  # noqa: BLE001
        _log(f"closing: couldn't ask about unsaved work: {exc!r}")
        return True
    _log(f"closing with unsaved work: {'closed' if answer else 'kept open'}")
    return answer


def _close_on_purpose(handler, window):
    """Close the window without asking (see may_close)."""
    handler.quit_requested = True
    window.destroy()


MB_YESNO, MB_ICONWARNING, MB_DEFBUTTON2, MB_SETFOREGROUND, MB_TOPMOST, IDYES = 0x4, 0x30, 0x100, 0x10000, 0x40000, 6
MB_ICONQUESTION = 0x20


def _ask_to_close(window, text, warning=True):
    """Ask ``text`` with Yes and No (No the default): True for Yes.

    pywebview runs the closing event on the window's own thread and waits
    for it, so this mustn't wait for that thread: on Windows a plain
    MessageBoxW (it runs its own message loop; owned by the window, when its
    handle can be had, so it sits on top of it). Elsewhere pywebview's own
    dialog, unless this is the GUI's main thread, where it would wait for
    itself (GTK, Cocoa, Qt): the window then closes as before."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        try:
            owner = int(window.native.Handle.ToInt64())
        except Exception:  # noqa: BLE001 - no WinForms form to own it
            owner = None
        # (A question that isn't a warning: the question mark, and Yes the default.)
        look = MB_ICONWARNING | MB_DEFBUTTON2 if warning else MB_ICONQUESTION
        flags = MB_YESNO | look | (0 if owner else MB_TOPMOST | MB_SETFOREGROUND)
        box = ctypes.windll.user32.MessageBoxW
        box.argtypes = (wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT)
        box.restype = ctypes.c_int
        return box(owner, text, "ZPDF", flags) == IDYES
    confirm = getattr(window, "create_confirmation_dialog", None)
    if confirm is None or threading.current_thread() is threading.main_thread():
        return True
    return bool(confirm("Close ZPDF?", text))


def _no_autofill(window):
    """Keep WebView2 from saving or offering passwords and form entries: a
    PDF's password, or what was typed into one PDF's form, must not be kept
    in the app's browser profile and offered again in another document.
    (0.22.1 turned both on, for password managers; they are off since
    0.40.0, when the checks showed what that kept.) WebView2 has them off by
    default; they are set off here all the same, so a profile or a later
    default can't bring them back. Windows' EdgeChromium
    backend only: pywebview runs its before_show event on the window's own
    thread right after making the WebView2 control (window.native.browser.
    webview); its CoreWebView2 is ready later, in CoreWebView2Initialization
    Completed, where the settings are set. Anything missing or failing is
    logged and the window opens as before."""
    if sys.platform != "win32":
        return

    def on_ready(sender, args):
        try:
            if args.IsSuccess:
                settings = sender.CoreWebView2.Settings
                settings.IsPasswordAutosaveEnabled = False
                settings.IsGeneralAutofillEnabled = False
        except Exception as exc:  # noqa: BLE001 - an older WebView2 lacks these settings
            _log(f"launch: couldn't turn off password saving and autofill: {exc!r}")

    def before_show(window):
        try:
            control = window.native.browser.webview
            if getattr(control, "CoreWebView2", None) is not None:  # ready already
                on_ready(control, type("Ready", (), {"IsSuccess": True})())
            else:
                control.CoreWebView2InitializationCompleted += on_ready
        except Exception as exc:  # noqa: BLE001 - another backend (no WebView2 control)
            _log(f"launch: no WebView2 settings to turn on autofill: {exc!r}")

    try:
        window.events.before_show += before_show
    except AttributeError as exc:  # pywebview before 5 has no before_show event
        _log(f"launch: no before_show event for autofill: {exc!r}")


SAVE_TYPES = {".pdf": "PDF files (*.pdf)", ".zip": "ZIP archives (*.zip)", ".docx": "Word documents (*.docx)",
              ".xlsx": "Excel workbooks (*.xlsx)", ".pptx": "PowerPoint presentations (*.pptx)",
              ".html": "Web pages (*.html)", ".png": "PNG pictures (*.png)", ".jpg": "JPEG pictures (*.jpg)",
              ".txt": "Text files (*.txt)", ".csv": "CSV files (*.csv)", ".dxf": "DXF drawings (*.dxf)"}


def _save_types(name):
    """The Save dialog's file type list for a file of this name: its own kind
    first (Windows shows that, not "All files"), then all files."""
    kind = SAVE_TYPES.get(Path(name).suffix.lower())
    return (kind, "All files (*.*)") if kind else ("All files (*.*)",)


def _debug_port():
    """PDFM_DEBUG_PORT: let tests drive the window over the DevTools protocol."""
    try:
        return int(os.environ.get("PDFM_DEBUG_PORT", "")) or None
    except ValueError:
        return None


def find_app_browser():
    """A Chromium-based browser that can show the app as a window of its
    own, without tabs or an address bar: Edge (part of Windows), Chrome,
    Chromium or Brave. Returns its path or None."""
    if sys.platform == "win32":
        bases = [os.environ.get(k) for k in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA")]
        apps = ["Microsoft/Edge/Application/msedge.exe", "Google/Chrome/Application/chrome.exe",
                "BraveSoftware/Brave-Browser/Application/brave.exe"]
        for app in apps:
            for base in filter(None, bases):
                if (Path(base) / app).is_file():
                    return str(Path(base) / app)
        return None
    if sys.platform == "darwin":
        for app in ("Microsoft Edge", "Google Chrome", "Chromium", "Brave Browser"):
            path = Path("/Applications") / f"{app}.app" / "Contents" / "MacOS" / app
            if path.is_file():
                return str(path)
        return None
    for name in ("microsoft-edge", "microsoft-edge-stable", "google-chrome", "google-chrome-stable",
                 "chromium", "chromium-browser", "brave-browser"):
        found = shutil.which(name)
        if found:
            return found
    return None


def run_app_window(port=0, open_paths=(), action=None, lan=False):
    """Open ZPDF as an Edge/Chrome app window. Returns False if no
    such browser is installed. Results are saved like downloads."""
    browser = find_app_browser()
    if not browser:
        return False
    server, url = make_server(port, open_paths=() if action else open_paths, prefer=APP_PORT, action=action, lan=lan)
    # A browser profile of its own keeps the window separate from the user's
    # normal browsing, so closing the app never touches their other windows.
    # A second copy of the app (on another port) gets a temporary profile of
    # its own, so each copy only ever closes its own browser.
    primary = server.server_address[1] == APP_PORT
    profile = _window_profile(server.server_address[1])
    try:
        profile.mkdir(parents=True, exist_ok=True)
        # PDFM_BROWSER_ARGS: more options for the browser (for tests and
        # troubleshooting), and PDFM_DEBUG_PORT a remote debugging port.
        extra = os.environ.get("PDFM_BROWSER_ARGS", "").split()
        if _debug_port():
            extra.append(f"--remote-debugging-port={_debug_port()}")
        proc = subprocess.Popen(
            [browser, f"--app={url}", f"--user-data-dir={profile}", "--no-first-run",
             "--no-default-browser-check", "--window-size=1180,820", *extra],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        server.server_close()
        return False
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _register_instance(server, "app-window", browser=browser, profile=profile)
    _log(f"app window: {browser} on port {server.server_address[1]}, profile {profile}")
    try:
        wait_until_closed(server.RequestHandlerClass, proc)
    except KeyboardInterrupt:
        pass
    _unregister_instance(server)
    _log("app window: instance file removed")
    server.shutdown()
    server.server_close()
    _log("app window: server stopped")
    _close_browser(proc, profile, Path(browser).name)
    _log("app window: browser closed")
    if not primary:
        shutil.rmtree(profile, ignore_errors=True)
    return True


def _profile_processes(profile, exe=None):
    """Ids of browser processes running with this profile (Edge and Chrome
    may keep running in the background after their last window closes).
    ``exe`` (e.g. "msedge.exe") narrows the search, which is much quicker
    on Windows."""
    marker = f"--user-data-dir={profile}"
    if sys.platform == "win32":
        # The whole argument (a prefix would also match "...window-9000").
        pattern = re.sub(r"([\\.^$|?*+()\[\]{}])", r"\\\1", marker) + '("|\\s|$)'
        where = f"-Filter \"Name = '{exe}'\" " if exe and re.fullmatch(r"[\w.-]+", exe) else ""
        script = (f"Get-CimInstance Win32_Process {where}| Where-Object {{ $_.CommandLine -and "
                  "($_.CommandLine -match $env:PDFM_PROFILE_RE) } | "
                  "ForEach-Object { $_.ProcessId }")
        started = time.monotonic()
        try:
            out = subprocess.run([office.powershell_exe(), "-NoProfile", "-NonInteractive", "-Command", script],
                                 capture_output=True, text=True, timeout=20,
                                 env={**os.environ, "PDFM_PROFILE_RE": pattern},
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.TimeoutExpired) as exc:
            _log(f"profile processes: {exc}")
            return []
        pids = [int(p) for p in out.stdout.split() if p.isdigit()]
        _log(f"profile processes ({exe or 'any'}): {pids} in {time.monotonic() - started:.1f}s"
             + (f"; stderr: {out.stderr.strip()[:200]}" if out.stderr.strip() else ""))
        return pids
    if sys.platform == "darwin" or not Path("/proc").is_dir():
        pattern = re.compile(re.escape(marker) + r"(?:\s|$)")
        try:
            out = subprocess.run(["ps", "-axo", "pid=,command="],
                                 capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            return []
        pids = []
        for line in out.stdout.splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) == 2 and parts[0].isdigit() and pattern.search(parts[1]):
                pids.append(int(parts[0]))
        return pids
    pids = []
    for entry in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            args = entry.read_bytes().decode("utf-8", "replace").split("\0")
            if marker in args:
                pids.append(int(entry.parent.name))
        except (OSError, ValueError):
            continue
    return pids


def _close_browser(proc, profile, exe=None):
    """Stop the browser that showed the app window once the window is gone."""
    try:
        proc.wait(timeout=1.5)  # usually it quits by itself
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
    # A browser started earlier with this profile (e.g. kept running in the
    # background by Edge) may still be there.
    for pid in _profile_processes(profile, exe):
        if pid == os.getpid():
            continue
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


def _win32_bring_forward(title):
    """Bring this program's window called ``title`` to the front through
    Windows itself: nothing is asked of the window's own thread. True when
    a window was found."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    mine = os.getpid()
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == mine and user32.IsWindowVisible(hwnd):
            text = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, text, 512)
            if text.value == title:
                found.append(hwnd)
        return True

    user32.EnumWindows(each, 0)
    for hwnd in found:
        if user32.IsIconic(hwnd):
            user32.ShowWindowAsync(hwnd, 9)  # SW_RESTORE, without waiting for the window
        user32.SetForegroundWindow(hwnd)
    return bool(found)


def _raise_window(window):
    """Bring the pywebview window to the front (a file was handed to it).
    This runs on the server's thread. On Windows the window is raised
    through the system: pywebview's own calls wait for the window's thread,
    and the window froze when that thread was busy opening the file."""
    if sys.platform == "win32":
        try:
            if _win32_bring_forward(window.title):
                return
        except Exception:  # noqa: BLE001 - raising the window is a courtesy
            return
        return
    try:
        window.restore()
        window.show()
        window.on_top = True
        window.on_top = False
    except Exception as _exc:
        logging.getLogger("zpdf").debug("gui._raise_window: %s", _exc)


def _rename_old_install():
    """Shortcuts made before the app was called ZPDF get the new name and icon."""
    from pdfmanager import update, winsetup

    if update.how_installed() != "pip":
        return
    try:
        if winsetup.rename_old_install():
            _log("launch: renamed the shortcuts of an earlier install")
    except Exception as exc:  # noqa: BLE001 - the app starts all the same
        _log(f"launch: renaming the old shortcuts failed: {exc!r}")


def launch(open_paths=(), port=0, action=None, background_start=False, lan=False):
    """Open the app the best way available: its own pywebview window, else
    an Edge/Chrome app window, else a browser tab. Returns once closed. If
    the app is already running, it gets the files instead. ``action``: a
    right-click action to run on the files (quickactions.ACTIONS) rather
    than opening them. ``background_start``: start with no window, only
    the notification-area icon (at sign-in; see background.py): nothing
    happens if ZPDF is running already or can't stay in the background."""
    _startup.acquire()
    applog.setup()
    applog.started(update.how_installed())
    _log(f"launch: {list(map(str, open_paths))} port {port}" + (f" action {action}" if action else ""))
    try:
        if background_start:
            if not port and forward_to_running_app(quiet=True):
                _log("launch: in the background, and a copy is running already")
                return
            if not background.supported()[0] or not desktop_available():
                _log("launch: can't start in the background here; not starting")
                return
            privacy.begin_run()
            if not run_desktop(port=port, hidden=True, lan=lan):
                _log("launch: the window couldn't be made; not starting in the background")
            return
        if not port and not lan and forward_to_running_app(open_paths, action=action):
            _log("launch: handed to the running copy")
            return
        privacy.begin_run()  # this copy's working files, in its own folder; earlier leftovers swept
        if sys.platform == "win32":
            threading.Thread(target=_rename_old_install, daemon=True).start()
        # PDFM_WINDOW=desktop, app-window or browser uses only that kind of
        # window (for tests and troubleshooting).
        choice = os.environ.get("PDFM_WINDOW", "").strip().lower()
        todo = (action, list(open_paths)) if action else None
        if choice in ("", "desktop") and run_desktop(port=port, open_paths=open_paths, action=todo, lan=lan):
            return
        if choice in ("", "app-window") and run_app_window(port=port, open_paths=open_paths, action=todo, lan=lan):
            return
        serve(port=port, open_paths=open_paths, exit_when_closed=True, action=todo, lan=lan)
    finally:
        _startup.release()
        privacy.end_run()


def main_app(argv=None):
    """Entry point for the `pdfm-app` launcher. PDFs given as arguments
    (e.g. by "Open with…" or a file association) open in the reader."""
    args = sys.argv[1:] if argv is None else argv
    launch([a for a in args if Path(a).suffix.lower() == ".pdf" and Path(a).is_file()],
           background_start="--background" in args)
