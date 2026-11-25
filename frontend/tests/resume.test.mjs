import assert from "node:assert/strict";
import { after, test } from "node:test";
import { createServer } from "vite";

const vite = await createServer({ configFile: false, server: { middlewareMode: true, ws: false } });
after(() => vite.close());
const { extractResumeCandidates, mergeResumeCandidates, RESUME_LIMITS } = await vite.ssrLoadModule("/src/resume.ts");
const { normalizeProfile } = await vite.ssrLoadModule("/src/profile.ts");

const sample = `
Taylor Example
Education
Example University
B.Tech in Computer Science
Expected graduation: May 2027
Technical Skills: Python, SQL, python
Projects:
Project: Campus Finder — Built a catalogue.
• Implemented search.
Experience
Seeking a Backend Developer role.
`;

test("proposes explicitly supported facts with evidence, not inferred roles", () => {
  const candidates = extractResumeCandidates(sample);
  assert.deepEqual(candidates.map(({ field, value }) => [field, value]), [
    ["project", { name: "Campus Finder", description: "Built a catalogue.\nImplemented search." }],
    ["name", "Taylor Example"], ["college", "Example University"], ["degree", "B.Tech"],
    ["branch", "Computer Science"], ["batch", 2027], ["skill", "Python"], ["skill", "SQL"],
  ]);
  assert(candidates.every(({ evidence }) => evidence.length > 0));
});

test("skips ambiguous education, job titles, and unmarked graduation dates", () => {
  assert.deepEqual(extractResumeCandidates("Technical Skills\nEducation\nFirst University\nB.Tech in Computing\nSecond University\nM.Tech in Robotics\nGraduation: 2025\nGraduation: 2027"), []);
  assert.deepEqual(extractResumeCandidates("Backend Developer\nEducation\nExample University\n2020–2024\nExperience\nBuilt a Python app").map(({ field }) => field), ["college"]);
});

test("refuses oversized text and leaves unsupported content blank", () => {
  assert.throws(() => extractResumeCandidates("a".repeat(RESUME_LIMITS.chars + 1)), /too long/);
  assert.deepEqual(extractResumeCandidates("~%%%@"), []);
});

test("merges selected changes without mutating or replacing existing tags or projects", () => {
  const { onboarding_state: _, ...fields } = normalizeProfile({ name: "Existing User", skills: ["SQL"], roles: ["Developer"],
    projects: [{ id: "original", name: "Campus Finder", description: "Existing description" }] });
  const candidates = extractResumeCandidates(sample);
  const selected = candidates.filter(({ field }) => field === "project" || field === "skill");
  const merged = mergeResumeCandidates(fields, selected);
  assert.deepEqual(merged, { ...fields, skills: ["SQL", "Python"] });
  assert.deepEqual(fields.skills, ["SQL"]);
  const extended = mergeResumeCandidates(fields, [{ field: "project", value: { name: "A new project", description: "" }, evidence: "Projects section" }]);
  assert.match(extended.projects[1].id, /^[a-f0-9-]{36}$/);
  assert.equal(extended.projects[0].id, "original");
  assert.equal(fields.projects.length, 1);
});
