import type { ButtonHTMLAttributes, ReactNode } from "react";

export function cx(...classes: (string | false | null | undefined)[]): string {
  return classes.filter(Boolean).join(" ");
}

export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <section className={cx("rounded-xl border border-line bg-surface", className)}>{children}</section>
  );
}

export function CardHeader({ title, hint, right }: { title: string; hint?: string; right?: ReactNode }) {
  return (
    <header className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-3.5">
      <div>
        <h2 className="text-sm font-semibold text-ink">{title}</h2>
        {hint && <p className="mt-0.5 text-xs text-muted">{hint}</p>}
      </div>
      {right}
    </header>
  );
}

type Tone = "neutral" | "accent" | "good" | "warning" | "critical";
const TONES: Record<Tone, string> = {
  neutral: "border-line bg-raised text-ink-2",
  accent: "border-accent/30 bg-accent/10 text-[#8cb8f0]",
  good: "border-good/30 bg-good/10 text-[#5fd35f]",
  warning: "border-warning/30 bg-warning/10 text-warning",
  critical: "border-critical/40 bg-critical/10 text-[#f08080]",
};

export function Badge({ children, tone = "neutral", title }: { children: ReactNode; tone?: Tone; title?: string }) {
  return (
    <span
      title={title}
      className={cx("inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] font-medium", TONES[tone])}
    >
      {children}
    </span>
  );
}

export function Button({
  variant = "ghost",
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "ghost" | "outline" }) {
  return (
    <button
      {...props}
      className={cx(
        "inline-flex items-center justify-center gap-2 rounded-lg text-sm font-medium transition-colors",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-50",
        variant === "primary" && "bg-accent px-3.5 py-2 text-white hover:bg-[#3053bd]",
        variant === "outline" && "border border-line px-3 py-1.5 text-ink-2 hover:bg-raised hover:text-ink",
        variant === "ghost" && "p-1.5 text-muted hover:bg-raised hover:text-ink",
        className,
      )}
    />
  );
}

export function Empty({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="px-6 py-16 text-center">
      <p className="text-sm font-medium text-ink-2">{title}</p>
      {hint && <p className="mt-1 text-xs text-muted">{hint}</p>}
    </div>
  );
}

export function ErrorNote({ message }: { message: string }) {
  return (
    <p role="alert" className="rounded-lg border border-critical/40 bg-critical/10 px-4 py-3 text-sm text-[#f08080]">
      Could not load: {message}. Is the API running (`uv run internradar serve`)?
    </p>
  );
}
