# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Regression tests for v0.88.1 audit polish: LAN CLI wiring, OpenFiles
deduplication, handler.pages lock, pip wheel update selection, and scoped
_leading_dash_ranges."""

import socket
from pathlib import Path
from unittest import mock

import pytest

from pdfmanager import gui, update
from pdfmanager.cli import parser as cli_parser
from pdfmanager.server.common import OpenFiles


def test_cli_gui_and_view_accept_lan_flag():
    p = cli_parser.build_parser()
    a_gui = p.parse_args(["gui", "--lan", "--browser"])
    assert a_gui.lan is True
    assert a_gui.browser is True
    a_view = p.parse_args(["view", "--lan", "a.pdf"])
    assert a_view.lan is True
    a_default = p.parse_args(["gui"])
    assert a_default.lan is False


def test_detect_lan_ip_prefers_private_ipv4():
    fake_infos = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.1.2", 0)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.42", 0)),
    ]
    with mock.patch("socket.getaddrinfo", return_value=fake_infos):
        assert gui._detect_lan_ip() == "192.168.1.42"


def test_open_files_deduplicates_paths(tmp_path):
    f1 = tmp_path / "doc.pdf"
    f1.write_bytes(b"%PDF-1.4\n")
    opened = OpenFiles()
    id1 = opened.add(f1)
    id2 = opened.add(f1)
    id3 = opened.add(str(f1))
    assert id1 == id2 == id3
    assert len(opened.items) == 1
    assert opened.get(id1) == f1.resolve()


def test_make_server_initializes_pages_lock_and_lan_flag():
    server, _url = gui.make_server(port=0, lan=False)
    try:
        handler = server.RequestHandlerClass
        assert hasattr(handler, "pages_lock")
        assert handler.lan_enabled is False
    finally:
        server.server_close()


def test_update_release_asset_candidates_for_pip_prefers_source_bundle():
    assert update.release_asset_candidates("0.88.1", platform_name="darwin", for_pip=True) == [
        "ZPDF-0.88.1-windows.zip",
    ]
    assert update.release_asset_candidates("0.88.1", platform_name="linux", for_pip=True) == [
        "ZPDF-0.88.1-windows.zip",
    ]
    assert update.release_asset_candidates("0.88.1", platform_name="darwin", for_pip=False)[0].endswith(".dmg")


def test_leading_dash_ranges_only_rewrites_after_range_flags():
    assert cli_parser._leading_dash_ranges(["extract", "a.pdf", "-p", "-3,5"]) == [
        "extract", "a.pdf", "-p", "1-3,5",
    ]
    assert cli_parser._leading_dash_ranges(["remove", "a.pdf", "--pages", "-2"]) == [
        "remove", "a.pdf", "--pages", "1-2",
    ]
    # Filenames / passwords starting with -digit are untouched
    assert cli_parser._leading_dash_ranges(["info", "-2026-report.pdf"]) == [
        "info", "-2026-report.pdf",
    ]
    assert cli_parser._leading_dash_ranges(["unlock", "a.pdf", "--password", "-123abc"]) == [
        "unlock", "a.pdf", "--password", "-123abc",
    ]
