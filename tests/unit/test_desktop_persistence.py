from desktop.persistence import load_desktop_prefs, load_window_state, save_desktop_prefs, save_window_state

def test_save_load_window_state(tmp_path):
    state = {'width': 1000, 'height': 700, 'x': 100, 'y': 50}
    assert save_window_state(state, str(tmp_path))
    loaded = load_window_state(str(tmp_path))
    assert loaded['width'] == 1000
    assert loaded['y'] == 50

def test_save_load_preferences(tmp_path):
    prefs = {'clipboard_enabled': False, 'theme': 'dark'}
    assert save_desktop_prefs(prefs, str(tmp_path))
    loaded = load_desktop_prefs(str(tmp_path))
    assert loaded['clipboard_enabled'] is False

def test_preferences_merge(tmp_path):
    save_desktop_prefs({'key1': 'val1'}, str(tmp_path))
    save_desktop_prefs({'key2': 'val2'}, str(tmp_path))
    loaded = load_desktop_prefs(str(tmp_path))
    assert loaded['key1'] == 'val1'
    assert loaded['key2'] == 'val2'

def test_load_nonexistent(tmp_path):
    assert load_window_state(str(tmp_path)) is None
    assert load_desktop_prefs(str(tmp_path)) is None
