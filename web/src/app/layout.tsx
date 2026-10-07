import type { Metadata } from "next";
import { connection } from "next/server";
import "./globals.css";

export const metadata: Metadata = {
  title: "RAG Assistant",
  description: "Ask questions about your documents",
  referrer: "no-referrer",
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  // Every page renders per request so Next.js can apply the CSP nonce from src/proxy.ts.
  await connection();
  return (
    <html lang="en">
      <body className="min-h-dvh">{children}</body>
    </html>
  );
}
