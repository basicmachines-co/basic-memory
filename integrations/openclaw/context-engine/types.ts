import type { OpenClawPluginApi } from "openclaw/plugin-sdk/core"

// OpenClaw 2026.10 dropped the bare "openclaw/plugin-sdk" entry point that used
// to export ContextEngine and its result types. Derive them from the public
// registerContextEngine signature so we depend only on plugin-sdk/core.
type ContextEngineFactory = Parameters<
  OpenClawPluginApi["registerContextEngine"]
>[1]

export type ContextEngine = Awaited<ReturnType<ContextEngineFactory>>
export type AssembleResult = Awaited<ReturnType<ContextEngine["assemble"]>>
export type BootstrapResult = Awaited<
  ReturnType<NonNullable<ContextEngine["bootstrap"]>>
>
export type CompactResult = Awaited<ReturnType<ContextEngine["compact"]>>
export type SubagentSpawnPreparation = NonNullable<
  Awaited<ReturnType<NonNullable<ContextEngine["prepareSubagentSpawn"]>>>
>
export type CommitTurnResult = Awaited<
  ReturnType<NonNullable<ContextEngine["commitTurn"]>>
>
