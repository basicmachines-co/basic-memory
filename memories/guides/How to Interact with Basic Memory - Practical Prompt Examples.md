---
title: How to Interact with Basic Memory - Practical Prompt Examples
type: note
permalink: guides/how-to-interact-with-basic-memory-practical-prompt-examples
tags:
- basic-memory,prompts,workflow,examples
---

A practical guide to effectively interacting with Basic Memory through prompts, with real examples.

## The Observation Pattern (Automatic Semantic Extraction)

Basic Memory automatically extracts semantic meaning from this pattern:

```markdown
- [category] Your observation content here
```

You don't need to tag or categorize manually - just write naturally using this format and Basic Memory indexes it semantically.

## Effective Prompting Patterns

### Pattern 1: Learning Something New

**You (to Claude):**
> "I just learned about React Server Components. Can you help me document this in Basic Memory?"

**Claude's Response:**
I'll create a note with semantic observations about React Server Components:

```markdown
## Observations

- [concept] React Server Components run only on the server
- [benefit] Reduces bundle size by keeping component code server-side
- [benefit] Direct access to backend resources without API layer
- [use-case] Fetching data from databases without client-side API calls
- [limitation] Cannot use browser-only APIs like useState or useEffect
- [pattern] Combine Server Components with Client Components for interactivity

## Relations

- related_to [[React]]
- related_to [[Server-Side Rendering]]
- part_of [[Frontend Architecture]]
```

**What Basic Memory Does Automatically:**
- Extracts 6 observations across different categories
- Creates searchable semantic index
- Links concepts together
- Makes this knowledge available to future AI conversations

### Pattern 2: Recording a Debugging Session

**You (to Claude):**
> "I just spent 2 hours debugging a memory leak. Document what I learned."

**Better Approach:**
```markdown
## Observations

- [problem] Memory leak in React useEffect without cleanup
- [symptom] Browser tab memory growing to 2GB over time
- [root-cause] Event listeners not being removed on unmount
- [solution] Added cleanup function to useEffect return
- [pattern] Always return cleanup function when adding event listeners
- [learning] Chrome DevTools Memory profiler shows retained detached DOM nodes
- [tool] useEffect cleanup prevents memory leaks on component unmount

## Relations

- related_to [[React Hooks]]
- related_to [[Memory Management]]
- solved_by [[useEffect Cleanup Pattern]]
```

**Why This Works:**
Each `[category]` becomes searchable. When you ask "What do I know about React memory issues?" Basic Memory finds these observations.

### Pattern 3: Continuing Previous Conversations

**Prompt Examples That Leverage Basic Memory:**

```
"Continue our discussion about authentication from last week"

"What did I learn about Docker deployment yesterday?"

"Show me my notes about the API refactoring project"

"Based on my previous notes about Next.js, help me set up middleware"
```

**How Claude Uses This:**
When you use the `continue_conversation` prompt or reference previous topics, Claude can:
1. Search your Basic Memory for relevant notes
2. Load context from observations matching the topic
3. Continue the conversation with full historical context

### Pattern 4: Project-Specific Context

**Setup (one-time):**
Create a project note in `projects/my-app.md`:

```markdown
## Observations

- [stack] Next.js 14 with App Router
- [stack] PostgreSQL with Prisma ORM
- [stack] TailwindCSS for styling
- [pattern] Using Server Actions for mutations
- [pattern] RSC for data fetching
- [convention] API routes in app/api/
- [convention] Components in components/ with feature-based folders
- [environment] Development on Railway
- [environment] Production on Vercel

## Relations

- uses [[Next.js]]
- uses [[PostgreSQL]]
- uses [[Prisma]]
```

**Usage:**
Now when you ask: "Help me add authentication to my app"

Claude can:
1. Read your project context from Basic Memory
2. See you're using Next.js 14 with Server Actions
3. Recommend auth solutions compatible with your stack
4. Suggest implementation following your existing patterns

### Pattern 5: Decision Documentation

**Prompt:**
> "We decided to use Zustand instead of Redux. Document this decision."

**Response:**
```markdown
## Observations

- [decision] Chose Zustand over Redux for state management
- [reason] Simpler API with less boilerplate
- [reason] Better TypeScript support out of the box
- [reason] Smaller bundle size (3kb vs 45kb for Redux Toolkit)
- [tradeoff] Less ecosystem and middleware options
- [context] Team is small and doesn't need Redux DevTools complexity
- [date] Decision made on 2026-01-12

## Relations

- alternative_to [[Redux]]
- chosen_for [[Project Alpha]]
- related_to [[State Management]]
```

**Value:**
When someone asks "Why aren't we using Redux?" 6 months later, the context is preserved.

## Common Observation Categories

Here are categories people commonly use (but remember - YOU decide the categories, Basic Memory just extracts them):

**Technical:**
- `[concept]` - Core ideas and definitions
- `[feature]` - Product or library features
- `[pattern]` - Design patterns and best practices
- `[antipattern]` - Things to avoid
- `[tool]` - Tools and utilities

**Problem Solving:**
- `[problem]` - Issues encountered
- `[symptom]` - Observable symptoms
- `[root-cause]` - Underlying causes
- `[solution]` - How it was fixed
- `[workaround]` - Temporary fixes

**Decision Making:**
- `[decision]` - Decisions made
- `[reason]` - Justifications
- `[tradeoff]` - Pros and cons
- `[alternative]` - Options not chosen

**Learning:**
- `[learning]` - Key insights
- `[gotcha]` - Surprising behavior
- `[tip]` - Helpful hints
- `[limitation]` - Constraints or limits

**Project Context:**
- `[requirement]` - Project requirements
- `[constraint]` - Limitations or boundaries
- `[assumption]` - Assumptions made
- `[stack]` - Technology choices

## Anti-Patterns to Avoid

### ❌ Don't: Write unstructured notes
```markdown
I learned about React hooks today. They're pretty cool. You can use 
useState to manage state and useEffect for side effects.
```

### ✅ Do: Use observation format
```markdown
## Observations

- [concept] React Hooks are functions that let you use state in function components
- [hook] useState manages component state
- [hook] useEffect handles side effects
- [benefit] Cleaner than class components
- [pattern] Call hooks at top level, not in conditions or loops
```

### ❌ Don't: Over-tag with manual tags
```yaml
tags: ["react", "hooks", "useState", "useEffect", "javascript", "frontend", "web", "programming", "learning"]
```

### ✅ Do: Use minimal, high-level tags
```yaml
tags: ["react", "learning-notes"]
```

Let observations handle the semantic categorization!

## Prompting Claude to Help You

**Great Prompts:**

1. **"Create a Basic Memory note about [topic] using observation patterns"**
   - Claude will structure it properly

2. **"Search my Basic Memory for anything about [topic]"**
   - Claude uses search_notes to find relevant content

3. **"What have I learned about [topic] in the last week?"**
   - Claude uses recent_activity with timeframe

4. **"Based on my notes, what's my current approach to [problem]?"**
   - Claude synthesizes your documented patterns

5. **"Continue working on [project] - catch yourself up from my notes"**
   - Claude uses build_context to load project knowledge

## The Mental Model

Think of Basic Memory as a **semantic second brain**:

1. **You write naturally** using `[category]` observations
2. **Basic Memory indexes** all those observations
3. **Claude can search and reference** your accumulated knowledge
4. **Context persists** across conversations and time

You're not tagging for organization - you're creating **searchable semantic meaning**.

## Example Workflow

**Day 1:** Document learning about GraphQL
```markdown
- [concept] GraphQL is a query language for APIs
- [benefit] Client specifies exactly what data it needs
- [comparison] More flexible than REST for complex data requirements
```

**Day 5:** Ask Claude to help with API design
> "Based on what I know about GraphQL, help me design this API"

Claude reads your GraphQL observations and provides informed suggestions.

**Day 30:** Review what you've learned
> "Show me everything I learned about APIs this month"

Claude uses `recent_activity(timeframe="30d")` and filters for API-related observations.

## Relations: The Knowledge Graph Layer

Relations connect your notes:

```markdown
## Relations

- related_to [[GraphQL]]
- alternative_to [[REST API]]
- implements [[Apollo Server]]
- solves [[Over-fetching Problem]]
```

This creates a web of interconnected knowledge that Claude can traverse.

## The Power of Consistency

The more consistently you use observation patterns, the smarter your interactions become:

- Claude learns YOUR terminology
- Claude understands YOUR patterns
- Claude can reference YOUR previous solutions
- Claude builds on YOUR knowledge base

You're not just saving notes - you're **training your personal AI context**.