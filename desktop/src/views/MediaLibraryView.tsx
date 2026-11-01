import React, { useEffect, useState } from 'react';
import { api } from '../api/client';
import { MediaArtifact, DownloadJob } from '../types';
import { StatusBadge } from '../components/StatusBadge';
import { VideoPlayerModal } from '../components/VideoPlayerModal';
import { useAuth } from '../context/AuthContext';
import { NavView } from '../components/Layout';
import {
  Film,
  Download,
  Play,
  Scissors,
  Layers,
  RefreshCw,
  Search,
  CheckCircle2,
  Loader2,
  AlertCircle,
} from 'lucide-react';

interface MediaLibraryViewProps {
  onSelectForClip: (sourcePath: string, duration?: number) => void;
  onSelectForMerge: (clipIds: string[]) => void;
  onNavigate: (view: NavView) => void;
}

interface UnifiedMediaItem {
  id: string;
  sourceType: 'artifact' | 'download';
  title: string;
  filename: string;
  format: string;
  duration: number;
  sizeHuman: string;
  sizeBytes: number;
  status: string;
  createdAt: string;
  sha256?: string;
  downloadUrl: string;
  rawItem: MediaArtifact | DownloadJob;
}

export const MediaLibraryView: React.FC<MediaLibraryViewProps> = ({
  onSelectForClip,
  onSelectForMerge,
  onNavigate,
}) => {
  const { user } = useAuth();
  const [items, setItems] = useState<UnifiedMediaItem[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [typeFilter, setTypeFilter] = useState<'all' | 'clip' | 'merge' | 'download'>('all');
  const [selectedMergeClipIds, setSelectedMergeClipIds] = useState<string[]>([]);
  const [cachedMap, setCachedMap] = useState<Record<string, { isCached: boolean; mediaUrl?: string }>>({});
  const [downloadingMap, setDownloadingMap] = useState<Record<string, number>>({});
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  // Player state
  const [playerModal, setPlayerModal] = useState<{
    isOpen: boolean;
    mediaUrl: string;
    title: string;
    format: string;
    duration?: number;
  }>({
    isOpen: false,
    mediaUrl: '',
    title: '',
    format: '',
  });

  const loadMedia = async () => {
    setIsLoading(true);
    setErrorMessage(null);
    try {
      const [artifactsRes, downloadsRes] = await Promise.allSettled([
        api.listArtifacts({ limit: 50 }),
        api.listDownloads(),
      ]);

      const unified: UnifiedMediaItem[] = [];

      if (artifactsRes.status === 'fulfilled') {
        for (const art of artifactsRes.value.items) {
          unified.push({
            id: art.id,
            sourceType: 'artifact',
            title: art.filename,
            filename: art.filename,
            format: art.format,
            duration: art.duration_seconds,
            sizeHuman: art.file_size_human,
            sizeBytes: art.file_size_bytes,
            status: art.status,
            createdAt: art.created_at,
            sha256: art.sha256_checksum || undefined,
            downloadUrl: api.getArtifactDownloadUrl(art.id),
            rawItem: art,
          });
        }
      }

      if (downloadsRes.status === 'fulfilled' && downloadsRes.value.jobs) {
        for (const dl of downloadsRes.value.jobs) {
          unified.push({
            id: dl.id,
            sourceType: 'download',
            title: dl.title || dl.filename || 'Downloaded Media',
            filename: dl.filename || `download_${dl.id}.mp4`,
            format: dl.format || 'mp4',
            duration: dl.duration || 0,
            sizeHuman: dl.file_size_human || '0 B',
            sizeBytes: dl.file_size || 0,
            status: dl.status,
            createdAt: dl.created_at || new Date().toISOString(),
            downloadUrl: api.getDownloadFileUrl(dl.id),
            rawItem: dl,
          });
        }
      }

      setItems(unified);

      // Check tenant-isolated local cache state for each item
      if (window.mediaforge && user) {
        const cacheChecks: Record<string, { isCached: boolean; mediaUrl?: string }> = {};
        for (const item of unified) {
          const res = await window.mediaforge.media.checkCache(item.id, user.tenant_id);
          cacheChecks[item.id] = res;
        }
        setCachedMap(cacheChecks);
      }
    } catch (err: any) {
      setErrorMessage(err.message || 'Failed to load media library');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadMedia();

    // Listen to download progress
    if (window.mediaforge) {
      const unsubscribe = window.mediaforge.media.onDownloadProgress((data) => {
        setDownloadingMap((prev) => ({
          ...prev,
          [data.artifactId]: data.percent,
        }));
      });
      return () => unsubscribe();
    }
  }, [user?.tenant_id]);

  const handleDownload = async (item: UnifiedMediaItem) => {
    if (!window.mediaforge || !user) return;
    setDownloadingMap((prev) => ({ ...prev, [item.id]: 0 }));
    try {
      const res = await window.mediaforge.media.download({
        artifactId: item.id,
        downloadUrl: item.downloadUrl,
        filename: item.filename,
        tenantId: user.tenant_id,
        expectedSha256: item.sha256,
      });

      if (res.success) {
        setCachedMap((prev) => ({
          ...prev,
          [item.id]: { isCached: true, mediaUrl: res.mediaUrl },
        }));
      }
    } catch (err: any) {
      alert(`Download error: ${err.message}`);
    } finally {
      setDownloadingMap((prev) => {
        const next = { ...prev };
        delete next[item.id];
        return next;
      });
    }
  };

  const handlePlay = (item: UnifiedMediaItem) => {
    const cache = cachedMap[item.id];
    if (cache && cache.isCached && cache.mediaUrl) {
      setPlayerModal({
        isOpen: true,
        mediaUrl: cache.mediaUrl,
        title: item.title,
        format: item.format,
        duration: item.duration,
      });
    }
  };

  const handleStartClip = (item: UnifiedMediaItem) => {
    const sourceIdentifier =
      item.sourceType === 'artifact'
        ? (item.rawItem as MediaArtifact).storage_path || item.id
        : item.filename ? `downloads/${item.filename}` : item.id;
    onSelectForClip(sourceIdentifier, item.duration);
    onNavigate('clip');
  };

  const toggleMergeSelect = (id: string) => {
    setSelectedMergeClipIds((prev) =>
      prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id]
    );
  };

  const handleLaunchMerge = () => {
    if (selectedMergeClipIds.length < 2) return;
    onSelectForMerge(selectedMergeClipIds);
    onNavigate('merge');
  };

  const filteredItems = items.filter((item) => {
    const matchesSearch =
      item.title.toLowerCase().includes(search.toLowerCase()) ||
      item.id.toLowerCase().includes(search.toLowerCase());
    if (!matchesSearch) return false;

    if (typeFilter === 'clip') {
      return item.sourceType === 'artifact' && (item.rawItem as MediaArtifact).artifact_type === 'clip';
    }
    if (typeFilter === 'merge') {
      return item.sourceType === 'artifact' && (item.rawItem as MediaArtifact).artifact_type === 'merge';
    }
    if (typeFilter === 'download') {
      return item.sourceType === 'download';
    }
    return true;
  });

  return (
    <div className="space-y-6 max-w-6xl mx-auto">
      {/* Top Filter and Actions Bar */}
      <div className="flex flex-col md:flex-row items-center justify-between gap-4 bg-slate-950/60 p-4 rounded-xl border border-slate-800">
        <div className="flex items-center space-x-3 w-full md:w-auto">
          <div className="relative flex-1 md:w-64">
            <Search className="w-4 h-4 absolute left-3 top-3 text-slate-500" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search filename or ID..."
              className="w-full pl-9 pr-3 py-1.5 bg-slate-900 border border-slate-700 rounded-lg text-xs text-white placeholder-slate-500 focus:outline-none focus:border-emerald-500 transition-colors"
            />
          </div>

          <div className="flex items-center space-x-1 bg-slate-900 p-1 rounded-lg border border-slate-700 text-xs">
            {(['all', 'clip', 'merge', 'download'] as const).map((t) => (
              <button
                key={t}
                onClick={() => setTypeFilter(t)}
                className={`px-2.5 py-1 rounded capitalize font-medium transition-colors ${
                  typeFilter === t
                    ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30'
                    : 'text-slate-400 hover:text-white'
                }`}
              >
                {t}
              </button>
            ))}
          </div>
        </div>

        <div className="flex items-center space-x-3 w-full md:w-auto justify-end">
          {selectedMergeClipIds.length >= 2 && (
            <button
              onClick={handleLaunchMerge}
              className="flex items-center space-x-1.5 px-3.5 py-1.5 bg-purple-500 hover:bg-purple-600 text-slate-950 rounded-lg text-xs font-semibold transition-colors shadow-md shadow-purple-500/20"
            >
              <Layers className="w-3.5 h-3.5" />
              <span>Merge Selected ({selectedMergeClipIds.length})</span>
            </button>
          )}

          <button
            onClick={loadMedia}
            disabled={isLoading}
            className="flex items-center space-x-2 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg text-xs font-medium border border-slate-700 transition-colors shrink-0"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? 'animate-spin' : ''}`} />
            <span>Refresh</span>
          </button>
        </div>
      </div>

      {errorMessage && (
        <div className="p-4 rounded-xl bg-rose-500/10 border border-rose-500/30 flex items-center space-x-3 text-rose-400 text-xs">
          <AlertCircle className="w-4 h-4 shrink-0" />
          <span>{errorMessage}</span>
        </div>
      )}

      {/* Media Table */}
      {filteredItems.length === 0 ? (
        <div className="text-center py-16 bg-slate-900/30 border border-slate-800/80 rounded-2xl">
          <Film className="w-12 h-12 text-slate-600 mx-auto mb-3" />
          <h3 className="text-base font-semibold text-slate-300">No Media Found</h3>
          <p className="text-xs text-slate-500 mt-1 max-w-sm mx-auto mb-4">
            No artifacts or completed downloads match your criteria.
          </p>
          <div className="flex items-center justify-center space-x-3">
            <button
              onClick={() => onNavigate('downloads')}
              className="px-3.5 py-1.5 bg-emerald-500 hover:bg-emerald-600 text-slate-950 rounded-lg text-xs font-semibold flex items-center space-x-1.5"
            >
              <Download className="w-3.5 h-3.5" />
              <span>Download a Video</span>
            </button>
            <button
              onClick={() => onNavigate('clip')}
              className="px-3.5 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-xs font-medium border border-slate-700"
            >
              <Scissors className="w-3.5 h-3.5 text-purple-400" />
              <span>Create Clip</span>
            </button>
          </div>
        </div>
      ) : (
        <div className="bg-slate-900/40 border border-slate-800 rounded-xl overflow-hidden shadow-sm">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="bg-slate-950/70 border-b border-slate-800 text-slate-400 uppercase font-semibold tracking-wider">
                <tr>
                  <th className="px-3 py-3 w-8">
                    <span className="sr-only">Select</span>
                  </th>
                  <th className="px-4 py-3">Media Item</th>
                  <th className="px-4 py-3">Type</th>
                  <th className="px-4 py-3">Duration</th>
                  <th className="px-4 py-3">Size</th>
                  <th className="px-4 py-3">Status</th>
                  <th className="px-4 py-3">Local Cache</th>
                  <th className="px-4 py-3 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60">
                {filteredItems.map((item) => {
                  const isCached = cachedMap[item.id]?.isCached;
                  const downloadProgress = downloadingMap[item.id];
                  const isDownloading = downloadProgress !== undefined;
                  const isClip =
                    item.sourceType === 'artifact' &&
                    (item.rawItem as MediaArtifact).artifact_type === 'clip';
                  const isSelectedForMerge = selectedMergeClipIds.includes(item.id);

                  return (
                    <tr
                      key={item.id}
                      className={`hover:bg-slate-800/30 transition-colors ${
                        isSelectedForMerge ? 'bg-purple-500/10' : ''
                      }`}
                    >
                      <td className="px-3 py-3.5 text-center">
                        {isClip ? (
                          <input
                            type="checkbox"
                            checked={isSelectedForMerge}
                            onChange={() => toggleMergeSelect(item.id)}
                            className="rounded border-slate-700 bg-slate-900 text-purple-500 focus:ring-0"
                            title="Select for merge"
                          />
                        ) : null}
                      </td>
                      <td className="px-4 py-3.5">
                        <div className="font-medium text-slate-200 truncate max-w-xs">{item.title}</div>
                        <div className="text-[10px] text-slate-500 font-mono mt-0.5 truncate max-w-xs">ID: {item.id}</div>
                      </td>
                      <td className="px-4 py-3.5">
                        <span className="px-2 py-0.5 rounded text-[10px] font-semibold bg-slate-800 text-slate-300 uppercase">
                          {item.sourceType === 'artifact'
                            ? (item.rawItem as MediaArtifact).artifact_type
                            : 'download'}
                        </span>
                      </td>
                      <td className="px-4 py-3.5 text-slate-300">
                        {item.duration > 0
                          ? `${Math.floor(item.duration / 60)}:${Math.floor(item.duration % 60)
                              .toString()
                              .padStart(2, '0')}`
                          : '--:--'}
                      </td>
                      <td className="px-4 py-3.5 text-slate-300">{item.sizeHuman}</td>
                      <td className="px-4 py-3.5">
                        <StatusBadge status={item.status} />
                      </td>
                      <td className="px-4 py-3.5">
                        {isCached ? (
                          <div className="flex items-center space-x-1.5 text-emerald-400">
                            <CheckCircle2 className="w-3.5 h-3.5" />
                            <span className="text-[11px] font-medium">Cached</span>
                          </div>
                        ) : isDownloading ? (
                          <div className="w-24">
                            <div className="flex justify-between text-[10px] text-slate-400 mb-1">
                              <span>Downloading</span>
                              <span>{downloadProgress}%</span>
                            </div>
                            <div className="w-full bg-slate-800 h-1 rounded-full overflow-hidden">
                              <div
                                className="bg-emerald-500 h-full transition-all duration-300"
                                style={{ width: `${downloadProgress}%` }}
                              />
                            </div>
                          </div>
                        ) : (
                          <span className="text-[11px] text-slate-500">Not Cached</span>
                        )}
                      </td>
                      <td className="px-4 py-3.5 text-right space-x-2">
                        {isCached ? (
                          <button
                            onClick={() => handlePlay(item)}
                            className="inline-flex items-center space-x-1 px-2.5 py-1 bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-400 rounded-lg text-xs font-medium border border-emerald-500/30 transition-colors"
                          >
                            <Play className="w-3 h-3 fill-emerald-400" />
                            <span>Play</span>
                          </button>
                        ) : (
                          <button
                            onClick={() => handleDownload(item)}
                            disabled={isDownloading || !['ready', 'completed'].includes(item.status)}
                            className="inline-flex items-center space-x-1 px-2.5 py-1 bg-slate-800 hover:bg-slate-700 disabled:opacity-50 text-slate-300 rounded-lg text-xs font-medium border border-slate-700 transition-colors"
                          >
                            {isDownloading ? (
                              <Loader2 className="w-3 h-3 animate-spin text-emerald-400" />
                            ) : (
                              <Download className="w-3 h-3" />
                            )}
                            <span>{isDownloading ? 'Saving...' : 'Download'}</span>
                          </button>
                        )}

                        <button
                          onClick={() => handleStartClip(item)}
                          className="inline-flex items-center space-x-1 px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-purple-400 rounded-lg text-xs font-medium border border-slate-700 transition-colors"
                          title="Extract Clip from this video"
                        >
                          <Scissors className="w-3 h-3" />
                          <span>Clip</span>
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Video Player Modal */}
      <VideoPlayerModal
        isOpen={playerModal.isOpen}
        onClose={() => setPlayerModal((prev) => ({ ...prev, isOpen: false }))}
        mediaUrl={playerModal.mediaUrl}
        title={playerModal.title}
        format={playerModal.format}
        duration={playerModal.duration}
      />
    </div>
  );
};
