import {
  cpSync,
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const PACKAGE_ROOT = resolve(__dirname, "..");
const SKILL_REFERENCES_DIR = resolve(PACKAGE_ROOT, "skill-references");
const SOURCE_SKILLS_DIR = resolve(PACKAGE_ROOT, "..", "..", "skills");
const INCLUDED_SKILLS = ["memory-notes", "memory-capture", "memory-continue", "memory-tasks"];
const REFERENCE_PREAMBLE = `> Pi package reference only.
> These canonical Basic Memory instructions may mention direct MCP tools such as
> \`read_note\`, \`write_note\`, \`search_notes\`, or \`build_context\`.
> In Pi CLI mode, use the active \`basic-memory-pi\` skill with \`bm_recall\` and
> \`bm_capture\` instead. In Pi MCP mode, only use direct Basic Memory tool names
> if the MCP adapter exposes them in the current runtime.

`;

interface SkillManifestEntry {
  dir: string;
  name: string;
  description: string;
}

function parseFrontmatter(md: string): { name: string; description: string } {
  const match = md.match(/^---\r?\n([\s\S]*?)\r?\n---/);
  if (!match) throw new Error("SKILL.md missing YAML frontmatter");

  const yaml = match[1] ?? "";
  const name = yaml.match(/^name:\s*(.+)$/m)?.[1]?.trim().replace(/^["']|["']$/g, "");
  const description = yaml
    .match(/^description:\s*(.+)$/m)?.[1]
    ?.trim()
    .replace(/^["']|["']$/g, "");

  if (!name) throw new Error("Frontmatter missing 'name'");
  if (!description) throw new Error("Frontmatter missing 'description'");
  return { name, description };
}

function copySkill(dir: string): SkillManifestEntry {
  const sourceDir = resolve(SOURCE_SKILLS_DIR, dir);
  const skillPath = resolve(sourceDir, "SKILL.md");
  if (!existsSync(skillPath)) throw new Error(`Missing SKILL.md for ${dir}: ${skillPath}`);

  const content = readFileSync(skillPath, "utf8");
  const meta = parseFrontmatter(content);
  const outDir = resolve(SKILL_REFERENCES_DIR, dir);

  rmSync(outDir, { recursive: true, force: true });
  mkdirSync(outDir, { recursive: true });
  writeFileSync(resolve(outDir, "REFERENCE.md"), `${REFERENCE_PREAMBLE}${content}`);

  for (const entry of readdirSync(sourceDir, { withFileTypes: true })) {
    if (entry.name === "SKILL.md") continue;
    cpSync(resolve(sourceDir, entry.name), resolve(outDir, entry.name), { recursive: true });
  }

  return { dir, name: meta.name, description: meta.description };
}

function main(): void {
  if (!existsSync(SOURCE_SKILLS_DIR)) {
    throw new Error(`Missing source skills directory: ${SOURCE_SKILLS_DIR}`);
  }

  mkdirSync(SKILL_REFERENCES_DIR, { recursive: true });
  const manifest = INCLUDED_SKILLS.map(copySkill);
  writeFileSync(
    resolve(SKILL_REFERENCES_DIR, "manifest.json"),
    `${JSON.stringify(manifest, null, 2)}\n`,
  );
  console.log(`Copied ${manifest.length} Basic Memory skill references to ${SKILL_REFERENCES_DIR}`);
}

try {
  main();
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
}
