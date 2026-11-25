import { ArrowRight, FileUp, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import type { FormEvent } from "react";
import { api } from "../api";
import { normalizeProfile, PROFILE_LIMITS as limits, uniqueProfileTags } from "../profile";
import type { ProfileFields, ProfileProject, StudentProfile } from "../profile";
import { inputClass } from "./ResumeImport";
import { Button, Card } from "./ui";

const TEXT_FIELDS = [
  ["name", "Name"], ["college", "College or university"], ["degree", "Degree"], ["branch", "Branch or major"],
] as const;

function TagField({ label, values, onChange, max }: {
  label: "Target roles" | "Skills"; values: string[]; onChange: (values: string[]) => void; max: number;
}) {
  const [entry, setEntry] = useState("");
  const full = values.length >= max;
  function add() {
    onChange(uniqueProfileTags([...values, ...entry.split(/[,\n]/)]).slice(0, max));
    setEntry("");
  }
  return (
    <div className="space-y-2">
      <label className="block text-sm font-medium" htmlFor={label === "Skills" ? "profile-skills" : "profile-roles"}>{label}</label>
      <div className="flex gap-2">
        <input className={inputClass} id={label === "Skills" ? "profile-skills" : "profile-roles"} value={entry}
          placeholder={label === "Skills" ? "e.g. Python" : "e.g. Backend developer"}
          disabled={full} maxLength={limits.tag} onChange={(e) => setEntry(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.nativeEvent.isComposing) { e.preventDefault(); add(); }
          }} />
        <Button type="button" variant="outline" disabled={full || !entry.trim()} onClick={add}>Add</Button>
      </div>
      {full && <p className="text-xs text-muted">{max} item limit reached.</p>}
      {!!values.length && <ul className="flex flex-wrap gap-2" aria-label={`${label} added`}>
        {values.map((value, i) => (
          <li key={i} className="flex max-w-full items-center gap-1 rounded-lg border border-line bg-raised pl-2.5 text-sm">
            <span className="min-w-0 wrap-break-word">{value}</span>
            <Button type="button" aria-label={`Remove ${value}`} onClick={() => onChange(values.filter((_, index) => index !== i))}>
              <span aria-hidden="true">×</span>
            </Button>
          </li>
        ))}
      </ul>}
    </div>
  );
}

export function ProfileEditor({ draft, batch, onboarding, onEdit, onBatchChange, onSaved, onImport, onCancel }: {
  draft: ProfileFields;
  batch: string;
  onboarding: boolean;
  onEdit: (patch: Partial<ProfileFields>) => void;
  onBatchChange: (batch: string) => void;
  onSaved: (profile: StudentProfile) => void;
  onImport: () => void;
  onCancel: () => void;
}) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  function edit(patch: Partial<ProfileFields>) {
    setError("");
    onEdit(patch);
  }

  function editProject(id: string, patch: Partial<ProfileProject>) {
    edit({ projects: draft.projects.map((project) => project.id === id ? { ...project, ...patch } : project) });
  }

  async function save(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (saving) return;
    setError("");
    const graduation = batch.trim() ? Number(batch) : null;
    if (graduation !== null && (!/^\d{4}$/.test(batch.trim()) || graduation < limits.minBatch || graduation > limits.maxBatch)) {
      setError("Enter a graduation year between 2000 and 2100, or leave it blank.");
      return;
    }
    const fields = {
      ...draft, batch: graduation,
      name: draft.name.trim(), college: draft.college.trim(), branch: draft.branch.trim(), degree: draft.degree.trim(),
      roles: uniqueProfileTags(draft.roles), skills: uniqueProfileTags(draft.skills),
      projects: draft.projects.map((project) => ({ ...project, name: project.name.trim(), description: project.description.trim() })),
    };
    if (fields.projects.some((project) => !project.name)) {
      setError("Each project needs a name. Remove any project you don’t want to save.");
      return;
    }
    setSaving(true);
    try {
      onSaved(normalizeProfile(await api.saveProfile(fields)));
    } catch (e) {
      setError(`Could not save profile: ${e instanceof Error ? e.message : "try again."}`);
    } finally {
      setSaving(false);
    }
  }

  return (
    <form className="space-y-4" onSubmit={save}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-xl font-semibold tracking-tight">Edit profile</h2>
          <p className="mt-1 text-sm text-ink-2">Review and save the details you want to keep.</p>
        </div>
        <Button type="button" variant="outline" disabled={saving} onClick={onImport}>
          <FileUp className="size-4" aria-hidden="true" /> Import resume
        </Button>
      </div>
      <fieldset disabled={saving} className="min-w-0 space-y-4 border-0 p-0">
        <Card className="space-y-4 p-4 sm:p-5">
          <h2 className="text-sm font-semibold">Personal and academic details</h2>
          <div className="grid gap-4 sm:grid-cols-2">
            {TEXT_FIELDS.map(([field, label]) => (
              <label key={field} className="block space-y-2 text-sm font-medium">
                <span>{label}</span>
                <input className={inputClass} value={draft[field]} maxLength={limits[field]} autoComplete={field === "name" ? "name" : undefined}
                  onChange={(e) => edit({ [field]: e.target.value })} />
              </label>
            ))}
            <label className="block space-y-2 text-sm font-medium">
              <span>Graduation year</span>
              <input className={inputClass} inputMode="numeric" maxLength={4} value={batch} placeholder="e.g. 2027"
                onChange={(e) => { setError(""); onBatchChange(e.target.value); }} />
            </label>
          </div>
        </Card>
        <Card className="space-y-4 p-4 sm:p-5">
          <h2 className="text-sm font-semibold">Roles and skills</h2>
          <TagField label="Target roles" values={draft.roles} max={limits.roles} onChange={(roles) => edit({ roles })} />
          <TagField label="Skills" values={draft.skills} max={limits.skills} onChange={(skills) => edit({ skills })} />
        </Card>
        <Card className="space-y-4 p-4 sm:p-5">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-sm font-semibold">Projects</h2>
            <Button type="button" variant="outline" disabled={draft.projects.length >= limits.projects}
              onClick={() => edit({ projects: [...draft.projects, { id: crypto.randomUUID(), name: "", description: "" }] })}>
              <Plus className="size-4" aria-hidden="true" /> Add project
            </Button>
          </div>
          {draft.projects.length === 0 && <p className="text-sm text-muted">No projects added yet.</p>}
          {draft.projects.map((project, i) => (
            <fieldset key={project.id} className="min-w-0 space-y-3 rounded-lg border border-line px-3 pb-3">
              <legend className="px-1 text-xs font-medium text-ink-2">Project {i + 1}</legend>
              <div className="flex items-end gap-2">
                <label className="block min-w-0 flex-1 space-y-2 text-sm font-medium">
                  <span>Project name</span>
                  <input required className={inputClass} value={project.name} maxLength={limits.projectName} onChange={(e) => editProject(project.id, { name: e.target.value })} />
                </label>
                <Button type="button" className="mb-0.5" aria-label={`Remove project ${project.name || i + 1}`}
                  onClick={() => edit({ projects: draft.projects.filter((item) => item.id !== project.id) })}>
                  <Trash2 className="size-4" aria-hidden="true" />
                </Button>
              </div>
              <label className="block space-y-2 text-sm font-medium">
                <span>Description</span>
                <textarea className={inputClass} rows={3} value={project.description} maxLength={limits.projectDescription}
                  onChange={(e) => editProject(project.id, { description: e.target.value })} />
              </label>
            </fieldset>
          ))}
        </Card>
      </fieldset>
      {error && <p role="alert" className="rounded-lg border border-critical/40 bg-critical/10 px-3 py-2 text-sm text-[#f08080]">{error}</p>}
      <div className="flex flex-wrap items-center gap-3 pb-2">
        <Button type="submit" variant="primary" disabled={saving}>
          {saving ? "Saving…" : onboarding ? "Save and continue" : "Save profile"}
          {onboarding && <ArrowRight className="size-4" aria-hidden="true" />}
        </Button>
        <Button type="button" variant="outline" disabled={saving} onClick={onCancel}>Cancel</Button>
      </div>
    </form>
  );
}
