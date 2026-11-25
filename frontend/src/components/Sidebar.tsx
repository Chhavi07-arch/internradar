import { Bookmark, CalendarClock, EyeOff, Inbox, Mail, Radar, ServerCog, Shield, Sparkles, TrendingUp, UserRound } from "lucide-react";
import type { ComponentType } from "react";
import type { Overview } from "../api";
import type { Route } from "../hooks";
import { cx } from "./ui";

const NAV: { route: Route; label: string; icon: ComponentType<{ className?: string }>; count?: keyof Overview }[] = [
  { route: "inbox", label: "Inbox", icon: Inbox, count: "unseen" },
  { route: "saved", label: "Saved", icon: Bookmark, count: "saved" },
  { route: "deadlines", label: "Deadlines", icon: CalendarClock, count: "due_this_week" },
  { route: "funding", label: "Funding radar", icon: TrendingUp },
  { route: "sources", label: "Sources", icon: ServerCog },
  { route: "hidden", label: "Hidden", icon: EyeOff },
  { route: "profile", label: "Profile", icon: UserRound },
  { route: "admin", label: "Admin", icon: Shield },
];

export function Sidebar({ route, onNavigate, overview, hostedRole }: {
  route: Route;
  onNavigate: (route: Route) => void;
  overview: Overview | null;
  hostedRole?: "student" | "admin";
}) {
  return (
    <nav className="flex shrink-0 flex-col border-b border-line bg-surface px-3 py-2 md:w-60 md:border-r md:border-b-0 md:py-4" aria-label="Main">
      <div className="mb-2 flex items-center gap-2.5 px-2 md:mb-6">
        <span className="grid size-8 place-items-center rounded-lg bg-accent/15 text-accent">
          <Radar className="size-4.5" />
        </span>
        <div>
          <p className="text-sm font-semibold tracking-tight text-ink">InternRadar</p>
          <p className="text-[11px] text-muted">STUDENT WORKSPACE</p>
        </div>
      </div>
      <ul className="flex flex-wrap gap-1 md:block md:space-y-0.5">
        {NAV.filter(({ route: r }) => (r !== "profile" || hostedRole) && (r !== "admin" || hostedRole === "admin"))
          .map(({ route: r, label, icon: Icon, count }) => {
          const value = count && overview ? (overview[count] as number) : 0;
          return (
            <li key={r}>
              <button
                onClick={() => onNavigate(r)}
                aria-current={route === r ? "page" : undefined}
                className={cx(
                  "flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm transition-colors",
                  route === r ? "bg-accent/20 font-semibold text-ink" : "text-ink-2 hover:bg-raised/60 hover:text-ink",
                )}
              >
                <Icon className={cx("size-4", route === r ? "text-accent" : "text-muted")} />
                <span className="flex-1 text-left">{label}</span>
                {value > 0 && <span className="text-xs tabular-nums text-muted">{value}</span>}
              </button>
            </li>
          );
        })}
      </ul>
      {overview && (!hostedRole || Object.keys(overview.inboxes).length > 0) && (
        <div className="mt-auto space-y-1.5 border-t border-line px-2 pt-4 text-xs text-muted">
          {Object.entries(overview.inboxes).map(([label, connected]) => (
            <p key={label} className="flex items-center gap-2">
              <Mail className="size-3.5" />
              <span className="flex-1 capitalize">{label} inbox</span>
              <span className={connected ? "text-[#5fd35f]" : ""}>{connected ? "connected" : "not connected"}</span>
            </p>
          ))}
          <p className="flex items-center gap-2">
            <Sparkles className="size-3.5" />
            <span className="flex-1">Email reader</span>
            <span className={overview.llm_configured ? "text-[#5fd35f]" : ""}>{overview.llm_configured ? "ready" : "no key"}</span>
          </p>
        </div>
      )}
    </nav>
  );
}
