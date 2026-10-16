import Link from "next/link";
import { ThemeToggle } from "@/components/theme";

export default function NotFound() {
  return (
    <main className="relative grid min-h-dvh place-items-center p-6 text-center">
      <div className="absolute top-3 right-3">
        <ThemeToggle />
      </div>
      <div>
        <h1 className="text-xl font-semibold">Not found</h1>
        <p className="mt-2 text-sm text-zinc-500">This page doesn&apos;t exist or you don&apos;t have access to it.</p>
        <Link href="/" className="mt-4 inline-block text-sm text-indigo-600 underline dark:text-indigo-400">
          Back to chat
        </Link>
      </div>
    </main>
  );
}
