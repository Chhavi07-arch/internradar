import type { Origin } from "./api";

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

export function timeAgo(iso: string | null): string {
  if (!iso) return "—";
  const elapsed = Date.now() - new Date(iso).getTime();
  if (elapsed < HOUR) return `${Math.max(1, Math.round(elapsed / MINUTE))}m ago`;
  if (elapsed < DAY) return `${Math.round(elapsed / HOUR)}h ago`;
  return `${Math.round(elapsed / DAY)}d ago`;
}

/** "in 3d", "today", "2d ago" relative to now, for deadlines. */
export function dueIn(iso: string): { label: string; urgent: boolean; past: boolean } {
  const days = Math.ceil((new Date(iso).getTime() - Date.now()) / DAY);
  if (days < 0) return { label: `closed ${-days}d ago`, urgent: false, past: true };
  if (days === 0) return { label: "due today", urgent: true, past: false };
  return { label: `due in ${days}d`, urgent: days <= 3, past: false };
}

export function shortDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}

export const ORIGINS: { key: Origin; label: string }[] = [
  { key: "ats", label: "Company boards" },
  { key: "list", label: "Lists" },
  { key: "board", label: "Job boards" },
  { key: "email", label: "Email" },
];

const SOURCE_NAMES: Record<string, string> = {
  linkedin: "LinkedIn",
  naukri: "Naukri",
  indeed: "Indeed",
  greenhouse: "Greenhouse",
  lever: "Lever",
  ashby: "Ashby",
};

/** "greenhouse:anthropic" -> "Greenhouse", "gmail:college" -> "College mail". */
export function sourceLabel(source: string): string {
  const [kind, name] = source.split(":");
  if (kind === "gmail") return `${name[0].toUpperCase()}${name.slice(1)} mail`;
  return SOURCE_NAMES[kind] ?? kind[0].toUpperCase() + kind.slice(1);
}

export function place(city: string | null, remote: boolean): string {
  if (city === "remote" || (remote && !city)) return "Remote";
  if (!city) return "Location not stated";
  const name = city.replace(/\b\w/g, (c) => c.toUpperCase());
  return remote ? `${name} · Remote` : name;
}
