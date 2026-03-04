from desktop.bridge import JSBridge

def test_bridge_init():
    bridge = JSBridge()
    assert bridge._app is None

def test_bridge_get_app_version():
    bridge = JSBridge()
    info = bridge.get_app_version()
    assert info['name'] == 'GrabItDown'
    assert 'version' in info

def test_bridge_get_system_info():
    bridge = JSBridge()
    info = bridge.get_system_info()
    assert 'platform' in info
    assert 'cpu_count' in info
    assert 'ram_total_gb' in info

def test_bridge_open_file_nonexistent():
    bridge = JSBridge()
    assert bridge.open_file('/nonexistent/path') is False

def test_bridge_open_folder_nonexistent():
    bridge = JSBridge()
    assert bridge.open_folder('/nonexistent/path') is False

def test_bridge_get_settings_no_app():
    bridge = JSBridge()
    assert bridge.get_settings() == {}
