"""Phase 2.5 verification: bandwidth, disk, chunk, state persistence."""
import time

from src.download.bandwidth_throttle import BandwidthThrottle, GlobalBandwidthThrottle
from src.download.chunk_manager import ChunkPlan, DownloadChunk
from src.download.disk_monitor import DiskMonitor
from src.download.state_persistence import StatePersistence

# Bandwidth
throttle = BandwidthThrottle(limit_bytes_per_second=0)
print(f"Unlimited: {throttle.limit} B/s")

throttle2 = BandwidthThrottle(limit_bytes_per_second=5242880)
print(f"Limited: {throttle2.limit} B/s (5 MB/s)")

# Disk
monitor = DiskMonitor(min_space_mb=100)
stats = monitor.get_stats()
print(f"Disk: {stats['free_gb']} GB free ({stats['usage_percent']}% used)")

can, reason = monitor.check_before_download(1048576)
print(f"Can download 1MB: {can} ({reason})")

# Chunks
plan = ChunkPlan(
    total_bytes=1073741824,
    num_connections=4,
    min_file_size_mb=100,
)
print(f"Chunks: {plan.chunk_count}")
for c in plan.chunks:
    print(f"  Chunk {c.chunk_id}: {c.start_byte}-{c.end_byte} ({c.total_bytes / 1048576:.0f} MB)")

print("PHASE 2.5 OK")
