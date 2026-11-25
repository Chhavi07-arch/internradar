import * as Dialog from "@radix-ui/react-dialog";
import { Bookmark, ExternalLink, TrendingUp, X } from "lucide-react";
import { type ReactNode, useLayoutEffect, useRef, useState } from "react";
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

  const [updating, setUpdating] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const busy = useRef(false);
  const returnFocus = useRef<HTMLElement | null>(null);

  useLayoutEffect(() => {
    if (document.activeElement instanceof HTMLElement && !returnFocus.current) {
      returnFocus.current = document.activeElement;
    }
  }, []);

  async function update(patch: OpportunityPatch) {
    if (busy.current) return;
    busy.current = true;
    setUpdating(true);
    setSaveError(null);
    try {
      const updated = await api.update(id, patch);
      setData((d) => (d ? { ...d, ...updated } : d));
      onChange(updated);
    } catch {
      setSaveError("Changes could not be saved. Try again.");
    } finally {
      busy.current = false;
      setUpdating(false);
    }
  }

  return (
    <Dialog.Root open onOpenChange={(open) => { if (!open) onClose(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/65 backdrop-blur-[2px]" />
        <Dialog.Content className="fixed inset-y-0 right-0 z-50 flex w-full max-w-xl flex-col border-l border-line bg-surface shadow-2xl"
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            if (returnFocus.current?.isConnected) returnFocus.current.focus();
            else document.getElementById("opportunity-feed")?.focus();
          }}>
          <div className="flex items-center justify-between gap-4 border-b border-line px-5 py-3">
            <Dialog.Title className="text-xs font-semibold uppercase tracking-widest text-ink-2">Internship details</Dialog.Title>
            <Dialog.Description className="sr-only">Review the opportunity and manage your saved listing and notes.</Dialog.Description>
            <Dialog.Close className="grid size-9 place-items-center rounded-lg text-ink-2 hover:bg-raised" aria-label="Close internship details"><X className="size-4" /></Dialog.Close>
          </div>
          <div className="flex-1 overflow-y-auto px-5 py-5 sm:px-6">
            {error && <p role="alert" className="text-sm text-[#f08080]">{error}</p>}
            {!data && !error && <p role="status" className="text-sm text-ink-2">Loading internship…</p>}
            {saveError && <p role="alert" className="mb-4 text-sm text-[#f08080]">{saveError}</p>}
            {data && <Detail key={id} item={data} onUpdate={update} updating={updating} />}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function Detail({ item, onUpdate, updating }: { item: OpportunityDetail; onUpdate: (patch: OpportunityPatch) => Promise<void>; updating: boolean }) {
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

      <div className="flex flex-wrap gap-2">
        {link && (
          <a href={link} target="_blank" rel="noreferrer"
            className="inline-flex items-center gap-2 rounded-lg bg-accent px-3.5 py-2 text-sm font-medium text-white hover:bg-[#4d95ea]">
            {item.apply_url ? "Apply" : "View posting"} <ExternalLink className="size-3.5" />
          </a>
        )}
        <Button variant="outline" disabled={updating} onClick={() => onUpdate({ bookmarked: !item.bookmarked })}>
          <Bookmark className="size-4" fill={item.bookmarked ? "currentColor" : "none"} />
          {item.bookmarked ? "Saved" : "Save"}
        </Button>
        <Button variant="outline" disabled={updating} onClick={() => onUpdate({ hidden: !item.hidden })}>
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
          aria-label="Private internship notes"
          placeholder="Referral contact, things to mention, questions…"
          rows={3}
          className="w-full resize-y rounded-lg border border-line bg-page px-3 py-2 text-sm text-ink placeholder:text-muted focus:border-accent focus:outline-none"
        />
        <Button variant="outline" className="mt-2" disabled={updating || notes.trim() === (item.notes ?? "")} onClick={() => void onUpdate({ notes: notes.trim() })}>Save note</Button>
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
