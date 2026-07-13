import { existsSync, readdirSync, readFileSync } from "node:fs"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry"

const __dirname = dirname(fileURLToPath(import.meta.url))

interface ManifestEntry {
  dir: string
  name: string
  description: string
}

function resolveSkillsDir(api: OpenClawPluginApi): string {
  if (api.resolvePath) {
    return api.resolvePath("skills")
  }

  const sourceRootSkills = resolve(__dirname, "..", "skills")
  if (existsSync(sourceRootSkills)) {
    return sourceRootSkills
  }

  return resolve(__dirname, "..", "..", "skills")
}

function loadManifest(skillsDir: string): ManifestEntry[] {
  try {
    const raw = readFileSync(resolve(skillsDir, "manifest.json"), "utf-8")
    return JSON.parse(raw) as ManifestEntry[]
  } catch {
    throw new Error(
      "skills/manifest.json not found. Run `bun scripts/fetch-skills.ts` first.",
    )
  }
}

function loadSkill(skillsDir: string, dir: string): string {
  return readFileSync(resolve(skillsDir, dir, "SKILL.md"), "utf-8")
}

/**
 * List a skill's bundled resource files (references/, assets/, ...) so the
 * command can tell the assistant where they live on disk. SKILL.md workflows
 * reference these by relative path; without the mapping, bundled commands
 * would direct the assistant to files it was never given. evals/ is excluded
 * as test fixtures.
 */
function listResourceFiles(skillsDir: string, dir: string): string[] {
  const root = resolve(skillsDir, dir)
  const files: string[] = []
  const walk = (rel: string) => {
    for (const entry of readdirSync(resolve(root, rel), {
      withFileTypes: true,
    })) {
      const relPath = rel ? `${rel}/${entry.name}` : entry.name
      if (entry.isDirectory()) {
        if (relPath !== "evals") walk(relPath)
      } else if (relPath !== "SKILL.md") {
        files.push(relPath)
      }
    }
  }
  walk("")
  return files.sort()
}

function resourceSection(
  skillsDir: string,
  dir: string,
  files: string[],
): string {
  if (files.length === 0) return ""
  const lines = files
    .map((f) => `- \`${f}\` → ${resolve(skillsDir, dir, f)}`)
    .join("\n")
  return (
    "\n\n## Bundled resource files\n\n" +
    "This skill references companion files by relative path. They are installed at:\n\n" +
    `${lines}\n\n` +
    "When the workflow directs you to read one of these files, read it from the absolute path listed above."
  )
}

export function registerSkillCommands(api: OpenClawPluginApi): void {
  const skillsDir = resolveSkillsDir(api)
  const manifest = loadManifest(skillsDir)

  for (const entry of manifest) {
    const commandName = entry.dir.replace(/^memory-/, "")
    const content =
      loadSkill(skillsDir, entry.dir) +
      resourceSection(
        skillsDir,
        entry.dir,
        listResourceFiles(skillsDir, entry.dir),
      )

    api.registerCommand({
      name: commandName,
      description: entry.description,
      acceptsArgs: true,
      requireAuth: true,
      handler: async (ctx: { args?: string }) => {
        const args = ctx.args?.trim()
        const prefix = args
          ? `User request: ${args}\n\nFollow this workflow:\n\n`
          : "Follow this workflow:\n\n"
        return { text: prefix + content }
      },
    })
  }
}
