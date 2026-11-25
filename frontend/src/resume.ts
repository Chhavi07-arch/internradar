import { PROFILE_LIMITS as limits, profileIdentity, uniqueProfileTags } from "./profile";
import type { ProfileFields, ProfileProject } from "./profile";

export const RESUME_LIMITS = { bytes: 5 * 1024 * 1024, pages: 10, chars: 50_000, ms: 8_000 } as const;

type ScalarField = "name" | "college" | "branch" | "degree" | "batch";
export type ResumeCandidate =
  | { [K in ScalarField]: { field: K; value: NonNullable<ProfileFields[K]>; evidence: string } }[ScalarField]
  | { field: "skill"; value: string; evidence: string }
  | { field: "project"; value: Omit<ProfileProject, "id">; evidence: string };

const headings = /^(technical skills|core skills|skills|technologies|education|academic background|(?:personal |academic |selected |technical )?projects|(?:work |professional )?experience|(?:work |employment )?history|(?:positions of )?responsibility|internships?|certifications?|awards?|achievements?|honou?rs|publications?|summary|objective|profile|interests?|languages?|activities|volunteer(?:ing)?|courses?|coursework|contact|references|links?)\s*(?::|$)\s*(.*)$/i;
const institution = /\b(?:university|institute|college|polytechnic)\b/i;
const degree = /^(b\.?\s?tech\.?|m\.?\s?tech\.?|b\.?\s?e\.?|m\.?\s?e\.?|b\.?\s?sc\.?|m\.?\s?sc\.?|bca|mca|bba|mba|bachelor(?:['’]s)?(?: of (?:technology|engineering|science|arts|computer applications))?|master(?:['’]s)?(?: of (?:technology|engineering|science|arts|computer applications))?)(?=\s|[,(–—|:-]|$)\s*(.*)$/i;
const bullet = /^(?:[•●▪◦‣–—]|[-*]|\d+[.)])\s+/u;
const person = /^\p{Lu}[\p{L}\p{M}.'’-]*(?: +(?:\p{Lu}[\p{L}\p{M}.'’-]*|de|van|von|al)){1,4}$/u;
const notPerson = /\b(?:resume|résumé|curriculum|vitae|developer|engineer|student|graduate|designer|analyst|scientist|data|software|intern|specialist|manager|portfolio|contact|profile)\b/i;

export function extractResumeCandidates(text: string): ResumeCandidate[] {
  if (text.length > RESUME_LIMITS.chars) throw new Error("Resume text is too long (50,000 character limit).");
  const lines = text.split(/\r\n?|\n/u).map((line) => line.replace(/\s+/gu, " ").trim()).filter(Boolean);
  const found: ResumeCandidate[] = [];
  const add = (candidate: ResumeCandidate) => {
    if (!found.some((item) => item.field === candidate.field)) found.push(candidate);
  };
  const unlabeledName = lines.slice(0, 2).find((line, index) =>
    (index === 0 || /^(résumé|resume|curriculum vitae)$/i.test(lines[0])) &&
    line.length <= limits.name && person.test(line) && !headings.test(line) && !notPerson.test(line) && !institution.test(line));
  let section = "";
  let project: Omit<ProfileProject, "id"> | null = null;
  const projectNames = new Set<string>();
  const skills: string[] = [];
  const flushProject = () => {
    if (project && !projectNames.has(profileIdentity(project.name)) && projectNames.size < limits.projects) {
      found.push({ field: "project", value: project, evidence: "Projects section" });
      projectNames.add(profileIdentity(project.name));
    }
    project = null;
  };
  const education: string[] = [];
  for (const raw of lines) {
    const labeledName = /^name\s*:\s*(.+)$/i.exec(raw)?.[1];
    if (labeledName && labeledName.length <= limits.name && person.test(labeledName)) {
      add({ field: "name", value: labeledName, evidence: "Explicit name label" });
    }
    const heading = headings.exec(raw);
    if (heading) {
      flushProject();
      section = heading[1].toLowerCase();
      if (!heading[2]) continue;
    }
    const line = heading?.[2] ?? raw.replace(bullet, "");
    if (/skills|^technologies$/.test(section)) {
      const list = line.replace(/^(?:languages|frameworks|tools|databases|platforms|programming|cloud|libraries|web)\s*:\s*/i, "");
      if (!/[,;|•]/.test(list) && list.split(/\s+/).length > 3) continue;
      for (const skill of list.split(/[,;|•]/)) {
        const item = skill.trim();
        if (skills.length < limits.skills * 4 && item.length <= limits.tag && /\p{L}/u.test(item) &&
            /^[\p{L}\p{N} .+#/_&()-]+$/u.test(item)) skills.push(item);
      }
    } else if (/^(education|academic background)$/.test(section)) {
      education.push(line);
    } else if (/projects$/.test(section)) {
      const labeledProject = /^project\s*:\s*(.+)$/i.exec(line)?.[1];
      const explicit = /^([^:]{2,140}?)\s*(?:\s+[–—-]\s+|:\s+)(.+)$/u.exec(labeledProject ?? line);
      const title = explicit && (labeledProject || !bullet.test(raw)) && !/https?:\/\//i.test(explicit[1])
        ? explicit[1] : labeledProject;
      if (title && title.length <= limits.projectName && !/[.!?]$/.test(title)) {
        flushProject();
        project = { name: title.trim(), description: (explicit?.[2] ?? "").slice(0, limits.projectDescription) };
      } else if (project && bullet.test(raw)) {
        project.description = [project.description, line].filter(Boolean).join("\n").slice(0, limits.projectDescription);
      }
    }
  }
  flushProject();
  if (unlabeledName) add({ field: "name", value: unlabeledName, evidence: "Name-like line at the top" });
  // Avoid combining fields from different entries when multiple qualifications are listed.
  const degreeLines = education.map((line) => degree.exec(line)).filter((match) => match !== null);
  const colleges = uniqueProfileTags(education.flatMap((line) => {
    const label = /^(?:college|institution|university)\s*:\s*(.+)$/i.exec(line)?.[1];
    const value = label ?? (institution.test(line) && !degree.test(line) && !/\d{4}/.test(line) ? line.split(/\s+\|\s+/)[0] : "");
    return value.length <= limits.college ? [value] : [];
  }));
  if (degreeLines.length <= 1 && colleges.length <= 1) {
    if (colleges[0]) add({ field: "college", value: colleges[0], evidence: "Education section" });
    if (degreeLines[0]) {
      const [, qualification, rest] = degreeLines[0];
      add({ field: "degree", value: qualification, evidence: "Education section" });
      const branch = /^(?:in\s+|\(|[–—:-]\s*)([^|,()\d]+)\)?(?=$|[|,]|\s+\d{4})/i.exec(rest)?.[1]?.trim();
      if (branch && branch.length <= limits.branch && !institution.test(branch)) {
        add({ field: "branch", value: branch, evidence: "Degree line in education" });
      }
    }
    const branches = uniqueProfileTags(education.flatMap((line) => /^(?:branch|major)\s*:\s*(.+)$/i.exec(line)?.[1] ?? []));
    if (branches.length === 1 && branches[0].length <= limits.branch) {
      add({ field: "branch", value: branches[0], evidence: "Explicit branch or major label" });
    }
    const years = new Set(education.flatMap((line) => Array.from(line.matchAll(/\b(?:expected(?: graduation)?|graduat(?:ion|ed|ing)(?:\s+year)?|class of|batch)\s*(?:in\s+|:\s*)?(?:(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+)?(20\d{2}|2100)\b/gi), (match) => Number(match[1]))));
    if (years.size === 1) add({ field: "batch", value: [...years][0], evidence: "Explicit graduation marker in education" });
  }
  uniqueProfileTags(skills).slice(0, limits.skills).forEach((value) => found.push({ field: "skill", value, evidence: "Skills section" }));
  return found;
}

export function mergeResumeCandidates(profile: ProfileFields, selected: ResumeCandidate[]): ProfileFields {
  const updated = { ...profile, skills: [...profile.skills], projects: [...profile.projects] };
  for (const candidate of selected) {
    if (candidate.field === "skill") {
      if (updated.skills.length < limits.skills) updated.skills = uniqueProfileTags([...updated.skills, candidate.value]);
    } else if (candidate.field === "project") {
      if (updated.projects.length < limits.projects && !updated.projects.some((project) =>
        profileIdentity(project.name) === profileIdentity(candidate.value.name))) {
        updated.projects.push({ id: crypto.randomUUID(), ...candidate.value });
      }
    } else if (candidate.field === "batch") {
      updated.batch = candidate.value;
    } else {
      updated[candidate.field] = candidate.value;
    }
  }
  return updated;
}
