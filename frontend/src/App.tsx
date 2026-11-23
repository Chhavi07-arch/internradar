import { CheckCheck, RefreshCw, Search } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import type { Opportunity, Origin, View } from "./api";
import { api } from "./api";
import { DailyChart } from "./components/DailyChart";
import { FundingTable } from "./components/FundingTable";
import { OpportunityDrawer } from "./components/OpportunityDrawer";
import { OpportunityList } from "./components/OpportunityList";
import { Sidebar } from "./components/Sidebar";
import { SourcesTable } from "./components/SourcesTable";
import { StatTiles } from "./components/StatTiles";
import { Button, Card, CardHeader, ErrorNote } from "./components/ui";
import { timeAgo } from "./format";
import { type Route, useData, useRoute } from "./hooks";

const TITLES: Record<Route, { title: string; hint: string }> = {
  inbox: { title: "Inbox", hint: "Unread first. Opening an internship marks it read." },
  saved: { title: "Saved", hint: "Your shortlist." },
  deadlines: { title: "Deadlines", hint: "Internships with a stated deadline, soonest first." },
  hidden: { title: "Hidden", hint: "Internships you marked as not interested." },
  funding: { title: "Funding radar", hint: "Startups that just raised money usually start hiring within weeks." },
  sources: { title: "Sources", hint: "The latest result from every job board, careers board and inbox." },
};

const EMPTY_HINTS: Partial<Record<Route, string>> = {
  inbox: "Press Refresh to fetch every source.",
  saved: "Bookmark an internship to keep it here.",
  deadlines: "Deadlines come from company boards and placement emails.",
  hidden: "Nothing hidden.",
};

export default function App() {
  const [route, navigate] = useRoute();
  const overview = useData(api.overview, []);

  return (
    <div className="flex h-full">
      <Sidebar route={route} onNavigate={navigate} overview={overview.data} />
      <main className="flex min-w-0 flex-1 flex-col overflow-y-auto">
        <TopBar route={route} lastRun={overview.data?.last_run_at ?? null} onFetched={overview.reload} />
        <div className="mx-auto w-full max-w-6xl space-y-5 px-6 py-6">
          {overview.error && <ErrorNote message={overview.error} />}
          {route === "funding" ? (
            <FundingPage />
          ) : route === "sources" ? (
            <SourcesPage />
          ) : (
            <>
              {route === "inbox" && overview.data && (
                <>
                  <StatTiles overview={overview.data} />
                  <DailyChart days={overview.data.per_day} />
                </>
              )}
              <ListPage view={route as View} onChanged={overview.reload} />
            </>
          )}
        </div>
      </main>
    </div>
  );
}

function TopBar({ route, lastRun, onFetched }: { route: Route; lastRun: string | null; onFetched: () => void }) {
  const [running, setRunning] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  // Pick up a fetch already in progress (started from the CLI or another tab).
  useEffect(() => {
    api.fetchState().then((s) => setRunning(s.running)).catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(async () => {
      const state = await api.fetchState();
      if (state.running) return;
      setRunning(false);
      setMessage(state.last_error ? `Fetch failed: ${state.last_error}` :
        state.last_new != null ? `${state.last_new} new internship${state.last_new === 1 ? "" : "s"}` : null);
      onFetched();
      window.dispatchEvent(new Event("internradar:refresh"));
    }, 2000);
    return () => window.clearInterval(timer);
  }, [running, onFetched]);

  async function start() {
    setMessage(null);
    try {
      await api.startFetch();
      setRunning(true);
    } catch (e) {
      setMessage((e as Error).message);
    }
  }

  const { title, hint } = TITLES[route];
  return (
    <header className="sticky top-0 z-20 border-b border-line bg-page/85 backdrop-blur">
      <div className="mx-auto flex w-full max-w-6xl items-center gap-4 px-6 py-3.5">
        <div className="min-w-0 flex-1">
          <h1 className="text-base font-semibold text-ink">{title}</h1>
          <p className="truncate text-xs text-muted">{hint}</p>
        </div>
        <p className="hidden text-xs text-muted sm:block" aria-live="polite">
          {message ?? (running ? "Fetching every source… (2-3 min)" : `Last fetch ${timeAgo(lastRun)}`)}
        </p>
        <Button variant="primary" onClick={start} disabled={running}>
          <RefreshCw className={running ? "size-4 animate-spin" : "size-4"} />
          {running ? "Fetching" : "Refresh"}
        </Button>
      </div>
    </header>
  );
}

function ListPage({ view, onChanged }: { view: View; onChanged: () => void }) {
  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");
  const [origin, setOrigin] = useState<Origin | null>(null);
  const [openId, setOpenId] = useState<number | null>(null);
  const list = useData(() => api.opportunities(view, debounced, origin), [view, debounced, origin]);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(query), 250);
    return () => window.clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    window.addEventListener("internradar:refresh", list.reload);
    return () => window.removeEventListener("internradar:refresh", list.reload);
  }, [list.reload]);

  // An item that no longer belongs in this view (unsaved, hidden...) leaves the list.
  const belongs = (item: Opportunity) =>
    view === "hidden" ? item.hidden : !item.hidden && (view !== "saved" || item.bookmarked);

  const replace = useCallback((updated: Opportunity) => {
    list.setData((items) =>
      items?.flatMap((item) => (item.id !== updated.id ? [item] : belongs(updated) ? [updated] : [])) ?? items,
    );
    onChanged();
  }, [view, onChanged]);

  async function open(item: Opportunity) {
    setOpenId(item.id);
    if (!item.seen) replace(await api.update(item.id, { seen: true }));
  }

  async function toggle(item: Opportunity, field: "bookmarked" | "hidden") {
    replace(await api.update(item.id, { [field]: !item[field] }));
  }

  async function markAllRead() {
    await api.markAllSeen();
    list.reload();
    onChanged();
  }

  const unread = list.data?.filter((i) => !i.seen).length ?? 0;
  return (
    <Card>
      <CardHeader
        title={list.data ? `${list.data.length} internship${list.data.length === 1 ? "" : "s"}` : "Loading…"}
        hint={view === "inbox" && unread ? `${unread} unread` : undefined}
        right={
          <div className="flex items-center gap-2">
            <label className="relative">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted" />
              <span className="sr-only">Search role or company</span>
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search role or company"
                className="w-56 rounded-lg border border-line bg-page py-1.5 pl-8 pr-3 text-sm text-ink placeholder:text-muted focus:border-accent focus:outline-none"
              />
            </label>
            {view === "inbox" && unread > 0 && (
              <Button variant="outline" onClick={markAllRead}><CheckCheck className="size-4" /> Mark all read</Button>
            )}
          </div>
        }
      />
      {list.error ? (
        <div className="p-5"><ErrorNote message={list.error} /></div>
      ) : (
        list.data && (
          <OpportunityList items={list.data} origin={origin} onOrigin={setOrigin} onOpen={open}
            onToggle={toggle} emptyHint={EMPTY_HINTS[view as Route] ?? ""} />
        )
      )}
      {openId !== null && <OpportunityDrawer id={openId} onClose={() => setOpenId(null)} onChange={replace} />}
    </Card>
  );
}

function FundingPage() {
  const [days, setDays] = useState(60);
  const funding = useData(() => api.funding(days), [days]);
  return (
    <Card>
      <CardHeader
        title="Recently funded tech startups"
        hint="From Entrackr, Inc42, YourStory and Indian Startup News. Careers boards found here are polled on every fetch."
        right={
          <select value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label="Time range"
            className="rounded-lg border border-line bg-page px-2.5 py-1.5 text-sm text-ink-2 focus:border-accent focus:outline-none">
            {[30, 60, 90, 180].map((d) => <option key={d} value={d}>Last {d} days</option>)}
          </select>
        }
      />
      {funding.error ? <div className="p-5"><ErrorNote message={funding.error} /></div>
        : funding.data && <FundingTable rounds={funding.data} />}
    </Card>
  );
}

function SourcesPage() {
  const sources = useData(api.sources, []);
  useEffect(() => {
    window.addEventListener("internradar:refresh", sources.reload);
    return () => window.removeEventListener("internradar:refresh", sources.reload);
  }, [sources.reload]);
  const failed = sources.data?.filter((s) => !s.ok).length ?? 0;
  return (
    <Card>
      <CardHeader
        title={sources.data ? `${sources.data.length} sources` : "Loading…"}
        hint={failed ? `${failed} failing: they are retried on the next fetch` : "All healthy"}
      />
      {sources.error ? <div className="p-5"><ErrorNote message={sources.error} /></div>
        : sources.data && <SourcesTable sources={sources.data} />}
    </Card>
  );
}
