import { Brand, Hero } from "@/components/brand";
import { ThemeToggle } from "@/components/theme";

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <main className="auth-page">
      <header className="auth-header">
        <Brand />
        <ThemeToggle />
      </header>
      <div className="auth-layout">
        <Hero />
        <section className="auth-form-side" aria-label="Your Moontig account">
          <div className="auth-card">{children}</div>
          <p className="auth-form-foot">Your workspace. Your documents. Your next discovery.</p>
        </section>
      </div>
      <footer className="auth-footer">
        <span>© {new Date().getFullYear()} Moontig</span>
        <span>A little space for everything you know.</span>
      </footer>
    </main>
  );
}
