import { Bookmark, CalendarClock, EyeOff, Eye, ExternalLink, TrendingUp } from "lucide-react";
import type { Opportunity, Origin } from "../api";
import { ORIGINS, dueIn, place, sourceLabel, timeAgo } from "../format";
import { Badge, Button, Empty, cx } from "./ui";

interface Props {
  items: Opportunity[];
  origin: Origin | null;
  onOrigin: (origin: Origin | null) => void;
  onOpen: (item: Opportunity) => void;
  onToggle: (item: Opportunity, field: "bookmarked" | "hidden") => void;
  emptyHint: string;
}

export function OpportunityList({ items, origin, onOrigin, onOpen, onToggle, emptyHint }: Props) {
  return (
    <div>
      <div className="flex flex-wrap gap-1.5 border-b border-line px-5 py-2.5" role="group" aria-label="Filter by source">
        {[{ key: null, label: "All sources" }, ...ORIGINS].map((o) => (
          <button
            key={o.key ?? "all"}
            onClick={() => onOrigin(o.key as Origin | null)}
            aria-pressed={origin === o.key}
            className={cx(
              "rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
              origin === o.key ? "bg-accent/15 text-[#8cb8f0]" : "text-muted hover:bg-raised hover:text-ink",
            )}
          >
            {o.label}
          </button>
        ))}
      </div>
      {items.length === 0 ? (
        <Empty title="Nothing here" hint={emptyHint} />
      ) : (
        <ul className="divide-y divide-line">
          {items.map((item) => (
            <Row key={item.id} item={item} onOpen={onOpen} onToggle={onToggle} />
          ))}
        </ul>
      )}
    </div>
  );
}

function Row({ item, onOpen, onToggle }: { item: Opportunity } & Pick<Props, "onOpen" | "onToggle">) {
  const due = item.deadline_at ? dueIn(item.deadline_at) : null;
  const link = item.apply_url ?? item.url;
  return (
    <li className="group flex items-center gap-2 px-3 py-4 sm:gap-3 sm:px-5 transition-colors hover:bg-raised/60">
      <span
        aria-label={item.seen ? undefined : "Unread"}
        className={cx("size-2 shrink-0 rounded-full", item.seen ? "bg-transparent" : "bg-accent")}
      />
      <button onClick={() => onOpen(item)} className="min-w-0 flex-1 text-left rounded-sm">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className={cx("break-words text-sm sm:text-base", item.seen ? "text-ink-2" : "font-semibold text-ink")}>
            {item.title}
          </span>
          {due && !due.past && (
            <Badge tone={due.urgent ? "critical" : "warning"}>
              <CalendarClock className="size-3" aria-hidden /> {due.label}
            </Badge>
          )}
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">
          <span className="font-medium text-ink-2">{item.company}</span>
          {item.funding && (
            <Badge tone="good" title="Recent funding round: a hiring signal">
              <TrendingUp className="size-3" aria-hidden /> {item.funding}
            </Badge>
          )}
          <span>·</span>
          <span>{place(item.city, item.is_remote)}</span>
          <span>·</span>
          <span>{item.sources.map(sourceLabel).filter((v, i, all) => all.indexOf(v) === i).join(", ")}</span>
          <span>·</span>
          <span title="First seen">{timeAgo(item.first_seen_at)}</span>
        </div>
      </button>
      <div className="flex shrink-0 items-center gap-0.5">
        <Button aria-label={item.bookmarked ? "Remove from saved" : "Save"} onClick={() => onToggle(item, "bookmarked")}
          className={item.bookmarked ? "text-accent hover:text-accent" : ""}>
          <Bookmark className="size-4" fill={item.bookmarked ? "currentColor" : "none"} />
        </Button>
        <Button aria-label={item.hidden ? "Unhide" : "Hide"} onClick={() => onToggle(item, "hidden")}
          className="md:opacity-0 md:group-hover:opacity-100 focus-visible:opacity-100">
          {item.hidden ? <Eye className="size-4" /> : <EyeOff className="size-4" />}
        </Button>
        {link && (
          <a href={link} target="_blank" rel="noreferrer" aria-label="Open posting"
            className="rounded-lg p-1.5 text-muted transition-colors hover:bg-raised hover:text-ink">
            <ExternalLink className="size-4" />
          </a>
        )}
      </div>
    </li>
  );
}
