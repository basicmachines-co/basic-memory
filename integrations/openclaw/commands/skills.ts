import { existsSync, readFileSync } from "node:fs"
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

export function registerSkillCommands(api: OpenClawPluginApi): void {
  const skillsDir = resolveSkillsDir(api)
  const manifest = loadManifest(skillsDir)

  for (const entry of manifest) {
    const commandName = entry.dir.replace(/^memory-/, "")
    const content = loadSkill(skillsDir, entry.dir)

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
