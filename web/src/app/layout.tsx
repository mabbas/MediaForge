import type { Metadata } from "next";
import "./globals.css";
import { Toaster } from "sonner";

export const metadata: Metadata = {
  title: "GrabItDown",
  description: "Media downloader platform",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <nav className="border-b px-4 py-2 flex gap-4">
          <a href="/" className="font-semibold">GrabItDown</a>
          <a href="/downloads">Downloads</a>
          <a href="/history">History</a>
          <a href="/transcripts">Transcripts</a>
          <a href="/providers">Providers</a>
          <a href="/settings">Settings</a>
        </nav>
        <main className="container mx-auto p-4">{children}</main>
        <Toaster />
      </body>
    </html>
  );
}
