import { afterEach, beforeEach, describe, expect, spyOn, test } from "bun:test"
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  rmSync,
  writeFileSync,
} from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { fetchSkills } from "./fetch-skills.ts"

function writeSkill(root: string, dir: string, frontmatter: string) {
  mkdirSync(join(root, dir), { recursive: true })
  writeFileSync(
    join(root, dir, "SKILL.md"),
    `---\n${frontmatter}\n---\n\n# ${dir}\n`,
  )
}

describe("fetchSkills", () => {
  let root: string
  let source: string
  let out: string

  beforeEach(() => {
    root = mkdtempSync(join(tmpdir(), "fetch-skills-"))
    source = join(root, "source")
    out = join(root, "out")
    spyOn(console, "log").mockImplementation(() => {})
    writeSkill(
      source,
      "memory-notes",
      'name: memory-notes\ndescription: "Write notes."',
    )
    writeSkill(
      source,
      "memory-ci-capture",
      'name: memory-ci-capture\ndescription: "CI only."\nmetadata:\n  internal: true',
    )
  })

  afterEach(() => {
    rmSync(root, { recursive: true, force: true })
  })

  test("removes generated skills that are no longer distributed", () => {
    // A copy left by an earlier run, before the skill was marked internal.
    writeSkill(
      out,
      "memory-ci-capture",
      'name: memory-ci-capture\ndescription: "CI only."',
    )
    writeSkill(
      out,
      "memory-removed",
      'name: memory-removed\ndescription: "Gone."',
    )

    const manifest = fetchSkills(source, out)

    expect(manifest.map((entry) => entry.dir)).toEqual(["memory-notes"])
    expect(existsSync(join(out, "memory-notes", "SKILL.md"))).toBe(true)
    expect(existsSync(join(out, "memory-ci-capture"))).toBe(false)
    expect(existsSync(join(out, "memory-removed"))).toBe(false)
  })

  test("leaves existing output alone when the source directory is missing", () => {
    writeSkill(
      out,
      "memory-notes",
      'name: memory-notes\ndescription: "Write notes."',
    )

    expect(() => fetchSkills(join(root, "missing"), out)).toThrow(
      "Missing source skills directory",
    )
    expect(existsSync(join(out, "memory-notes", "SKILL.md"))).toBe(true)
  })
  test.each([
    [
      "is missing SKILL.md",
      "memory-empty",
      null,
      "Missing SKILL.md for memory-empty",
    ],
    [
      "has bad frontmatter",
      "memory-broken",
      "name: memory-broken",
      "memory-broken: Frontmatter missing 'description'",
    ],
  ])("leaves existing output alone when a source skill %s", (_case, dir, frontmatter, error) => {
    writeSkill(
      out,
      "memory-notes",
      'name: memory-notes\ndescription: "Write notes."',
    )
    if (frontmatter === null) mkdirSync(join(source, dir))
    else writeSkill(source, dir, frontmatter)

    expect(() => fetchSkills(source, out)).toThrow(error)
    expect(existsSync(join(out, "memory-notes", "SKILL.md"))).toBe(true)
  })
})
