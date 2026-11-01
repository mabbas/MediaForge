import React, { useEffect, useState } from 'react';
import { api, ApiError } from '../api/client';
import { TranscriptItem, ExtractTranscriptRequest } from '../types';
import {
  FileText,
  Download,
  Copy,
  Check,
  Languages,
  Sparkles,
  AlertCircle,
  RefreshCw,
  Loader2,
} from 'lucide-react';

export const TranscriptView: React.FC = () => {
  const [url, setUrl] = useState('');
  const [language, setLanguage] = useState('en');
  const [availableLanguages, setAvailableLanguages] = useState<string[]>(['en']);
  const [format, setFormat] = useState<'srt' | 'vtt' | 'txt' | 'json'>('srt');
  const [script, setScript] = useState<'default' | 'roman' | 'native'>('default');
  const [extractedContent, setExtractedContent] = useState<string | null>(null);
  const [transcriptsHistory, setTranscriptsHistory] = useState<TranscriptItem[]>([]);
  const [isExtracting, setIsExtracting] = useState(false);
  const [isFetchingLanguages, setIsFetchingLanguages] = useState(false);
  const [copied, setCopied] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const fetchHistory = async () => {
    try {
      const res = await api.listTranscripts({ page: 1, page_size: 20 });
      setTranscriptsHistory(res.transcripts || []);
    } catch {
      // Ignore
    }
  };

  useEffect(() => {
    fetchHistory();
  }, []);

  const handleFetchLanguages = async () => {
    if (!url.trim()) return;
    setIsFetchingLanguages(true);
    setErrorMessage(null);
    try {
      const res = await api.listTranscriptLanguages(url.trim());
      const langs = Object.keys(res.languages || {});
      if (langs.length > 0) {
        setAvailableLanguages(langs);
        if (!langs.includes(language)) {
          setLanguage(langs[0]);
        }
      }
    } catch (err: any) {
      setErrorMessage(err.message || 'Could not fetch available languages for this video');
    } finally {
      setIsFetchingLanguages(false);
    }
  };

  const handleExtract = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!url.trim()) return;

    setIsExtracting(true);
    setErrorMessage(null);
    setExtractedContent(null);

    try {
      const req: ExtractTranscriptRequest = {
        url: url.trim(),
        language,
        format,
        script: script !== 'default' ? script : undefined,
      };

      const res = await api.extractTranscript(req);
      if (res && res.content) {
        setExtractedContent(res.content);
        await fetchHistory();
      }
    } catch (err: any) {
      const msg = err instanceof ApiError ? err.detail : err.message || 'Transcript extraction failed';
      setErrorMessage(msg);
    } finally {
      setIsExtracting(false);
    }
  };

  const handleCopy = () => {
    if (!extractedContent) return;
    navigator.clipboard.writeText(extractedContent);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleDownloadFile = () => {
    if (!extractedContent) return;
    const blob = new Blob([extractedContent], { type: 'text/plain;charset=utf-8' });
    const blobUrl = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = blobUrl;
    a.download = `transcript_${language}_${Date.now()}.${format}`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(blobUrl);
  };

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      {/* Header */}
      <div>
        <h2 className="text-xl font-bold text-white flex items-center space-x-2">
          <FileText className="w-5 h-5 text-teal-400" />
          <span>Video Subtitles & Transcripts</span>
        </h2>
        <p className="text-xs text-slate-400 mt-1">
          Extract, romanize, and export subtitles and captions directly from video sources.
        </p>
      </div>

      {/* AI Whisper Capability Notice */}
      <div className="p-4 rounded-xl bg-slate-950/60 border border-slate-800 flex items-start space-x-3 text-xs text-slate-400">
        <Sparkles className="w-4 h-4 text-purple-400 shrink-0 mt-0.5" />
        <div>
          <span className="font-semibold text-slate-200">Subtitle Extraction Engine: </span>
          Currently extracting official and auto-generated captions via server-side yt-dlp. Deep neural Whisper AI
          speech-to-text is scheduled for server integration in an upcoming backend milestone.
        </div>
      </div>

      {/* Error Alert */}
      {errorMessage && (
        <div className="p-4 rounded-xl bg-rose-500/10 border border-rose-500/30 flex items-start space-x-3 text-rose-400 text-xs">
          <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
          <div>
            <span className="font-semibold">Error: </span>
            {errorMessage}
          </div>
        </div>
      )}

      {/* Extraction Form */}
      <div className="bg-slate-950/60 border border-slate-800 rounded-xl p-5 space-y-4">
        <div className="flex items-center space-x-2 text-slate-200 font-semibold text-sm">
          <Languages className="w-4 h-4 text-teal-400" />
          <span>Extract Subtitles</span>
        </div>

        <form onSubmit={handleExtract} className="space-y-4">
          <div>
            <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-1.5">
              Source Video URL
            </label>
            <div className="flex items-center space-x-2">
              <input
                type="url"
                required
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://www.youtube.com/watch?v=..."
                className="flex-1 px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-xs text-white placeholder-slate-500 focus:outline-none focus:border-teal-500 font-mono transition-colors"
              />
              <button
                type="button"
                onClick={handleFetchLanguages}
                disabled={isFetchingLanguages || !url.trim()}
                className="px-3.5 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-xs font-medium border border-slate-700 transition-colors shrink-0"
              >
                {isFetchingLanguages ? 'Scanning...' : 'Detect Languages'}
              </button>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs">
            <div>
              <label className="block font-semibold text-slate-300 mb-1">Language</label>
              <select
                value={language}
                onChange={(e) => setLanguage(e.target.value)}
                className="w-full px-2.5 py-1.5 bg-slate-900 border border-slate-700 rounded-lg text-white focus:outline-none focus:border-teal-500"
              >
                {availableLanguages.map((lang) => (
                  <option key={lang} value={lang}>
                    {lang.toUpperCase()}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="block font-semibold text-slate-300 mb-1">Format</label>
              <select
                value={format}
                onChange={(e) => setFormat(e.target.value as any)}
                className="w-full px-2.5 py-1.5 bg-slate-900 border border-slate-700 rounded-lg text-white focus:outline-none focus:border-teal-500"
              >
                <option value="srt">SubRip (.srt)</option>
                <option value="vtt">WebVTT (.vtt)</option>
                <option value="txt">Plain Text (.txt)</option>
                <option value="json">JSON (.json)</option>
              </select>
            </div>

            <div>
              <label className="block font-semibold text-slate-300 mb-1">Script / Romanization</label>
              <select
                value={script}
                onChange={(e) => setScript(e.target.value as any)}
                className="w-full px-2.5 py-1.5 bg-slate-900 border border-slate-700 rounded-lg text-white focus:outline-none focus:border-teal-500"
              >
                <option value="default">Default Script</option>
                <option value="roman">Romanized / Latin</option>
                <option value="native">Native Alphabet</option>
              </select>
            </div>
          </div>

          <button
            type="submit"
            disabled={isExtracting}
            className="px-4 py-2 bg-teal-500 hover:bg-teal-600 disabled:opacity-50 text-slate-950 font-semibold rounded-lg text-xs flex items-center space-x-2 transition-colors shadow-md shadow-teal-500/20"
          >
            {isExtracting ? (
              <>
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
                <span>Extracting Transcript...</span>
              </>
            ) : (
              <>
                <FileText className="w-3.5 h-3.5" />
                <span>Extract Transcript</span>
              </>
            )}
          </button>
        </form>
      </div>

      {/* Extracted Content Viewer */}
      {extractedContent && (
        <div className="bg-slate-950/60 border border-slate-800 rounded-xl overflow-hidden shadow-sm space-y-0">
          <div className="px-5 py-3 border-b border-slate-800 bg-slate-900/60 flex items-center justify-between">
            <span className="text-xs font-semibold text-slate-200 uppercase tracking-wider">
              Transcript Output ({format.toUpperCase()})
            </span>
            <div className="flex items-center space-x-2">
              <button
                onClick={handleCopy}
                className="inline-flex items-center space-x-1 px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded text-xs transition-colors"
              >
                {copied ? <Check className="w-3 h-3 text-emerald-400" /> : <Copy className="w-3 h-3" />}
                <span>{copied ? 'Copied!' : 'Copy'}</span>
              </button>
              <button
                onClick={handleDownloadFile}
                className="inline-flex items-center space-x-1 px-2.5 py-1 bg-teal-500/15 hover:bg-teal-500/25 text-teal-400 rounded text-xs transition-colors"
              >
                <Download className="w-3 h-3" />
                <span>Export File</span>
              </button>
            </div>
          </div>
          <div className="p-4 bg-slate-950 max-h-96 overflow-y-auto">
            <pre className="text-xs text-slate-300 font-mono whitespace-pre-wrap select-text leading-relaxed">
              {extractedContent}
            </pre>
          </div>
        </div>
      )}

      {/* Transcript History */}
      <div className="bg-slate-950/60 border border-slate-800 rounded-xl overflow-hidden shadow-sm">
        <div className="px-5 py-3 border-b border-slate-800 bg-slate-900/60 flex items-center justify-between">
          <h3 className="text-xs font-semibold text-slate-200 uppercase tracking-wider">Transcript History</h3>
          <button
            onClick={fetchHistory}
            className="p-1 rounded text-slate-400 hover:text-white"
            title="Refresh History"
          >
            <RefreshCw className="w-3 h-3" />
          </button>
        </div>

        {transcriptsHistory.length === 0 ? (
          <div className="text-center py-8 text-slate-500 text-xs">
            No saved transcripts found for this account.
          </div>
        ) : (
          <div className="divide-y divide-slate-800/60">
            {transcriptsHistory.map((t) => (
              <div key={t.id} className="px-4 py-3 flex items-center justify-between hover:bg-slate-800/20 text-xs">
                <div>
                  <div className="font-mono text-slate-300">{t.url}</div>
                  <div className="text-[10px] text-slate-500 flex items-center space-x-2 mt-0.5">
                    <span className="uppercase font-semibold text-teal-400">{t.language}</span>
                    <span>•</span>
                    <span>{t.format.toUpperCase()}</span>
                    <span>•</span>
                    <span>{t.word_count} words</span>
                  </div>
                </div>
                <div className="text-slate-500 text-[10px] font-mono">
                  {t.created_at ? new Date(t.created_at).toLocaleDateString() : ''}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};
