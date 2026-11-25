import { ArrowDownRight, CheckCheck, RefreshCw, Search } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import type { Opportunity, Origin, View } from "./api";
import { api } from "./api";
import { AdminDashboard } from "./components/AdminDashboard";
import { ProfileWorkspace } from "./components/ProfileWorkspace";
import { normalizeProfile } from "./profile";
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
  profile: { title: "Profile", hint: "Your student details, skills and projects." },
  admin: { title: "Admin", hint: "InternRadar system metrics." },
};

const EMPTY_HINTS: Partial<Record<Route, string>> = {
  inbox: "Press Refresh to fetch every source.",
  saved: "Bookmark an internship to keep it here.",
  deadlines: "Deadlines come from company boards and placement emails.",
  hidden: "Nothing hidden.",
};

export default function App({ hostedRole, onSignOut }: { hostedRole?: "student" | "admin"; onSignOut?: () => Promise<void> }) {
  const [requested, navigate] = useRoute();
  const overview = useData(api.overview, []);
  const profile = useData(async () => hostedRole ? normalizeProfile(await api.profile()) : null, [hostedRole]);
  const onboarding = profile.data?.onboarding_state === "pending";
  const availableRoute = (requested === "admin" && hostedRole !== "admin") || (requested === "profile" && !hostedRole) ? "inbox" : requested;
  const route = onboarding ? "profile" : availableRoute;
  const waiting = !!hostedRole && !profile.data && !profile.error;
  const profileError = !!hostedRole && !profile.data && profile.error;
  const mainRef = useRef<HTMLElement>(null);
  useEffect(() => { mainRef.current?.scrollTo(0, 0); }, [route, onboarding]);

  return (
    <div className="flex h-full flex-col md:flex-row">
      {!onboarding && !waiting && <Sidebar route={route} onNavigate={navigate} overview={overview.data} hostedRole={hostedRole} />}
      <main ref={mainRef} className="flex min-w-0 flex-1 flex-col overflow-y-auto">
        {import.meta.env.MODE === "public-demo" && <p className="border-b border-white/15 bg-[#2c3480] px-4 py-2 text-center text-xs text-white">PUBLIC DEMO · Fictional listings · No login or Gmail access · Changes last until reload</p>}
        <TopBar route={waiting ? "profile" : route} lastRun={overview.data?.last_run_at ?? null} onFetched={overview.reload} onSignOut={onSignOut} hosted={!!hostedRole} />
        <div className="mx-auto w-full max-w-6xl space-y-5 px-4 py-5 sm:px-6 sm:py-6">
          {waiting ? <p role="status" className="text-sm text-ink-2">Loading profile…</p>
          : (route === "profile" || route === "admin") && profileError ? (
            <Card className="space-y-3 p-4">
              <p role="alert" className="text-sm text-[#f08080]">Could not load your profile: {profileError}</p>
              <div className="flex gap-2">
                <Button variant="outline" onClick={profile.reload}>Retry</Button>
                <Button onClick={() => navigate("inbox")}>Back to inbox</Button>
              </div>
            </Card>
          ) : route === "profile" && profile.data ? (
            <ProfileWorkspace profile={profile.data} onboarding={onboarding} onBack={() => navigate("inbox")} onSaved={(updated) => {
              profile.setData(updated);
              if (onboarding) navigate("inbox");
            }} onSkipped={(updated) => {
              profile.setData(updated);
              navigate("inbox");
            }} />
          ) : route === "admin" ? <AdminDashboard />
          : route === "funding" ? (
            <FundingPage />
          ) : route === "sources" ? (
            <SourcesPage hosted={!!hostedRole} />
          ) : (
            <>
              {route === "inbox" && <Discovery overview={overview.data} />}
              {overview.error && <ErrorNote message={overview.error} />}
              <ListPage view={route as View} onChanged={overview.reload} hosted={!!hostedRole} />
              {route === "inbox" && overview.data?.per_day.some((day) => day.ats + day.list + day.board + day.email > 0) && (
                <details className="rounded-xl border border-line bg-surface p-4">
                  <summary className="cursor-pointer text-sm font-medium">Discovery activity · last 14 days</summary>
                  <div className="mt-4"><DailyChart days={overview.data.per_day} /></div>
                </details>
              )}
            </>
          )}
        </div>
      </main>
    </div>
  );
}

function Discovery({ overview }: { overview: Awaited<ReturnType<typeof api.overview>> | null }) {
  return (
    <section className="radar-hero relative isolate overflow-hidden rounded-2xl border border-line px-5 py-6 sm:px-8 sm:py-8">
      <div className="editorial-orbit pointer-events-none absolute inset-y-0 right-0 -z-10 w-1/2" aria-hidden="true" />
      <p className="text-xs font-semibold uppercase tracking-[.23em] text-[#b4c7ff]">InternRadar / Discovery</p>
      <h2 className="editorial-title mt-3 max-w-2xl text-4xl leading-[.99] tracking-tight text-white sm:text-6xl">Your next chapter<br /><em>starts here.</em></h2>
      <p className="mt-3 max-w-md text-sm leading-relaxed text-ink-2">Browse openings, spot upcoming deadlines, and save roles worth coming back to.</p>
      <a href="#opportunity-feed" onClick={(event) => { event.preventDefault(); document.getElementById("opportunity-feed")?.focus(); }} className="mt-5 inline-flex items-center gap-2 text-sm font-semibold text-[#c6d4ff] hover:text-white">Browse the feed <ArrowDownRight className="size-4" aria-hidden="true"/></a>
      {overview && <div className="editorial-stats mt-6 rounded-sm px-4 py-4"><StatTiles overview={overview} /></div>}
    </section>
  );
}

function TopBar({ route, lastRun, onFetched, onSignOut, hosted }: { route: Route; lastRun: string | null; onFetched: () => void; onSignOut?: () => Promise<void>; hosted: boolean }) {
  const [running, setRunning] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const isFeed = route !== "profile" && route !== "admin";

  // Pick up a fetch already in progress (started from the CLI or another tab).
  useEffect(() => {
    if (isFeed && !hosted) api.fetchState().then((s) => setRunning(s.running)).catch(() => undefined);
  }, [isFeed, hosted]);

  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(async () => {
      try {
        const state = await api.fetchState();
        if (state.running) return;
        setRunning(false);
        setMessage(state.last_error ? `Fetch failed: ${state.last_error}` :
          state.last_new != null ? `${state.last_new} new internship${state.last_new === 1 ? "" : "s"}` : null);
        onFetched();
        window.dispatchEvent(new Event("internradar:refresh"));
      } catch (e) {
        setRunning(false);
        setMessage((e as Error).message);
      }
    }, 2000);
    return () => window.clearInterval(timer);
  }, [running, onFetched]);

  async function start() {
    setMessage(null);
    if (hosted) {
      onFetched();
      window.dispatchEvent(new Event("internradar:refresh"));
      return;
    }
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
      <div className="mx-auto flex w-full max-w-6xl items-center gap-2 px-4 py-3.5 sm:gap-4 sm:px-6">
        <div className="min-w-0 flex-1">
          <h1 className="text-base font-semibold text-ink">{title}</h1>
          <p className="truncate text-xs text-muted">{hint}</p>
        </div>
        <p className="hidden text-xs text-muted sm:block" aria-live="polite">
          {message ?? (isFeed ? running ? "Fetching every source… (2-3 min)" : lastRun ? `Last source run ${timeAgo(lastRun)}` : "No source run recorded" : null)}
        </p>
        {onSignOut && <Button className="shrink-0" variant="outline" onClick={() => void onSignOut().catch((e: Error) => setMessage(e.message))}>Sign out</Button>}
        {isFeed && <Button variant="primary" onClick={start} disabled={running}>
          <RefreshCw className={running ? "size-4 animate-spin" : "size-4"} />
          {hosted ? route === "inbox" ? "Reload feed" : "Reload data" : running ? "Fetching" : "Refresh"}
        </Button>}
      </div>
    </header>
  );
}

function ListPage({ view, onChanged, hosted }: { view: View; onChanged: () => void; hosted: boolean }) {
  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");
  const [origin, setOrigin] = useState<Origin | null>(null);
  const [openId, setOpenId] = useState<number | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
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

  async function change(action: () => Promise<void>) {
    setActionError(null);
    try {
      await action();
    } catch {
      setActionError("Could not update your feed. Try again.");
    }
  }

  function open(item: Opportunity) {
    setOpenId(item.id);
    if (!item.seen) void change(async () => replace(await api.update(item.id, { seen: true })));
  }

  function toggle(item: Opportunity, field: "bookmarked" | "hidden") {
    void change(async () => replace(await api.update(item.id, { [field]: !item[field] })));
  }

  function markAllRead() {
    void change(async () => {
      await api.markAllSeen();
      list.reload();
      onChanged();
    });
  }

  const unread = list.data?.filter((i) => !i.seen).length ?? 0;
  return (
    <section id="opportunity-feed" tabIndex={-1} className="scroll-mt-24 rounded-xl border border-line bg-surface">
      <CardHeader
        title={list.data ? `${list.data.length} internship${list.data.length === 1 ? "" : "s"}` : "Loading…"}
        hint={view === "inbox" && unread ? `${unread} unread` : undefined}
        right={
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <label className="relative">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted" />
              <span className="sr-only">Search role or company</span>
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search role or company"
                className="w-full max-w-56 rounded-lg border border-line bg-page py-1.5 pl-8 pr-3 text-sm text-ink placeholder:text-muted focus:border-accent focus:outline-none"
              />
            </label>
            {view === "inbox" && unread > 0 && (
              <Button variant="outline" onClick={markAllRead}><CheckCheck className="size-4" /> Mark inbox read</Button>
            )}
          </div>
        }
      />
      {actionError && <p role="alert" className="px-5 py-3 text-sm text-[#f08080]">{actionError}</p>}
      {list.error ? (
        <div className="p-5"><ErrorNote message={list.error} /></div>
      ) : (
        list.data && (
          <OpportunityList items={list.data} origin={origin} onOrigin={setOrigin} onOpen={open}
            onToggle={toggle} emptyHint={query.trim() || origin ? "Try a different search or source." : hosted && view === "inbox"
              ? "Listings appear here after a catalogue run. Reload checks for already collected results."
              : EMPTY_HINTS[view as Route] ?? ""} />
        )
      )}
      {openId !== null && <OpportunityDrawer id={openId} onClose={() => setOpenId(null)} onChange={replace} />}
    </section>
  );
}

function FundingPage() {
  const [days, setDays] = useState(60);
  const funding = useData(() => api.funding(days), [days]);
  useEffect(() => {
    window.addEventListener("internradar:refresh", funding.reload);
    return () => window.removeEventListener("internradar:refresh", funding.reload);
  }, [funding.reload]);
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

function SourcesPage({ hosted }: { hosted: boolean }) {
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
        : sources.data && <SourcesTable sources={sources.data} hosted={hosted} />}
    </Card>
  );
}
