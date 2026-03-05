# SPEC-LOCAL-GRAPH-INTELLIGENCE: Premium Local Graph Intelligence

**Status:** Draft  
**Date:** 2026-03-05  
**Owner:** Basic Memory
**Current Phase (2026-03-05):** Phase 1 contract foundation shipped; engineering is now executing SQL-backed Phase 2 graph logic.

Companion technical addendum:
`/docs/specs/SPEC-LOCAL-GRAPH-INTELLIGENCE-TECHNICAL-ADDENDUM.md`

## Summary

Add a premium local feature that turns Basic Memory from "search and recall" into "explain and guide."

The value is not a new database. The value is better decisions for local users:

1. Understand why something matters.
2. See what will be affected before making a change.
3. Detect weak spots in the knowledge base early.
4. Navigate complex knowledge intentionally instead of loading everything.

This feature is additive. Existing local workflows remain intact.

## Positioning

Core message:
"Your notes do more than store knowledge. They reveal consequences, lineage, and blind spots."

Local user promise:

1. Keep files local.
2. Keep markdown as source of truth.
3. Get advanced graph intelligence as an opt-in premium capability.

## Problem

Today, deep graph navigation is possible but often expensive in context size and hard to steer for complex questions.
Users can find information, but they still do manual synthesis to answer:

1. What changed because of this note?
2. Why did we decide this?
3. What might break if I update this?
4. Which parts of the graph are stale, isolated, or contradictory?

The cost is time, cognitive load, and missed risk.

## Goals

1. Provide clear, explainable graph insights that users can act on.
2. Make deep navigation feel guided, not overwhelming.
3. Help users prevent mistakes before they happen.
4. Create premium local value that is easy to understand and justify.
5. Keep feature behavior transparent and trustworthy.

## Non-Goals

1. Replacing SQLite as the primary operational store.
2. Changing markdown as source of truth.
3. Forcing users to learn graph query languages.
4. Building a cloud-only feature set.
5. Turning Basic Memory into an enterprise BI product.

## Product Frame: From Retrieval to Reasoning

The feature should be framed as a shift in user outcome:

1. Retrieval: "Find me the note."
2. Reasoning: "Show me the path, impact, and confidence around this note."

This is the main narrative upgrade for premium local users.

## Premium Value Pillars

### 1) Decision Confidence

Users can see decision lineage:

1. What evidence supported a decision.
2. Which notes/specs informed it.
3. How that decision evolved over time.

### 2) Change Safety

Users can run impact-aware workflows:

1. Estimate blast radius before editing.
2. Surface downstream dependencies.
3. Prioritize what to review first.

### 3) Knowledge Quality

Users can maintain graph health:

1. Detect orphaned notes.
2. Detect overloaded hub notes.
3. Detect stale but high-centrality notes.
4. Detect likely contradictions.

### 4) Guided Navigation

Users can explore deeper relationships without context explosion:

1. Follow promising branches.
2. Stop when confidence is sufficient.
3. Avoid "load everything and hope."

## Feature Catalog (Value-First)

### A. Decision Lineage

What users get:

1. A clear "why chain" for important conclusions.
2. Traceable connections to supporting notes.
3. Better handoffs and historical understanding.

### B. Impact Radius

What users get:

1. A ranked list of likely affected notes before edits.
2. Safer refactors for docs, plans, and architecture.
3. Reduced accidental drift and inconsistency.

### C. Knowledge Health Dashboard

What users get:

1. Weekly health signals for the graph.
2. Actionable cleanup targets.
3. Better long-term memory quality with less manual auditing.

### D. Path Explorer

What users get:

1. "Show me how A connects to B" style explanations.
2. Multiple candidate paths with confidence cues.
3. Better discovery across large note collections.

### E. Contradiction Watch

What users get:

1. Early warnings for conflicting statements.
2. Suggested reconciliation workflow.
3. Higher trust in the knowledge base.

### F. Priority Briefs

What users get:

1. Periodic "what matters now" graph summaries.
2. Focused recommendations, not noisy activity dumps.
3. Better focus for solo builders and small teams.

## User Personas and Why They Pay

### Solo Technical Founder

Pain:
Cannot hold full architecture and decision history in working memory.

Premium value:
Impact Radius + Decision Lineage prevent rework and regressions.

### Product/Research Lead

Pain:
Knowledge is fragmented across specs, notes, and decisions.

Premium value:
Path Explorer + Priority Briefs compress synthesis time.

### Consultant/Fractional Operator

Pain:
Frequent context switching across domains and clients.

Premium value:
Knowledge Health + Decision Lineage speed onboarding and reporting.

## Packaging Direction

Suggested packaging:

1. OSS Local: existing search + context tools.
2. Local+ Graph Intelligence: advanced graph insight features listed above.
3. Future Team Add-On: shared policies, shared graph health views, shared lineage views.

Core upsell line:
"Keep your local workflow. Add graph intelligence when complexity grows."

## Experience Principles

1. Explainability first.
Every advanced result should show "why this was suggested."

2. Actionability over novelty.
Insights should lead to concrete next steps, not abstract charts.

3. Progressive disclosure.
Start with concise summaries, expand on demand.

4. Deterministic where possible.
Users should trust repeated runs of the same workflow.

5. Respect local-first expectations.
No surprise cloud dependency in premium local mode.

## Success Criteria (Product)

1. Users can describe the benefit in one sentence:
"It shows me what matters and what breaks before I change things."
2. Premium users report lower time-to-understanding for complex topics.
3. Premium users report fewer "surprise side effects" after edits.
4. Premium users keep larger knowledge graphs healthy with less manual effort.
5. Feature adoption is driven by outcomes, not by curiosity-only usage.

## Risks and Mitigations

Risk: Feature sounds like "just better search."  
Mitigation: Lead messaging with decision confidence and change safety, not traversal depth.

Risk: Feature feels too advanced for normal users.  
Mitigation: Package as guided insights and reports, not as a query language.

Risk: Insight quality feels noisy.  
Mitigation: Focus launch scope on high-precision insight types and transparent rationale.

Risk: Value is hard to prove.  
Mitigation: Track user-facing outcomes (time saved, risk avoided, cleanup completed).

## Rollout Narrative

Phase 1: "Safer Changes"

1. Impact Radius
2. Decision Lineage

Phase 2: "Health and Clarity"

1. Knowledge Health Dashboard
2. Contradiction Watch

Phase 3: "Strategic Navigation"

1. Path Explorer
2. Priority Briefs

## One-Line Positioning Options

1. "Local notes, strategic intelligence."
2. "Know what changed, why it matters, and what it affects."
3. "From note-taking to decision support."

## Open Questions

1. Which two features best define the paid tier at launch?
2. Which insight types should be guaranteed deterministic in v1?
3. Should Priority Briefs be bundled or separate as an add-on?
4. What is the simplest in-product education flow for first-time premium users?
