import { FileText, ScanText } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { RESUME_LIMITS, extractResumeCandidates } from "../resume";
import type { ResumeCandidate } from "../resume";
import { readResumeFile } from "../resumeFile";
import { Button, Card } from "./ui";

const LABELS = { name: "Name", college: "College", branch: "Branch", degree: "Degree", batch: "Graduation year", skill: "Skill", project: "Project" };

export const inputClass = "w-full rounded-lg border border-line bg-page px-3 py-2 text-sm text-ink placeholder:text-muted focus-visible:outline-2 focus-visible:outline-accent disabled:opacity-60";

export type ResumeSource = { kind: "file"; filename: string } | { kind: "paste" };

export function ResumeImport({ onApply, onCancel }: {
  onApply: (selected: ResumeCandidate[], source: ResumeSource) => void;
  onCancel: () => void;
}) {
  const [paste, setPaste] = useState("");
  const [reading, setReading] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [source, setSource] = useState<ResumeSource | null>(null);
  const [candidates, setCandidates] = useState<ResumeCandidate[]>([]);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const active = useRef<AbortController | null>(null);
  useEffect(() => () => active.current?.abort(), []);

  function resetReview() {
    setSource(null);
    setCandidates([]);
    setSelected(new Set());
    setNotice("");
    setError("");
  }

  function preview(text: string, from: ResumeSource) {
    const result = extractResumeCandidates(text);
    setCandidates(result);
    setSource(from);
    if (result.length) {
      setNotice(`${result.length} suggestion${result.length === 1 ? "" : "s"} ready to review.`);
    } else {
      setNotice("No clear suggestions found. You can still fill in your profile manually.");
    }
  }

  async function chooseFile(file: File) {
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    resetReview();
    setReading(true);
    try {
      const text = await readResumeFile(file, controller.signal);
      if (!controller.signal.aborted) preview(text, { kind: "file", filename: file.name });
    } catch (e) {
      if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "Could not read that file.");
    } finally {
      if (active.current === controller) {
        active.current = null;
        if (!controller.signal.aborted) setReading(false);
      }
    }
  }

  function reviewPaste() {
    resetReview();
    try {
      preview(paste, { kind: "paste" });
      setPaste("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not read that text.");
    }
  }

  function apply() {
    if (source && selected.size) onApply(candidates.filter((_, i) => selected.has(i)), source);
  }

  return (
    <Card className="space-y-4 p-4 sm:p-5">
      <div className="flex flex-wrap items-start gap-3">
        <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-accent/15 text-[#9dc9ff]">
          <FileText className="size-4" aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1 basis-48">
          <h2 className="text-xl font-semibold tracking-tight">Import resume</h2>
          <p className="mt-1 text-sm text-ink-2">Your file and full resume text stay in this browser. Only profile fields you save are sent to InternRadar.</p>
        </div>
        <Button type="button" variant="outline" onClick={onCancel}>Cancel import</Button>
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <div className="space-y-2">
          <label className="block text-sm font-medium" htmlFor="resume-file">Choose a resume file</label>
          <input id="resume-file" type="file" accept=".pdf,.txt,application/pdf,text/plain" disabled={reading}
            aria-describedby="resume-file-help" className={`${inputClass} file:mr-3 file:rounded file:bg-raised file:px-2 file:py-1 file:text-ink`}
            onChange={(e) => {
              const file = e.currentTarget.files?.[0];
              e.currentTarget.value = "";
              if (file) void chooseFile(file);
            }} />
          <p id="resume-file-help" className="text-xs text-muted">Text-based PDF (up to 10 pages) or UTF-8 text, up to 5 MB. No OCR.</p>
          {reading && <div className="flex items-center gap-2">
            <span className="text-sm text-ink-2" role="status">Reading text locally…</span>
            <Button type="button" variant="outline" onClick={() => {
              active.current?.abort();
              setReading(false);
              setNotice("Resume extraction cancelled.");
            }}>Cancel</Button>
          </div>}
        </div>
        <div className="space-y-2">
          <label className="block text-sm font-medium" htmlFor="resume-paste">Or paste resume text</label>
          <textarea id="resume-paste" className={inputClass} value={paste} rows={3} disabled={reading}
            maxLength={RESUME_LIMITS.chars + 1} onChange={(e) => setPaste(e.target.value)} />
          <Button type="button" variant="outline" onClick={reviewPaste} disabled={reading || !paste.trim()}>
            <ScanText className="size-4" aria-hidden="true" /> Review pasted text
          </Button>
        </div>
      </div>
      {notice && <p role="status" className="text-sm text-[#9dc9ff]">{notice}</p>}
      {error && <p role="alert" className="text-sm text-[#f08080]">{error}</p>}
      {!!candidates.length && (
        <fieldset className="min-w-0 space-y-3 rounded-lg border border-line p-3 sm:p-4">
          <legend className="px-1 text-sm font-semibold">Review suggested changes</legend>
          <p className="text-sm text-ink-2">Select only what you want to use. Selected personal details replace the matching draft fields; skills and projects are added without duplicates, up to the profile limits.</p>
          <ul className="max-h-80 divide-y divide-line overflow-y-auto">
            {candidates.map((candidate, i) => (
              <li key={i}>
                <label className="flex cursor-pointer items-start gap-3 py-2 pr-2">
                  <input type="checkbox" className="mt-1 size-4 shrink-0 accent-accent" checked={selected.has(i)}
                    onChange={(e) => {
                      const checked = e.target.checked;
                      setSelected((previous) => {
                        const next = new Set(previous);
                        if (checked) next.add(i);
                        else next.delete(i);
                        return next;
                      });
                    }} />
                  <span className="min-w-0 wrap-break-word text-sm">
                    <span className="font-medium">{LABELS[candidate.field]} · {candidate.field === "project" ? candidate.value.name : candidate.value}</span>
                    {candidate.field === "project" && candidate.value.description && <span className="mt-0.5 block whitespace-pre-line text-ink-2">{candidate.value.description}</span>}
                    <span className="block text-xs text-muted">{candidate.evidence}</span>
                  </span>
                </label>
              </li>
            ))}
          </ul>
          <div className="flex flex-wrap gap-2">
            <Button type="button" variant="outline" disabled={!selected.size} onClick={apply}>Apply selected to draft</Button>
            <Button type="button" onClick={resetReview}>Discard suggestions</Button>
          </div>
        </fieldset>
      )}
    </Card>
  );
}
