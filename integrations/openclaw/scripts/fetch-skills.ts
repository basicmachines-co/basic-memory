/**
 * Copy all memory-* skills from the top-level basic-memory skills source.
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

function discoverSkillDirs(): string[] {
  if (!existsSync(SOURCE_SKILLS_DIR)) {
    throw new Error(`Missing source skills directory: ${SOURCE_SKILLS_DIR}`)
  }

  const skillDirs = readdirSync(SOURCE_SKILLS_DIR, { withFileTypes: true })
    .filter((e) => e.isDirectory() && e.name.startsWith("memory-"))
    .map((e) => e.name)
    .sort()

  if (skillDirs.length === 0) {
    throw new Error(`No memory-* directories found in ${SOURCE_SKILLS_DIR}`)
  }

  return skillDirs
}

function main() {
  console.log(`Copying skills from ${SOURCE_SKILLS_DIR}`)

  const skillDirs = discoverSkillDirs()
  console.log(`Found ${skillDirs.length} skills: ${skillDirs.join(", ")}`)

  const manifest: SkillManifestEntry[] = []

  for (const dir of skillDirs) {
    const skillPath = resolve(SOURCE_SKILLS_DIR, dir, "SKILL.md")
    if (!existsSync(skillPath)) {
      throw new Error(`Missing SKILL.md for ${dir}: ${skillPath}`)
    }

    const content = readFileSync(skillPath, "utf8")
    const meta = parseFrontmatter(content)

    const outDir = resolve(SKILLS_DIR, dir)
    mkdirSync(outDir, { recursive: true })
    writeFileSync(resolve(outDir, "SKILL.md"), content)

    // Copy bundled resources (references/, evals/, assets/, ...) so
    // multi-file skills stay self-contained in the generated package.
    for (const entry of readdirSync(resolve(SOURCE_SKILLS_DIR, dir), {
      withFileTypes: true,
    })) {
      if (entry.name === "SKILL.md") continue
      cpSync(
        resolve(SOURCE_SKILLS_DIR, dir, entry.name),
        resolve(outDir, entry.name),
        { recursive: true },
      )
    }

    manifest.push({ dir, name: meta.name, description: meta.description })
    console.log(`  ✓ ${dir}`)
  }

  manifest.sort((a, b) => a.dir.localeCompare(b.dir))

  mkdirSync(SKILLS_DIR, { recursive: true })
  writeFileSync(
    resolve(SKILLS_DIR, "manifest.json"),
    `${JSON.stringify(manifest, null, 2)}\n`,
  )

  console.log(
    `\nWrote ${manifest.length} skills + manifest.json to ${SKILLS_DIR}`,
  )
}

try {
  main()
} catch (err) {
  const message = err instanceof Error ? err.message : String(err)
  console.error("Fatal:", message)
  process.exit(1)
}
