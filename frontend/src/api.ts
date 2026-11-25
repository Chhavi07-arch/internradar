// Mirrors the response models in backend/src/internradar/api.py.
import type { ProfileFields, StudentProfile } from "./profile";

export type Origin = "ats" | "list" | "board" | "email";
export type View = "inbox" | "all" | "saved" | "deadlines" | "hidden";

export interface DayCount {
  day: string;
  ats: number;
  list: number;
  board: number;
  email: number;
}

export interface Overview {
  open_internships: number;
  unseen: number;
  saved: number;
  due_this_week: number;
  funded_rounds_30d: number;
  last_run_at: string | null;
  sources_ok: number;
  sources_failed: number;
  fetch_running: boolean;
  llm_configured: boolean;
  inboxes: Record<string, boolean>;
  per_day: DayCount[];
}

export interface Eligibility {
  min_cgpa?: number | null;
  branches?: string[];
  batches?: number[];
  backlogs_allowed?: boolean | null;
  notes?: string | null;
}

export interface Opportunity {
  id: number;
  title: string;
  company: string;
  city: string | null;
  is_remote: boolean;
  url: string | null;
  apply_url: string | null;
  posted_at: string | null;
  first_seen_at: string;
  deadline_at: string | null;
  primary_origin: Origin;
  status: "new" | "active" | "closed";
  sources: string[];
  stipend: string | null;
  eligibility: Eligibility | null;
  funding: string | null;
  seen: boolean;
  bookmarked: boolean;
  hidden: boolean;
  notes: string | null;
}

export interface Sighting {
  source: string;
  origin: Origin;
  url: string | null;
  first_seen_at: string;
  last_seen_at: string;
  closed: boolean;
}

export interface OpportunityDetail extends Opportunity {
  location: string | null;
  description: string | null;
  sightings: Sighting[];
}

export interface FundingRound {
  id: number;
  company: string;
  round: string | null;
  amount_text: string | null;
  amount_usd: number | null;
  lead_investors: string | null;
  headline: string;
  url: string;
  published_at: string;
  mentions: number;
  board: string | null;
  internships: number;
}

export interface SourceHealth {
  source: string;
  ok: boolean;
  fetched: number;
  kept: number;
  new: number;
  duration_ms: number;
  error: string | null;
}

export interface FetchState {
  running: boolean;
  last_finished_at: string | null;
  last_new: number | null;
  last_error: string | null;
}

export type OpportunityPatch = Partial<Pick<Opportunity, "seen" | "bookmarked" | "hidden" | "notes">>;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(typeof body?.detail === "string" ? body.detail : response.status === 422
      ? "Some fields are invalid. Check your entries and try again."
      : `${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  profile: () => request<Partial<StudentProfile>>("/api/profile"),
  saveProfile: (fields: ProfileFields) => request<StudentProfile>("/api/profile", { method: "PUT", body: JSON.stringify(fields) }),
  skipProfile: () => request<StudentProfile>("/api/profile/skip", { method: "POST" }),
  overview: () => request<Overview>("/api/overview"),
  opportunities: (view: View, q: string, origin: Origin | null) => {
    const params = new URLSearchParams({ view });
    if (q.trim()) params.set("q", q.trim());
    if (origin) params.set("origin", origin);
    return request<Opportunity[]>(`/api/opportunities?${params}`);
  },
  opportunity: (id: number) => request<OpportunityDetail>(`/api/opportunities/${id}`),
  update: (id: number, patch: OpportunityPatch) =>
    request<Opportunity>(`/api/opportunities/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),
  markAllSeen: () => request<{ marked: number }>("/api/opportunities/mark-seen", { method: "POST" }),
  funding: (days: number) => request<FundingRound[]>(`/api/funding?days=${days}`),
  sources: () => request<SourceHealth[]>("/api/sources"),
  fetchState: () => request<FetchState>("/api/fetch"),
  startFetch: () => request<FetchState>("/api/fetch", { method: "POST", body: "{}" }),
};
