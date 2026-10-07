"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Alert, Button } from "@/components/ui";
import { ApiError, request } from "@/lib/client";
import { ACCEPT, uploadError, type UploadKind } from "@/lib/uploads";

export function FileUpload({ kind }: { kind: UploadKind }) {
  const qc = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const camera = useRef<HTMLInputElement>(null);
  const gallery = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [notes, setNotes] = useState<{ tone: "error" | "success"; text: string }[]>([]);
  async function upload(files: FileList | File[]) {
    if (busy) return;
    setBusy(true);
    const results: typeof notes = [];
    for (const file of Array.from(files)) {
      const error = uploadError(file, kind);
      if (error) {
        results.push({ tone: "error", text: `${file.name}: ${error}` });
        continue;
      }
      const form = new FormData();
      form.append("file", file, file.name);
      try {
        const response = await request(`/api/v1/${kind === "receipt" ? "receipts" : "documents"}`, {
          method: "POST",
          body: form,
        });
        const result = (await response.json()) as { status: string };
        results.push({ tone: "success", text: `${file.name}: ${result.status}. Processing may take a moment.` });
      } catch (e) {
        results.push({
          tone: "error",
          text: `${file.name}: ${e instanceof ApiError ? e.message : "Upload failed. Try again."}`,
        });
      }
    }
    setNotes(results);
    setBusy(false);
    for (const ref of [input, camera, gallery]) if (ref.current) ref.current.value = "";
    await qc.invalidateQueries({ queryKey: [kind === "receipt" ? "receipt-uploads" : "documents"] });
    if (kind === "receipt") await qc.invalidateQueries({ queryKey: ["receipts"] });
  }
  return (
    <section aria-label={kind === "receipt" ? "Upload receipts" : "Upload documents"} className="space-y-3">
      <div
        onDragOver={(event) => {
          event.preventDefault();
          if (!busy) setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          if (!busy) void upload(event.dataTransfer.files);
        }}
        className={`rounded-2xl border-2 border-dashed p-6 text-center ${dragging ? "border-teal-500 bg-teal-50 dark:bg-teal-950" : "border-zinc-300 dark:border-zinc-700"}`}
      >
        <p className="mb-3 hidden text-sm font-medium md:block">
          Drag and drop {kind === "receipt" ? "receipts" : "documents"} here
        </p>
        <input
          ref={input}
          aria-label={`Choose ${kind} files`}
          type="file"
          accept={ACCEPT[kind]}
          multiple
          className="sr-only"
          disabled={busy}
          onChange={(event) => {
            if (event.target.files?.length) void upload(event.target.files);
          }}
        />
        <Button disabled={busy} onClick={() => input.current?.click()}>
          {busy ? "Uploading…" : `Choose ${kind === "receipt" ? "receipts" : "documents"}`}
        </Button>
        <p className="mt-3 text-xs text-zinc-500">
          {kind === "receipt" ? "PDF, JPEG, PNG or WebP" : "PDF, DOCX, HTML, Markdown or text"} · Up to 25 MB per file
        </p>
      </div>
      {kind === "receipt" && (
        <div className="space-y-3 md:hidden">
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" disabled={busy} onClick={() => camera.current?.click()}>
              Take receipt photo
            </Button>
            <Button variant="secondary" disabled={busy} onClick={() => gallery.current?.click()}>
              Open photo gallery
            </Button>
          </div>
          <input
            ref={camera}
            aria-label="Take receipt photo"
            type="file"
            accept="image/jpeg,image/png,image/webp"
            capture="environment"
            className="sr-only"
            disabled={busy}
            onChange={(event) => {
              if (event.target.files?.length) void upload(event.target.files);
            }}
          />
          <input
            ref={gallery}
            aria-label="Select receipt photos"
            type="file"
            accept="image/jpeg,image/png,image/webp"
            multiple
            className="sr-only"
            disabled={busy}
            onChange={(event) => {
              if (event.target.files?.length) void upload(event.target.files);
            }}
          />
          <p className="text-xs text-zinc-500">
            Your device asks for camera or photo access when needed. Only photos you select are shared. If camera access
            is denied, open the gallery or change access in your device settings. Use JPEG, PNG or WebP; convert HEIC
            photos before uploading.
          </p>
        </div>
      )}
      {notes.map((note, index) => (
        <Alert key={index} tone={note.tone}>
          {note.text}
        </Alert>
      ))}
    </section>
  );
}
