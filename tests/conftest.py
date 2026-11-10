"""Settings shared by every test: nothing reaches the user's own ZPDF folder
(settings, recent files, recipes, the search index, the log, the update
cache, OCR models), and nothing checks GitHub for updates.

ZPDF finds its folder through appdata._data_dir(), so pointing that at a
scratch folder covers every module. Updates are switched off in the scratch
folder's settings; the update tests bring their own folder and fake GitHub
(the "updates" fixture in test_more.py)."""
import pytest

from pdfmanager import appdata

REAL_DATA_DIR = appdata._data_dir  # before any test points it elsewhere (test_more checks it)


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
