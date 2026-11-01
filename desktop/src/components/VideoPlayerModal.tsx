import React from 'react';
import { X, Play, AlertCircle } from 'lucide-react';

interface VideoPlayerModalProps {
  isOpen: boolean;
  onClose: () => void;
  mediaUrl: string;
  title: string;
  format?: string;
  duration?: number;
}

export const VideoPlayerModal: React.FC<VideoPlayerModalProps> = ({
  isOpen,
  onClose,
  mediaUrl,
  title,
  format,
  duration,
}) => {
  const [hasError, setHasError] = React.useState(false);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4">
      <div className="relative w-full max-w-4xl bg-slate-900 border border-slate-700 rounded-xl overflow-hidden shadow-2xl flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3.5 border-b border-slate-800 bg-slate-950">
          <div className="flex items-center space-x-2 text-slate-200 truncate">
            <Play className="w-4 h-4 text-emerald-400 shrink-0" />
            <span className="font-medium text-sm truncate">{title}</span>
            {format && (
              <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-slate-800 text-slate-300 uppercase">
                {format}
              </span>
            )}
            {duration ? (
              <span className="text-xs text-slate-400">
                ({Math.floor(duration / 60)}:{(Math.floor(duration % 60)).toString().padStart(2, '0')})
              </span>
            ) : null}
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded-lg text-slate-400 hover:text-white hover:bg-slate-800 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Video Canvas */}
        <div className="relative bg-black flex-1 flex items-center justify-center min-h-[400px]">
          {hasError ? (
            <div className="text-center p-8 text-rose-400 space-y-2">
              <AlertCircle className="w-12 h-12 mx-auto" />
              <p className="font-semibold text-sm">Failed to stream video playback.</p>
              <p className="text-xs text-slate-400 max-w-md">
                The video stream could not be loaded via mediaforge-media:// protocol. Ensure the file was completely
                cached.
              </p>
            </div>
          ) : (
            <video
              src={mediaUrl}
              controls
              autoPlay
              className="max-h-[70vh] w-full h-auto object-contain"
              onError={() => setHasError(true)}
            />
          )}
        </div>

        {/* Footer info */}
        <div className="px-5 py-2.5 bg-slate-950 border-t border-slate-800 flex items-center justify-between text-xs text-slate-400">
          <span>Streaming via local isolated sandbox protocol (mediaforge-media://)</span>
          <button
            onClick={onClose}
            className="px-3 py-1 bg-slate-800 hover:bg-slate-700 text-white rounded font-medium transition-colors"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
};
