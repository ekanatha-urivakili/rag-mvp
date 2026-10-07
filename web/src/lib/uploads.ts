export type UploadKind = "document" | "receipt";
export const ACCEPT: Record<UploadKind, string> = {
  document: ".pdf,.docx,.html,.htm,.md,.markdown,.txt",
  receipt: ".pdf,.jpg,.jpeg,.png,.webp",
};
export function uploadError(file: Pick<File, "name" | "size">, kind: UploadKind): string | null {
  const extension = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
  if (!ACCEPT[kind].split(",").includes(extension))
    return kind === "receipt"
      ? "Choose a PDF, JPEG, PNG or WebP receipt."
      : "Choose a PDF, DOCX, HTML, Markdown or text document.";
  if (file.size === 0) return "This file is empty.";
  if (file.size > 25 * 1024 * 1024) return "File is larger than 25 MB.";
  return null;
}
