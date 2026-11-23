import { ExternalLink } from "lucide-react";
import type { FundingRound } from "../api";
import { shortDate } from "../format";
import { Badge, Empty } from "./ui";

export function FundingTable({ rounds }: { rounds: FundingRound[] }) {
  if (rounds.length === 0) {
    return <Empty title="No funding rounds yet" hint="Run `uv run internradar radar` to scan funding news." />;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[760px] text-left text-sm">
        <thead className="text-xs text-muted">
          <tr className="border-b border-line">
            {["Date", "Startup", "Round", "Amount", "Led by", "Careers board", "Interns open"].map((h) => (
              <th key={h} className="px-5 py-2.5 font-medium">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {rounds.map((r) => (
            <tr key={r.id} className="transition-colors hover:bg-raised/60">
              <td className="whitespace-nowrap px-5 py-3 tabular-nums text-muted">{shortDate(r.published_at)}</td>
              <td className="px-5 py-3">
                <a href={r.url} target="_blank" rel="noreferrer" title={r.headline}
                  className="inline-flex items-center gap-1.5 font-medium text-ink hover:text-accent">
                  {r.company} <ExternalLink className="size-3 text-muted" />
                </a>
                {r.mentions > 1 && <span className="ml-2 text-xs text-muted">{r.mentions} outlets</span>}
              </td>
              <td className="px-5 py-3 text-ink-2">{r.round ?? "—"}</td>
              <td className="whitespace-nowrap px-5 py-3 tabular-nums text-ink-2">{r.amount_text ?? "—"}</td>
              <td className="px-5 py-3 text-ink-2">{r.lead_investors ?? "—"}</td>
              <td className="px-5 py-3">
                {r.board ? <Badge tone="accent">watching {r.board}</Badge> : <span className="text-xs text-muted">not found yet</span>}
              </td>
              <td className="px-5 py-3 tabular-nums">
                {r.internships ? <Badge tone="good">{r.internships}</Badge> : <span className="text-muted">—</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
