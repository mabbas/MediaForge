from src.models import (
    MediaInfo, MediaFormat, Thumbnail,
    DownloadRequest, DownloadProgress, DownloadResult, DownloadJob,
    PlaylistInfo, PlaylistItem, PlaylistDownloadRequest,
    TranscriptSegment, TranscriptRequest, TranscriptResult,
    MediaType, Quality, DownloadStatus, ProviderType,
    AudioFormat, VideoFormat, TranscriptFormat, TranscriptSource,
)

print("All models importable from src.models")

fmt = MediaFormat(
    format_id="137",
    extension="mp4",
    filesize_bytes=1073741824,
    has_video=True,
    has_audio=False,
)
print(f"Size: {fmt.filesize_human}")
print(f"Video only: {fmt.is_video_only}")

info = MediaInfo(
    url="https://youtube.com/watch?v=test",
    provider=ProviderType.YOUTUBE,
    media_id="test",
    title="GrabItDown Test",
    duration_seconds=5025,
    thumbnails=[
        Thumbnail(url="small.jpg", width=120, height=90),
        Thumbnail(url="large.jpg", width=1280, height=720),
    ],
    subtitles_available={"en": ["srt", "vtt"], "ur": ["srt"]},
)
print(f"Duration: {info.duration_human}")
print(f"Best thumb: {info.best_thumbnail.width}")
print(f"Has EN: {info.has_subtitles('en')}")
print(f"Has FR: {info.has_subtitles('fr')}")

progress = DownloadProgress(
    job_id="test",
    status=DownloadStatus.DOWNLOADING,
    speed_bytes_per_second=5242880,
    eta_seconds=150,
    percent=63.5,
)
print(f"Speed: {progress.speed_human}")
print(f"ETA: {progress.eta_human}")
print(f"Percent: {progress.percent_display}")

seg = TranscriptSegment(
    start_seconds=83.456,
    end_seconds=87.123,
    text="Hello",
)
print(f"Timestamp: {seg.start_timestamp}")

job = DownloadJob(
    request=DownloadRequest(url="https://youtube.com/watch?v=test"),
    progress=DownloadProgress(job_id="test", status=DownloadStatus.CREATED),
)
print(f"Job ID length: {len(job.job_id)}")
print(f"User: {job.user_id}")
print(f"Tenant: {job.tenant_id}")
print("ALL MODELS OK")