from src.features.feature_flags import load_feature_flags
from src.features.feature_gate import FeatureGate

flags = load_feature_flags()
gate = FeatureGate(flags)

print(f"Mode: {flags.mode}")
print(f"Tiers: {list(flags.tiers.keys())}")
print(f"Video enabled: {gate.is_enabled('video_download')}")
print(f"Max quality (personal): {gate.get_limit('video_download', 'max_quality')}")
print(f"Max quality (basic): {gate.get_limit('video_download', 'max_quality', tier='basic')}")
print(f"Playlist (basic): {gate.is_enabled('playlist_download', tier='basic')}")
print(f"Playlist (pro): {gate.is_enabled('playlist_download', tier='pro')}")
access = gate.check_access('multi_connection', tier='basic')
print(f"Multi-conn basic: allowed={access.allowed}, reason={access.reason}")
all_features = gate.list_all_features(tier='basic')
print(f"Basic features: {sum(1 for v in all_features.values() if v)} enabled")
print("FEATURES OK")