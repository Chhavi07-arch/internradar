import type { OpportunityDetail, OpportunityPatch, Overview } from "./api";
import type { StudentProfile } from "./profile";

const now = Date.now();
const day = 86_400_000;
const date = (offset: number) => new Date(now + offset * day).toISOString();
const listings: OpportunityDetail[] = [
  ["Applied ML Intern", "Signal Labs", "Bengaluru"],
  ["Backend Engineering Intern", "Orchard Systems", "Remote"],
  ["Frontend Engineering Intern", "Cobalt Studio", "Bengaluru"],
  ["Data Engineering Intern", "Parallax Works", "Remote"],
].map(([title, company, city], index) => ({
  id: index + 1, title, company, city, location: city, is_remote: city === "Remote",
  url: null, apply_url: null, posted_at: date(-index), first_seen_at: date(-index),
  deadline_at: date(index + 2), primary_origin: "ats", status: "active", sources: ["greenhouse:demo"],
  stipend: null, eligibility: { branches: ["Computer Science"], batches: [2028] },
  funding: null, seen: false, bookmarked: false, hidden: false, notes: null,
  description: "Fictional internship for this public demo. Review the eligibility, bookmark the listing, or add a private demo note. This is not a real opening.",
  sightings: [{ source: "greenhouse:demo", origin: "ats", url: null, first_seen_at: date(-index), last_seen_at: date(0), closed: false }],
}));
let profile: StudentProfile = { name: "", college: "", branch: "", degree: "", batch: null,
  roles: [], skills: [], projects: [], onboarding_state: "skipped" };

export function demoRequest(path: string, init?: RequestInit): unknown {
  const url = new URL(path, "https://demo.invalid");
  const visible = listings.filter((item) => !item.hidden);
  const body = () => JSON.parse(String(init?.body ?? "{}"));
  switch (url.pathname) {
    case "/api/profile":
      if (init?.method === "PUT") profile = { ...body(), onboarding_state: "completed" };
      return profile;
    case "/api/profile/skip": return profile;
    case "/api/overview": return {
      open_internships: visible.length, unseen: visible.filter((item) => !item.seen).length,
      saved: visible.filter((item) => item.bookmarked).length, due_this_week: visible.length,
      funded_rounds_30d: 0, last_run_at: null, sources_ok: 0, sources_failed: 0,
      fetch_running: false, llm_configured: false, inboxes: {}, per_day: [],
    } satisfies Overview;
    case "/api/opportunities": {
      const view = url.searchParams.get("view");
      const origin = url.searchParams.get("origin");
      const query = url.searchParams.get("q")?.toLowerCase() ?? "";
      return listings.filter((item) => item.hidden === (view === "hidden") &&
        (view !== "saved" || item.bookmarked) && (!origin || item.primary_origin === origin) &&
        `${item.title} ${item.company}`.toLowerCase().includes(query))
        .sort((a, b) => view === "inbox" ? Number(a.seen) - Number(b.seen) : a.id - b.id);
    }
    case "/api/opportunities/mark-seen": {
      const unread = visible.filter((item) => !item.seen);
      unread.forEach((item) => { item.seen = true; });
      return { marked: unread.length };
    }
    case "/api/funding":
    case "/api/sources": return [];
    default: {
      const item = listings.find((entry) => url.pathname === `/api/opportunities/${entry.id}`);
      if (!item) throw new Error("Unavailable in the public demo.");
      if (init?.method === "PATCH") Object.assign(item, body() as OpportunityPatch);
      return item;
    }
  }
}
