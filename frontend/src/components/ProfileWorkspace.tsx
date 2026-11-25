import { Check, FileUp, Pencil, UserRound } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { normalizeProfile } from "../profile";
import type { ProfileFields, StudentProfile } from "../profile";
import { mergeResumeCandidates } from "../resume";
import type { ResumeCandidate } from "../resume";
import { ProfileEditor } from "./ProfileEditor";
import { ResumeImport } from "./ResumeImport";
import type { ResumeSource } from "./ResumeImport";
import { Button, Card } from "./ui";

export interface ProfileWorkspaceProps {
  profile: StudentProfile;
  onboarding: boolean;
  onSaved: (profile: StudentProfile) => void;
  onSkipped: (profile: StudentProfile) => void;
  onBack: () => void;
}

type Draft = { fields: ProfileFields; batch: string; source?: ResumeSource };
type View = "summary" | "edit" | "import";

function newDraft(profile: StudentProfile): Draft {
  const { name, college, branch, degree, batch, roles, skills, projects } = profile;
  return { fields: { name, college, branch, degree, batch, roles, skills, projects }, batch: batch?.toString() ?? "" };
}

function SummaryTags({ title, empty, tags }: { title: string; empty: string; tags: string[] }) {
  return (
    <div className="min-w-0 space-y-2">
      <h3 className="text-sm font-semibold">{title}</h3>
      {tags.length ? (
        <ul aria-label={title} className="flex flex-wrap gap-2">
          {tags.map((tag) => <li key={tag} className="max-w-full wrap-break-word rounded-lg border border-line bg-raised px-2.5 py-1 text-sm text-ink-2">{tag}</li>)}
        </ul>
      ) : <p className="text-sm text-muted">{empty}</p>}
    </div>
  );
}

function sourceName(source: ResumeSource) {
  return source.kind === "file" ? source.filename : "Pasted resume text";
}

export function ProfileWorkspace({ profile, onboarding, onSaved, onSkipped, onBack }: ProfileWorkspaceProps) {
  const [view, setView] = useState<View>("summary");
  // No draft when importing from the summary; cancel then returns to the summary.
  const [draft, setDraft] = useState<Draft | null>(null);
  const [savedSource, setSavedSource] = useState<ResumeSource | null>(null);
  const [saved, setSaved] = useState(false);
  const [skipping, setSkipping] = useState(false);
  const [skipError, setSkipError] = useState("");
  const focusTarget = useRef<HTMLDivElement>(null);
  const previousView = useRef(view);

  useEffect(() => {
    if (previousView.current !== view) {
      previousView.current = view;
      focusTarget.current?.focus({ preventScroll: true });
      focusTarget.current?.closest("main")?.scrollTo({ top: 0 });
    }
  }, [view]);

  function open(next: View) {
    setSaved(false);
    setSkipError("");
    setView(next);
  }

  function cancelEdit() {
    setDraft(null);
    open("summary");
  }

  function applyImport(selected: ResumeCandidate[], source: ResumeSource) {
    const current = draft ?? newDraft(profile);
    const fields = mergeResumeCandidates(current.fields, selected);
    const batch = selected.some((candidate) => candidate.field === "batch") ? fields.batch?.toString() ?? "" : current.batch;
    setDraft({ fields, batch, source });
    open("edit");
  }

  function finishSave(updated: StudentProfile) {
    if (draft?.source) setSavedSource(draft.source);
    setDraft(null);
    setSaved(true);
    setView("summary");
    onSaved(updated);
  }

  async function skip() {
    if (skipping) return;
    setSkipping(true);
    setSkipError("");
    try {
      onSkipped(normalizeProfile(await api.skipProfile()));
    } catch (e) {
      setSkipError(`Could not skip setup: ${e instanceof Error ? e.message : "try again."}`);
    } finally {
      setSkipping(false);
    }
  }

  const details = [
    ["College or university", profile.college], ["Degree", profile.degree],
    ["Branch or major", profile.branch], ["Graduation year", profile.batch],
  ] as const;

  return (
    <div ref={focusTarget} tabIndex={-1} aria-label={view === "summary" ? "Profile summary" : view === "edit" ? "Edit profile" : "Import resume"}
      className="mx-auto max-w-3xl space-y-4">
      {view === "summary" && (
        <>
          <section className="space-y-4 rounded-xl border border-accent/30 bg-linear-to-br from-accent/15 to-surface p-4 sm:p-6" aria-labelledby="profile-summary">
            <div>
              <p className="mb-2 flex items-center gap-2 text-xs font-semibold tracking-wide text-[#9dc9ff]"><UserRound className="size-4" aria-hidden="true" /> STUDENT PROFILE</p>
              <h2 id="profile-summary" className="wrap-break-word text-xl font-semibold tracking-tight sm:text-2xl">{onboarding ? "Set up your profile" : profile.name || "Your profile"}</h2>
              {onboarding && <p className="mt-2 text-sm text-ink-2">Add your education, target roles, skills and projects, or skip setup and do it later.</p>}
              {onboarding && profile.name && <p className="mt-2 wrap-break-word text-sm font-medium">{profile.name}</p>}
            </div>
            <dl className="grid grid-cols-1 gap-3 border-t border-line pt-4 sm:grid-cols-2">
              {details.map(([label, value]) => (
                <div key={label} className="min-w-0">
                  <dt className="text-xs text-muted">{label}</dt>
                  <dd className="mt-0.5 wrap-break-word text-sm text-ink-2">{value || "Not added"}</dd>
                </div>
              ))}
            </dl>
            <div className="flex flex-wrap items-center gap-2">
              <Button type="button" variant="primary" disabled={skipping} onClick={() => { setDraft(newDraft(profile)); open("edit"); }}>
                <Pencil className="size-4" aria-hidden="true" /> Edit profile
              </Button>
              <Button type="button" variant="outline" disabled={skipping} onClick={() => open("import")}>
                <FileUp className="size-4" aria-hidden="true" /> Import resume
              </Button>
              <Button type="button" disabled={skipping} onClick={onboarding ? () => void skip() : onBack}>
                {skipping ? "Skipping…" : onboarding ? "Skip for now" : "Back to inbox"}
              </Button>
            </div>
            {saved && <p role="status" className="flex items-center gap-1 text-sm text-ink-2"><Check className="size-4" aria-hidden="true" /> Profile saved.</p>}
            {skipError && <p role="alert" className="text-sm text-[#f08080]">{skipError}</p>}
          </section>
          <Card className="space-y-4 p-4 sm:p-5">
            <SummaryTags title="Target roles" tags={profile.roles} empty="No target roles added yet." />
            <SummaryTags title="Skills" tags={profile.skills} empty="No skills added yet." />
          </Card>
          <Card className="space-y-3 p-4 sm:p-5">
            <h3 className="text-sm font-semibold">Projects</h3>
            {profile.projects.length ? (
              <ul className="divide-y divide-line">
                {profile.projects.map((project) => (
                  <li key={project.id} className="space-y-1 py-3 first:pt-0 last:pb-0">
                    <p className="wrap-break-word text-sm font-medium">{project.name}</p>
                    {project.description && <p className="whitespace-pre-wrap wrap-break-word text-sm text-ink-2">{project.description}</p>}
                  </li>
                ))}
              </ul>
            ) : <p className="text-sm text-muted">No projects added yet.</p>}
          </Card>
          <Card className="space-y-2 p-4 sm:p-5">
            <h3 className="text-sm font-semibold">Resume import</h3>
            {savedSource && <p className="wrap-break-word text-sm text-ink-2">Last import: {sourceName(savedSource)}</p>}
            <p className="text-sm text-muted">{savedSource ? "Selected profile fields were saved. The resume itself is not stored." : "No resume import in this view. Files are not uploaded or stored."}</p>
          </Card>
        </>
      )}
      {draft && (
        <div hidden={view !== "edit"}>
          {draft.source && <p role="status" className="mb-4 wrap-break-word rounded-lg border border-accent/30 bg-accent/10 px-3 py-2 text-sm text-[#9dc9ff]">
            Applied to draft from {sourceName(draft.source)}. Review the changes before saving.
          </p>}
          <ProfileEditor draft={draft.fields} batch={draft.batch} onboarding={onboarding} onSaved={finishSave}
            onEdit={(patch) => setDraft((current) => current && { ...current, fields: { ...current.fields, ...patch } })}
            onBatchChange={(batch) => setDraft((current) => current && { ...current, batch })}
            onImport={() => open("import")} onCancel={cancelEdit} />
        </div>
      )}
      {view === "import" && <>
        {onboarding && <div className="space-y-2">
          <Button type="button" variant="outline" disabled={skipping} onClick={() => void skip()}>
            {skipping ? "Skipping…" : "Skip setup and go to inbox"}
          </Button>
          {skipError && <p role="alert" className="text-sm text-[#f08080]">{skipError}</p>}
        </div>}
        <ResumeImport onApply={applyImport} onCancel={() => open(draft ? "edit" : "summary")} />
      </>}
    </div>
  );
}
