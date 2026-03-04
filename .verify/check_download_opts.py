from src.providers.youtube.provider import YouTubeProvider
from src.models import DownloadRequest, MediaType, AudioFormat, Quality

p = YouTubeProvider()

req = DownloadRequest(
    url="https://youtube.com/watch?v=test",
    media_type=MediaType.AUDIO,
    audio_format=AudioFormat.MP3,
    audio_bitrate="320k",
)
opts = p._build_download_opts(req, "/tmp", None)
print(f"Audio format: {opts['format']}")
has_extract = any(pp["key"] == "FFmpegExtractAudio" for pp in opts.get("postprocessors", []))
print(f"Has audio extractor: {has_extract}")

req2 = DownloadRequest(
    url="https://youtube.com/watch?v=test",
    media_type=MediaType.VIDEO,
    quality=Quality.Q_720P,
)
opts2 = p._build_download_opts(req2, "/tmp", None)
print(f"Video format: {opts2['format'][:50]}...")
print(f"Merge format: {opts2['merge_output_format']}")
print("BUILD OPTS OK")