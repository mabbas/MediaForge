from desktop.updater import UpdateChecker


def test_updater_init():
    updater = UpdateChecker(current_version="0.1.0")
    assert updater._current == "0.1.0"
    assert updater.update_available is False
    assert updater.latest_version is None


def test_updater_version_comparison():
    updater = UpdateChecker(current_version="0.1.0")
    updater._latest = "0.2.0"
    assert updater.update_available is True


def test_updater_same_version():
    updater = UpdateChecker(current_version="0.1.0")
    updater._latest = "0.1.0"
    assert updater.update_available is False


def test_updater_newer_current():
    updater = UpdateChecker(current_version="1.0.0")
    updater._latest = "0.9.0"
    assert updater.update_available is False
