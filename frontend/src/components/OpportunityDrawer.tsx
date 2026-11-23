import { Bookmark, ExternalLink, TrendingUp, X } from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";
import type { Opportunity, OpportunityDetail, OpportunityPatch } from "../api";
import { api } from "../api";
import { dueIn, place, shortDate, sourceLabel, timeAgo } from "../format";
import { useData } from "../hooks";
import { Badge, Button } from "./ui";

interface Props {
  id: number;
  onClose: () => void;
  onChange: (updated: Opportunity) => void;
}

export function OpportunityDrawer({ id, onClose, onChange }: Props) {
  const { data, setData, error } = useData(() => api.opportunity(id), [id]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function update(patch: OpportunityPatch) {
    const updated = await api.update(id, patch);
    setData((d) => (d ? { ...d, ...updated } : d));
    onChange(updated);
  }

  return (
    <div className="fixed inset-0 z-40 flex justify-end" role="dialog" aria-modal="true" aria-label="Internship details">
      <button aria-label="Close" onClick={onClose} className="absolute inset-0 bg-black/60 backdrop-blur-[2px]" />
      <aside className="relative flex h-full w-full max-w-xl flex-col border-l border-line bg-surface shadow-2xl">
        <div className="flex items-center justify-end border-b border-line px-4 py-2.5">
          <Button aria-label="Close" onClick={onClose}><X className="size-4" /></Button>
        </div>
        <div className="flex-1 overflow-y-auto px-6 py-5">
          {error && <p className="text-sm text-[#f08080]">{error}</p>}
          {data && <Detail item={data} onUpdate={update} />}
        </div>
      </aside>
    </div>
  );
}

function Detail({ item, onUpdate }: { item: OpportunityDetail; onUpdate: (patch: OpportunityPatch) => Promise<void> }) {
  const [notes, setNotes] = useState(item.notes ?? "");
  const due = item.deadline_at ? dueIn(item.deadline_at) : null;
  const link = item.apply_url ?? item.url;
  const eligibility = item.eligibility;
  const criteria = eligibility && [
    eligibility.min_cgpa != null && `CGPA ≥ ${eligibility.min_cgpa}`,
    eligibility.branches?.length && eligibility.branches.join(", "),
    eligibility.batches?.length && `Batch ${eligibility.batches.join(", ")}`,
    eligibility.backlogs_allowed === false && "No active backlogs",
  ].filter(Boolean);

  return (
    <div className="space-y-6">
      <div>
        <p className="text-sm font-medium text-ink-2">{item.company}</p>
        <h2 className="mt-1 text-xl font-semibold leading-snug text-ink">{item.title}</h2>
        <div className="mt-3 flex flex-wrap gap-1.5">
          <Badge>{place(item.city, item.is_remote)}</Badge>
          {item.stipend && <Badge>{item.stipend}</Badge>}
          {due && <Badge tone={due.past ? "neutral" : due.urgent ? "critical" : "warning"}>{due.label}</Badge>}
          {item.funding && <Badge tone="good"><TrendingUp className="size-3" aria-hidden /> {item.funding}</Badge>}
        </div>
      </div>

      <div className="flex gap-2">
        {link && (
          <a href={link} target="_blank" rel="noreferrer"
            className="inline-flex items-center gap-2 rounded-lg bg-accent px-3.5 py-2 text-sm font-medium text-white hover:bg-[#4d95ea]">
            {item.apply_url ? "Apply" : "View posting"} <ExternalLink className="size-3.5" />
          </a>
        )}
        <Button variant="outline" onClick={() => onUpdate({ bookmarked: !item.bookmarked })}>
          <Bookmark className="size-4" fill={item.bookmarked ? "currentColor" : "none"} />
          {item.bookmarked ? "Saved" : "Save"}
        </Button>
        <Button variant="outline" onClick={() => onUpdate({ hidden: !item.hidden })}>
          {item.hidden ? "Unhide" : "Not interested"}
        </Button>
      </div>

      {criteria && criteria.length > 0 && (
        <Section title="Eligibility">
          <ul className="flex flex-wrap gap-1.5">
            {criteria.map((c) => <li key={String(c)}><Badge tone="accent">{c}</Badge></li>)}
          </ul>
          {eligibility?.notes && <p className="mt-2 text-sm text-ink-2">{eligibility.notes}</p>}
        </Section>
      )}

      <Section title={`Seen on ${item.sightings.length} source${item.sightings.length === 1 ? "" : "s"}`}>
        <ul className="divide-y divide-line rounded-lg border border-line text-sm">
          {item.sightings.map((s) => (
            <li key={s.source + s.first_seen_at} className="flex items-center justify-between gap-3 px-3 py-2">
              <span className="text-ink-2">
                {sourceLabel(s.source)} <span className="text-muted">({s.source})</span>
              </span>
              <span className="flex items-center gap-2 text-xs text-muted">
                {s.closed ? "taken down" : `since ${shortDate(s.first_seen_at)}`}
                {s.url && (
                  <a href={s.url} target="_blank" rel="noreferrer" aria-label={`Open on ${s.source}`} className="hover:text-ink">
                    <ExternalLink className="size-3.5" />
                  </a>
                )}
              </span>
            </li>
          ))}
        </ul>
        <p className="mt-2 text-xs text-muted">
          First seen {timeAgo(item.first_seen_at)}
          {item.posted_at && ` · posted ${shortDate(item.posted_at)}`}
        </p>
      </Section>

      <Section title="Notes">
        <textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          onBlur={() => notes !== (item.notes ?? "") && onUpdate({ notes })}
          placeholder="Referral contact, things to mention, questions… saved when you click away"
          rows={3}
          className="w-full resize-y rounded-lg border border-line bg-page px-3 py-2 text-sm text-ink placeholder:text-muted focus:border-accent focus:outline-none"
        />
      </Section>

      {item.description && (
        <Section title="Description">
          <div className="max-h-[28rem] overflow-y-auto whitespace-pre-wrap rounded-lg border border-line bg-page px-4 py-3 text-sm leading-relaxed text-ink-2">
            {item.description}
          </div>
        </Section>
      )}
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section>
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted">{title}</h3>
      {children}
    </section>
  );
}
