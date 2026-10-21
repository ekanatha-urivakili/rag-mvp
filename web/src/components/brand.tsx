import Link from "next/link";

export function BrandMark({ className = "size-10" }: { className?: string }) {
  return (
    <svg viewBox="0 0 48 48" className={className} fill="none" aria-hidden="true">
      <rect width="48" height="48" rx="15" fill="#6D4AFF" />
      <path d="M31.5 33.8A15 15 0 0 1 20.2 9.5 15 15 0 1 0 38.5 29a14.9 14.9 0 0 1-7 4.8Z" fill="#F5F0FF" />
      <path d="m32 10 2.2 6.8L41 19l-6.8 2.2L32 28l-2.2-6.8L23 19l6.8-2.2L32 10Z" fill="#D8FFB0" />
    </svg>
  );
}

export function Brand() {
  return (
    <Link
      href="/"
      className="inline-flex items-center gap-2.5 text-xl font-semibold tracking-[-0.04em]"
      aria-label="Moontig home"
    >
      <BrandMark className="size-9 shrink-0" />
      <span>
        Moontig<span className="text-violet-600 dark:text-violet-400">.</span>
      </span>
    </Link>
  );
}

export function Hero() {
  return (
    <section className="auth-story">
      <p className="brand-eyebrow">A LITTLE CLARITY. A LOT OF POSSIBILITY.</p>
      <h2>
        Your knowledge.
        <br />A little more
        <br />
        <span>illuminated.</span>
      </h2>
      <p className="auth-story-copy">
        Turn the things you save into the answers you need. A thoughtful home for your documents, receipts, and next big
        question.
      </p>
      <div className="knowledge-orbit" aria-hidden="true">
        <div className="orbit-ring" />
        <div className="orbit-ring orbit-ring-inner" />
        <div className="orbit-moon">
          <BrandMark className="size-24" />
        </div>
        <div className="orbit-note orbit-note-document">
          <span className="note-icon">↗</span>
          <div>
            <strong>Everything, connected.</strong>
            <small>Your documents, in one place</small>
          </div>
        </div>
        <div className="orbit-note orbit-note-answer">
          <span className="note-icon">✧</span>
          <div>
            <strong>Answers with context.</strong>
            <small>Grounded in your sources</small>
          </div>
        </div>
        <span className="orbit-spark">✦</span>
      </div>
      <div className="auth-story-foot">
        <span /> Less searching. More understanding.
      </div>
    </section>
  );
}

export function Footer() {
  return (
    <footer className="flex shrink-0 flex-wrap justify-center gap-x-5 gap-y-1 border-t border-zinc-200 px-3 py-2 text-xs text-zinc-500 dark:border-zinc-800">
      <span>Moontig</span>
      <Link href="/">Home</Link>
      <Link href="/documents">Upload docs</Link>
      <Link href="/receipts">Scan receipts</Link>
      <Link href="/settings">Settings &amp; help</Link>
    </footer>
  );
}
