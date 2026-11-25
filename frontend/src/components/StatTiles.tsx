import type { Overview } from "../api";

interface Tile {
  label: string;
  value: number;
  hint: string;
}

export function StatTiles({ overview }: { overview: Overview }) {
  const tiles: Tile[] = [
    { label: "Open internships", value: overview.open_internships, hint: "across every source" },
    { label: "Unread", value: overview.unseen, hint: "not opened yet" },
    { label: "Due this week", value: overview.due_this_week, hint: "deadline within 7 days" },
    { label: "Saved", value: overview.saved, hint: "your shortlist" },
    { label: "Funded startups", value: overview.funded_rounds_30d, hint: "tech rounds, last 30 days" },
  ];
  return (
    <div className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3 xl:grid-cols-5">
      {tiles.map((tile) => (
        <div key={tile.label} className="min-w-0">
          <p className="text-xs font-medium text-ink-2">{tile.label}</p>
          <p className="mt-1 text-2xl font-semibold tabular-nums text-ink">
            {tile.value.toLocaleString("en-IN")}
          </p>
          <p className="sr-only">{tile.hint}</p>
        </div>
      ))}
    </div>
  );
}
