import { Activity, Building2, CircleCheck, Clock, TriangleAlert, Users } from "lucide-react";
import { useData } from "../hooks";
import { Button, Card, CardHeader, Empty } from "./ui";

type Metrics = {
  current_accounts: number;
  catalogue_listings: number;
  funding_events_last_30_days: number;
  sources: { source: string; latest_attempt_at: string; latest_attempt_ok: boolean | null;
    last_successful_refresh_at: string | null }[];
};

async function loadMetrics(): Promise<Metrics> {
  const result = await fetch("/api/admin/metrics", { cache: "no-store" });
  if (result.status === 401 || result.status === 403) throw new Error("Admin access is required.");
  if (!result.ok) throw new Error("Metrics are unavailable. Try again after the service is configured.");
  return result.json();
}

const date = (value: string | null) => value ? new Date(value).toLocaleString() : "—";

export function AdminDashboard() {
  const { data, error, reload } = useData(loadMetrics, []);
  const tiles = data ? [
    { label: "Student accounts", value: data.current_accounts, hint: "Current accounts, including admins", icon: Users },
    { label: "Catalogue listings", value: data.catalogue_listings, hint: "Public records, including closed listings", icon: Building2 },
    { label: "Funding events", value: data.funding_events_last_30_days, hint: "Published in the last 30 days", icon: Activity },
  ] : [];
  return (
    <div className="min-w-0 space-y-5">
      <section className="rounded-2xl border border-line bg-linear-to-br from-[#162453] via-[#151b30] to-surface p-5 sm:p-7">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-widest text-[#a6c4ff]">Admin workspace</p>
            <h2 className="mt-2 text-2xl font-semibold tracking-tight text-ink">Platform overview</h2>
            <p className="mt-2 max-w-lg text-sm text-ink-2">Track the catalogue and source health. Student data stays in each student’s workspace.</p>
          </div>
          <Button variant="outline" onClick={reload}>Reload metrics</Button>
        </div>
      </section>
      {error && <p role="alert" className="rounded-lg border border-line bg-surface p-4 text-sm">{error}</p>}
      {!data && !error && <p role="status" className="text-sm text-ink-2">Loading metrics…</p>}
      {data && !error && <>
        <div className="grid gap-3 sm:grid-cols-3">
          {tiles.map(({ label, value, hint, icon: Icon }) => <Card key={label} className="min-w-0 p-4">
            <Icon className="mb-3 size-4 text-[#a6c4ff]" aria-hidden="true" />
            <p className="text-sm font-medium text-ink-2">{label}</p>
            <p className="my-1 text-3xl font-semibold tracking-tight text-ink">{value.toLocaleString()}</p>
            <p className="text-xs text-ink-2">{hint}</p>
          </Card>)}
        </div>
        <Card className="min-w-0">
          <CardHeader title="Public source health" hint="The latest attempt and the last successful refresh are reported separately." />
          {data.sources.length === 0 ? <Empty title="No public source runs recorded" /> : (
            <ul className="divide-y divide-line">
              {data.sources.map((source) => {
                const status = source.latest_attempt_ok === true ? "Succeeded" : source.latest_attempt_ok === false ? "Failed" : "Incomplete";
                const Icon = status === "Succeeded" ? CircleCheck : status === "Failed" ? TriangleAlert : Clock;
                return <li key={source.source} className="grid gap-2 px-5 py-4 text-sm md:grid-cols-3">
                  <p className="break-all font-medium text-ink">{source.source}</p>
                  <p className="text-ink-2"><Icon className="mr-1 inline size-4" aria-hidden="true" />{status} · {date(source.latest_attempt_at)}</p>
                  <p className="text-ink-2">Last success: {date(source.last_successful_refresh_at)}</p>
                </li>;
              })}
            </ul>
          )}
        </Card>
      </>}
    </div>
  );
}
