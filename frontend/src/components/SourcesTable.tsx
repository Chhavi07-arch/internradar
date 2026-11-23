import { CircleAlert, CircleCheck } from "lucide-react";
import type { SourceHealth } from "../api";
import { Empty } from "./ui";

export function SourcesTable({ sources }: { sources: SourceHealth[] }) {
  if (sources.length === 0) return <Empty title="No fetch has run yet" hint="Press Refresh to fetch every source." />;
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[680px] text-left text-sm">
        <thead className="text-xs text-muted">
          <tr className="border-b border-line">
            <th className="px-5 py-2.5 font-medium">Source</th>
            <th className="px-5 py-2.5 font-medium">Status</th>
            {["Fetched", "Relevant", "New", "Time"].map((h) => (
              <th key={h} className="px-5 py-2.5 text-right font-medium">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-line tabular-nums">
          {sources.map((s) => (
            <tr key={s.source} className="transition-colors hover:bg-raised/60">
              <td className="px-5 py-2.5 font-medium text-ink-2">{s.source}</td>
              <td className="px-5 py-2.5">
                {/* status colour always paired with an icon and a label */}
                {s.ok ? (
                  <span className="inline-flex items-center gap-1.5 text-[#5fd35f]">
                    <CircleCheck className="size-4" aria-hidden /> Healthy
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1.5 text-[#f08080]" title={s.error ?? undefined}>
                    <CircleAlert className="size-4" aria-hidden /> Failed
                    <span className="max-w-64 truncate text-xs text-muted">{s.error}</span>
                  </span>
                )}
              </td>
              <td className="px-5 py-2.5 text-right text-ink-2">{s.fetched}</td>
              <td className="px-5 py-2.5 text-right text-ink-2">{s.kept}</td>
              <td className="px-5 py-2.5 text-right">{s.new ? <span className="font-semibold text-ink">{s.new}</span> : <span className="text-muted">0</span>}</td>
              <td className="px-5 py-2.5 text-right text-muted">{(s.duration_ms / 1000).toFixed(1)}s</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
