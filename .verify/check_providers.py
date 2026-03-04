from src.core.provider_registry import ProviderRegistry
from src.providers.youtube.provider import YouTubeProvider
from src.providers.generic.provider import GenericProvider
from src.models.enums import ProviderType

yt = YouTubeProvider()
print(f"Name: {yt.name}")
print(f"Type: {yt.provider_type}")
print(f"Playlists: {yt.capabilities.supports_playlists}")
print(f"Subtitles: {yt.capabilities.supports_subtitles}")
print(f"Domains: {yt.capabilities.supported_domains[:3]}...")

urls_yes = [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtu.be/dQw4w9WgXcQ",
    "https://m.youtube.com/watch?v=test",
    "https://music.youtube.com/watch?v=test",
]
urls_no = [
    "https://vimeo.com/123",
    "https://facebook.com/video",
    "https://example.com",
]
for url in urls_yes:
    assert yt.can_handle(url), f"Should handle: {url}"
for url in urls_no:
    assert not yt.can_handle(url), f"Should NOT handle: {url}"
print("All URL checks passed")

clean = yt.sanitize_filename('My Video: Test/File <2024> \"quotes\"')
print(f"Sanitized: {clean}")

gen = GenericProvider()
print(f"Generic handles anything: {gen.can_handle('https://anything.com')}")
print("YOUTUBE PROVIDER OK")