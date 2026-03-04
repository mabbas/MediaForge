from src.core.provider_factory import create_provider_registry
from src.models.enums import ProviderType

registry = create_provider_registry()
print(f"Media providers: {registry.media_provider_count}")
print(f"File providers: {registry.file_provider_count}")

providers = registry.list_providers()
for p in providers:
    print(f"  {p['name']} ({p['type']}) - {p['kind']}")

yt = registry.detect_provider("https://youtube.com/watch?v=abc")
print(f"YouTube URL → {yt.name}")

generic = registry.detect_provider("https://random.com/video")
print(f"Random URL → {generic.name}")

yt2 = registry.get_provider(ProviderType.YOUTUBE)
print(f"Direct access: {yt2.name}")
print("FACTORY OK")