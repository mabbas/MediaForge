"use client";

import { useState, useEffect } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Label } from "@/components/ui/label";
import { Download, Loader2, Languages } from "lucide-react";
import api from "@/lib/api-client";

const LANGUAGE_OPTIONS = [
  { value: "en", label: "English" },
  { value: "es", label: "Spanish" },
  { value: "fr", label: "French" },
  { value: "de", label: "German" },
];

export function TranscriptForm({ initialUrl }: { initialUrl?: string }) {
  const [url, setUrl] = useState(initialUrl ?? "");
  const [language, setLanguage] = useState("en");

  useEffect(() => {
    if (initialUrl) setUrl(initialUrl);
  }, [initialUrl]);
  const [loading, setLoading] = useState(false);
  const [detectedLangs, setDetectedLangs] = useState<Record<string, string[]> | null>(null);
  const [detecting, setDetecting] = useState(false);

  const detectLanguages = async () => {
    const trimmed = url.trim();
    if (!trimmed) return;
    setDetecting(true);
    try {
      const res = await api.listLanguages(trimmed);
      setDetectedLangs(res.languages ?? null);
      const langs = Object.keys(res.languages || {});
      if (langs.length > 0) {
        toast.success("Found " + langs.length + " languages");
        if (langs.includes("en")) {
          setLanguage("en");
        } else {
          setLanguage(langs[0]);
        }
      } else {
        toast.error("No subtitles found for this URL");
      }
    } catch (e: any) {
      toast.error(e.message || "Detection failed");
    } finally {
      setDetecting(false);
    }
  };

  const languageOptions = detectedLangs
    ? Object.keys(detectedLangs).map((code) => ({
        value: code,
        label: code + (detectedLangs[code]?.includes("auto") ? " (auto)" : ""),
      }))
    : LANGUAGE_OPTIONS;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!url.trim()) return;
    setLoading(true);
    try {
      // Placeholder: call transcript API when available
      toast.success("Transcript request sent");
    } catch (e: any) {
      toast.error(e.message || "Failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="border rounded-lg p-4 space-y-4 bg-card max-w-xl">
      <div className="space-y-2">
        <Label>Video URL</Label>
        <div className="flex gap-2 flex-wrap">
          <Input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://..."
            className="flex-1 min-w-[200px]"
          />
          <Button type="button" variant="outline" size="sm" onClick={detectLanguages} disabled={!url.trim() || detecting}>
            {detecting ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <Languages className="h-4 w-4 mr-1" />}
            Detect Languages
          </Button>
        </div>
      </div>
      <div className="space-y-2">
        <Label>Language</Label>
        <Select value={language} onChange={(e) => setLanguage(e.target.value)} options={languageOptions} />
      </div>
      <Button type="submit" disabled={loading || !url.trim()}>
        {loading ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <Download className="h-4 w-4 mr-2" />}
        Download Transcript
      </Button>
    </form>
  );
}
