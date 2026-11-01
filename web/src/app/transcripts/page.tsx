"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { TranscriptForm } from "@/components/transcripts/transcript-form";

function TranscriptsContent() {
  const searchParams = useSearchParams();
  const urlFromQuery = searchParams.get("url") ?? undefined;
  return <TranscriptForm initialUrl={urlFromQuery} />;
}

export default function TranscriptsPage() {
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Transcripts</h1>
      <Suspense fallback={<div className="text-muted-foreground">Loading...</div>}>
        <TranscriptsContent />
      </Suspense>
    </div>
  );
}
