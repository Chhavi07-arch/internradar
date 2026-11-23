import type { Overview } from "../api";
import { cx } from "./ui";

interface Tile {
  label: string;
  value: number;
  hint: string;
  emphasis?: boolean;
}

export function StatTiles({ overview }: { overview: Overview }) {
  const tiles: Tile[] = [
    { label: "Open internships", value: overview.open_internships, hint: "across every source" },
    { label: "Unread", value: overview.unseen, hint: "not opened yet", emphasis: overview.unseen > 0 },
    { label: "Due this week", value: overview.due_this_week, hint: "deadline within 7 days" },
    { label: "Saved", value: overview.saved, hint: "your shortlist" },
    { label: "Funded startups", value: overview.funded_rounds_30d, hint: "tech rounds, last 30 days" },
  ];
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
      {tiles.map((tile) => (
        <div key={tile.label} className="rounded-xl border border-line bg-surface px-4 py-3.5">
          <p className="text-xs font-medium text-muted">{tile.label}</p>
          <p className={cx("mt-1.5 text-3xl font-semibold tracking-tight", tile.emphasis ? "text-accent" : "text-ink")}>
            {tile.value.toLocaleString("en-IN")}
          </p>
          <p className="mt-1 text-xs text-muted">{tile.hint}</p>
        </div>
      ))}
    </div>
  );
}
