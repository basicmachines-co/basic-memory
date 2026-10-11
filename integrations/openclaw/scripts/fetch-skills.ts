/**
 * Copy all memory-* skills from the top-level basic-memory skills source,
 * skipping internal ones.
 *
 * Auto-discovers skill directories in ../../../skills, copies each skill's
 * SKILL.md plus any bundled resources (references/, evals/, assets/, ...)
 * into this package's skills/<dir>/, and generates skills/manifest.json.
 */

import {
  cpSync,
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"

const __dirname = dirname(fileURLToPath(import.meta.url))
const SKILLS_DIR = resolve(__dirname, "..", "skills")
const SOURCE_SKILLS_DIR = resolve(__dirname, "..", "..", "..", "skills")

interface SkillManifestEntry {
  dir: string
  name: string
  description: string
}

function parseFrontmatter(md: string): { name: string; description: string } {
  const match = md.match(/^---\r?\n([\s\S]*?)\r?\n---/)
  if (!match) throw new Error("SKILL.md missing YAML frontmatter")

  const yaml = match[1]
  const name = yaml
    .match(/^name:\s*(.+)$/m)?.[1]
    ?.trim()
    .replace(/^["']|["']$/g, "")
  const description = yaml
    .match(/^description:\s*(.+)$/m)?.[1]
    ?.trim()
    .replace(/^["']|["']$/g, "")

  if (!name) throw new Error("Frontmatter missing 'name'")
  if (!description) throw new Error("Frontmatter missing 'description'")

  return { name, description }
}

function discoverSkillDirs(sourceDir: string): string[] {
  if (!existsSync(sourceDir)) {
    throw new Error(`Missing source skills directory: ${sourceDir}`)
  }

  const skillDirs = readdirSync(sourceDir, { withFileTypes: true })
    .filter((e) => e.isDirectory() && e.name.startsWith("memory-"))
    .map((e) => e.name)
    .sort()

  if (skillDirs.length === 0) {
    throw new Error(`No memory-* directories found in ${sourceDir}`)
  }

  return skillDirs.filter((dir) => {
    const skillPath = resolve(sourceDir, dir, "SKILL.md")
    return !(
      existsSync(skillPath) && isInternal(readFileSync(skillPath, "utf8"))
    )
  })
}

/**
 * Internal skills (frontmatter `metadata: { internal: true }`, the `skills`
 * CLI's marker) are CI tooling, not something a user should run from OpenClaw.
 */
function isInternal(md: string): boolean {
  const yaml = md.match(/^---\r?\n([\s\S]*?)\r?\n---/)?.[1] ?? ""
  // Walk lines rather than one regex: the nested-block regex backtracks badly.
  // Mirrors is_internal_skill in scripts/validate_skills.py.
  let inMetadata = false
  let childIndent: number | null = null
  for (const line of yaml.split(/\r?\n/)) {
    const stripped = line.trim()
    // YAML keeps a mapping open across blank and comment lines.
    if (stripped === "" || stripped.startsWith("#")) continue
    if (!/^[ \t]/.test(line)) {
      inMetadata = line.split(":", 1)[0].trim() === "metadata"
      childIndent = null
      continue
    }
    if (!inMetadata) continue
    // Only a direct child counts: metadata.sub.internal is not the marker.
    const indent = line.length - line.trimStart().length
    childIndent ??= indent
    if (indent === childIndent && /^\s+internal:\s*true\s*(#.*)?$/.test(line)) {
      return true
    }
  }
  return false
}

export function fetchSkills(
  sourceDir: string,
  outDir: string,
): SkillManifestEntry[] {
  console.log(`Copying skills from ${sourceDir}`)

  const skillDirs = discoverSkillDirs(sourceDir)
  console.log(`Found ${skillDirs.length} skills: ${skillDirs.join(", ")}`)

  // Read and validate every skill before touching the output, so a missing or
  // malformed SKILL.md fails while the previous generated package is intact.
  const skills = skillDirs.map((dir) => {
    const skillPath = resolve(sourceDir, dir, "SKILL.md")
    if (!existsSync(skillPath)) {
      throw new Error(`Missing SKILL.md for ${dir}: ${skillPath}`)
    }
    const content = readFileSync(skillPath, "utf8")
    try {
      return { dir, content, meta: parseFrontmatter(content) }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      throw new Error(`${dir}: ${message}`)
    }
  })

  // The output directory is entirely generated (and gitignored), so rebuild it
  // from scratch. Clearing only the skills discovered this run would leave
  // behind a skill that was removed or newly marked internal, and npm pack
  // ships everything under skills/.
  rmSync(outDir, { recursive: true, force: true })

  const manifest: SkillManifestEntry[] = []

  for (const { dir, content, meta } of skills) {
    const skillOutDir = resolve(outDir, dir)
    mkdirSync(skillOutDir, { recursive: true })
    writeFileSync(resolve(skillOutDir, "SKILL.md"), content)

    // Copy bundled resources (references/, evals/, assets/, ...) so
    // multi-file skills stay self-contained in the generated package.
    for (const entry of readdirSync(resolve(sourceDir, dir), {
      withFileTypes: true,
    })) {
      if (entry.name === "SKILL.md") continue
      cpSync(
        resolve(sourceDir, dir, entry.name),
        resolve(skillOutDir, entry.name),
        { recursive: true },
      )
    }

    manifest.push({ dir, name: meta.name, description: meta.description })
    console.log(`  ✓ ${dir}`)
  }

  manifest.sort((a, b) => a.dir.localeCompare(b.dir))

  mkdirSync(outDir, { recursive: true })
  writeFileSync(
    resolve(outDir, "manifest.json"),
    `${JSON.stringify(manifest, null, 2)}\n`,
  )

  console.log(`\nWrote ${manifest.length} skills + manifest.json to ${outDir}`)
  return manifest
}

if (import.meta.main) {
  try {
    fetchSkills(SOURCE_SKILLS_DIR, SKILLS_DIR)
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    console.error("Fatal:", message)
    process.exit(1)
  }
}
