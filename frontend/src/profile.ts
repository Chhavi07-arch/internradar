export interface ProfileProject {
  id: string;
  name: string;
  description: string;
}

export interface ProfileFields {
  name: string;
  college: string;
  branch: string;
  degree: string;
  batch: number | null;
  roles: string[];
  skills: string[];
  projects: ProfileProject[];
}

export type StudentProfile = ProfileFields & { onboarding_state: "pending" | "skipped" | "completed" };

export const PROFILE_LIMITS = {
  name: 120, college: 180, branch: 100, degree: 120,
  roles: 25, skills: 50, tag: 100,
  projects: 20, projectName: 140, projectDescription: 1000,
  minBatch: 2000, maxBatch: 2100,
} as const;

export function normalizeProfile(profile: Partial<StudentProfile>): StudentProfile {
  return {
    name: profile.name ?? "", college: profile.college ?? "", branch: profile.branch ?? "",
    degree: profile.degree ?? "", batch: profile.batch ?? null, roles: profile.roles ?? [],
    skills: profile.skills ?? [], projects: profile.projects ?? [],
    onboarding_state: profile.onboarding_state ?? "pending",
  };
}

export const profileIdentity = (value: string) => value.normalize("NFKC").trim().toLowerCase();

export function uniqueProfileTags(tags: string[]): string[] {
  const seen = new Set<string>();
  return tags.map((tag) => tag.trim()).filter((tag) => {
    const key = profileIdentity(tag);
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}
