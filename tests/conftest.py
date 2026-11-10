"""Settings shared by every test."""
import pytest


@pytest.fixture(autouse=True)
def _updates_stay_offline(tmp_path_factory, monkeypatch):
    """No test checks GitHub for updates or writes the user's update cache:
    the update module keeps its settings in a scratch folder where checking
    is turned off. The update tests bring their own folder and fake GitHub
    (the "updates" fixture in test_more.py)."""
    from pdfmanager import update

    folder = tmp_path_factory.mktemp("updates")
    (folder / "settings.json").write_text('{"check_updates": false}', encoding="utf-8")
    monkeypatch.setattr(update, "data_dir", lambda: folder)
