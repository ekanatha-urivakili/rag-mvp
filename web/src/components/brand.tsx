import Link from "next/link";

export function Brand() {
  return (
    <Link href="/" className="inline-flex items-center gap-2 font-semibold tracking-tight" aria-label="FolioNest home">
      <svg viewBox="0 0 40 40" width="34" height="34" fill="none" aria-hidden="true">
        <rect width="40" height="40" rx="12" fill="#0d9488" />
        <path d="M12 10h13l5 5v17H12z" fill="#eef2ff" />
        <path d="M25 10v6h5" fill="#6366f1" />
        <path d="M16 21h10M16 25h7" stroke="#4f46e5" strokeWidth="2" strokeLinecap="round" />
        <circle cx="11" cy="30" r="6" fill="#fbbf24" />
        <path d="m8 30 2 2 4-4" stroke="#134e4a" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      FolioNest
    </Link>
  );
}

export function Hero() {
  return (
    <section className="brand-hero rounded-2xl px-5 py-4">
      <p className="text-xs font-semibold uppercase tracking-widest text-teal-200">
        Your workspace, thoughtfully organised
      </p>
      <h1 className="mt-1 text-xl font-semibold sm:text-2xl">A home for every document and receipt.</h1>
      <p className="mt-1 text-sm text-indigo-100">Upload, scan, and find answers with FolioNest.</p>
    </section>
  );
}

export function Footer() {
  return (
    <footer className="flex shrink-0 flex-wrap justify-center gap-x-5 gap-y-1 border-t border-zinc-200 px-3 py-2 text-xs text-zinc-500 dark:border-zinc-800">
      <span>FolioNest</span>
      <Link href="/">Home</Link>
      <Link href="/documents">Upload docs</Link>
      <Link href="/receipts">Scan receipts</Link>
      <Link href="/settings">Settings &amp; help</Link>
    </footer>
  );
}
