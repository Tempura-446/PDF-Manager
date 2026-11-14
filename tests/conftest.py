# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Settings shared by every test: nothing reaches the user's own ZPDF folder
(settings, recent files, recipes, the search index, the log, the update
cache, OCR models), and nothing checks GitHub for updates. Also the
--perf option, which runs the performance limits (test_perf.py).

ZPDF finds its folder through appdata._data_dir(), so pointing that at a
scratch folder covers every module. Updates are switched off in the scratch
folder's settings; the update tests bring their own folder and fake GitHub
(the "updates" fixture in test_more.py)."""
import pytest

from pdfmanager import appdata

REAL_DATA_DIR = appdata._data_dir  # before any test points it elsewhere (test_more checks it)


def pytest_addoption(parser):
    parser.addoption("--perf", action="store_true",
                     help="also run the performance limits (test_perf.py); the release check does")
    parser.addoption("--sweep", metavar="CORPUS", default="",
                     help="run the maturity sweep on a corpus made by tests/sweep/make_corpus.py")
    parser.addoption("--sweep-out", metavar="DIR", default="", help="where the sweep writes its report")
    parser.addoption("--sweep-reader", metavar="N", type=int, default=0,
                     help="also open N of the sweep's files in the reader (needs Playwright for Node.js; "
                          "skipped without it)")


def pytest_configure(config):
    config.addinivalue_line("markers", "perf: a performance limit, run only with --perf")
    config.addinivalue_line("markers", "sweep: the maturity sweep, run only with --sweep CORPUS")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--perf"):
        return
    skip = pytest.mark.skip(reason="a performance limit: run with --perf")
    for item in items:
        if "perf" in item.keywords:
            item.add_marker(skip)


def _scratch(folder):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "settings.json").write_text('{"check_updates": false}', encoding="utf-8")
    return folder


@pytest.fixture(scope="session", autouse=True)
def _nothing_reaches_the_users_folder(tmp_path_factory):
    """For the whole session, before any module-wide fixture starts a server."""
    from pdfmanager import appdata, applog

    folder = _scratch(tmp_path_factory.mktemp("session-data"))
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(appdata, "_data_dir", lambda: folder)
        yield
    applog.shutdown()


@pytest.fixture(autouse=True)
def _each_test_has_its_own_folder(tmp_path_factory, monkeypatch):
    from pdfmanager import appdata, applog

    folder = _scratch(tmp_path_factory.mktemp("data"))
    monkeypatch.setattr(appdata, "_data_dir", lambda: folder)
    yield
    applog.shutdown()
