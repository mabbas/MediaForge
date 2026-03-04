"use client";

import { useSearchParams } from "next/navigation";
import { TranscriptForm } from "@/components/transcripts/transcript-form";

export default function TranscriptsPage() {
  const searchParams = useSearchParams();
  const urlFromQuery = searchParams.get("url") ?? undefined;
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Transcripts</h1>
      <TranscriptForm initialUrl={urlFromQuery} />
    </div>
  );
}
