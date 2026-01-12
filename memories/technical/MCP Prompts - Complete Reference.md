# MCP Prompts - Complete Reference

A comprehensive guide to all MCP prompts available in Basic Memory and how they guide AI agents.

## What Are MCP Prompts?

## Observations

- [definition] MCP prompts are pre-formatted context templates that guide AI agent behavior
- [purpose] They provide structured workflows and best practices for using Basic Memory
- [mechanism] Prompts are invoked by name and return formatted context with instructions
- [location] Defined in src/basic_memory/mcp/prompts/
- [difference] Prompts return formatted context; tools perform actions

## Available MCP Prompts

### 1. AI Assistant Guide (Resource)

- [type] MCP Resource (not a prompt - always available context)
- [uri] memory://ai_assistant_guide
- [file] src/basic_memory/mcp/prompts/ai_assistant_guide.py
- [content] Loads src/basic_memory/mcp/resources/ai_assistant_guide.md
- [size] 412 lines of guidance
- [purpose] Core reference for how AI should use Basic Memory tools
- [availability] Available as a resource agents can reference

#### Key Sections in AI Assistant Guide

- [section] Overview - Local-first, real-time, semantic knowledge system
- [section] Importance of Knowledge Graph - "Connections > content"
- [section] Core Tools Reference - write_note, read_note, search_notes, build_context
- [section] memory:// URLs Explained - Reference format and patterns
- [section] Semantic Markdown Format - Observations and relations syntax
- [section] When to Record Context - Decision triggers
- [section] Understanding User Interactions - Common patterns
- [section] Key Things to Remember - Files are truth, build context effectively
- [section] Common Knowledge Patterns - Decisions, structure, discussions
- [section] Creating Effective Relations - Search-first workflow
- [section] Error Handling - Missing content, forward references, sync issues
- [section] Best Practices - Record context, semantic graph, structure, navigate, maintain

#### Deep Search Guidance (Lines 156-159)

```markdown
Human: "What were our decisions about auth?"

You: Let me find that information for you.
[Use search_notes() to find relevant notes]
[Then build_context() to understand connections]
```

- [instruction] Two-step pattern: search → build_context
- [workflow] Find → Explore → Synthesize
- [implication] Single search insufficient for complex questions

#### Navigate Knowledge Effectively (Lines 397-402)

```markdown
4. **Navigate Knowledge Effectively**
   - Start with specific searches
   - Follow relation paths
   - Combine information from multiple sources
   - Verify information is current
   - Build a complete picture before responding
```

- [principle] Start specific then expand
- [principle] Multiple sources required
- [principle] Don't respond until picture is complete

### 2. Continue Conversation Prompt

- [name] "Continue Conversation"
- [file] src/basic_memory/mcp/prompts/continue_conversation.py
- [description] Continue a previous conversation or work session
- [endpoint] POST /prompt/continue-conversation
- [purpose] Pick up where user left off with relevant context

#### Parameters

- [param] topic: Optional[str] - Topic or keyword to search for
- [param] timeframe: Optional[TimeFrame] - How far back to look (e.g., "1d", "1 week")

#### Usage Example

```python
prompt = await continue_conversation(
    topic="authentication design",
    timeframe="1 week"
)
```

#### What It Returns

- [output] Formatted context from previous sessions on the topic
- [includes] Recent activity related to topic
- [includes] Relevant entities and observations
- [includes] Suggested continuation points

### 3. Recent Activity Prompt

- [name] "Share Recent Activity"
- [file] src/basic_memory/mcp/prompts/recent_activity.py
- [description] Get recent activity from across the knowledge base
- [purpose] Show what's changed recently

#### Parameters

- [param] timeframe: TimeFrame - How far back to look (default: "7d")

#### Usage Example

```python
prompt = await recent_activity_prompt(timeframe="1 week")
```

#### What It Returns

- [output] Formatted summary of recent activity
- [includes] Up to 5 primary results (entities)
- [includes] Up to 2 related results per primary item
- [includes] Suggestions for creating activity summary note
- [pattern] Encourages summarizing periodic activity for insights

#### Example Output Structure

```markdown
# Recent Activity from (1 week)

## Primary Results
[List of recently updated entities]

## Related Results
[Connected observations and relations]

## Opportunity to Capture Activity Summary
[Suggestion to create summary note with template]
```

### 4. Search Prompt

- [name] "Search Knowledge Base"
- [file] src/basic_memory/mcp/prompts/search.py
- [description] Search across all content in basic-memory
- [endpoint] POST /prompt/search
- [purpose] Provide formatted search results with helpful context

#### Parameters

- [param] query: str - The search text to look for
- [param] timeframe: Optional[TimeFrame] - Optional limit to recent results

#### Usage Example

```python
prompt = await search_prompt(
    query="authentication decisions",
    timeframe="1 month"
)
```

#### What It Returns

- [output] Formatted search results with context
- [includes] Matching entities, observations, relations
- [includes] Contextual information about results
- [format] Structured for agent consumption

## How Prompts Guide Agent Behavior

### Prompts vs Tools

- [comparison] Tools perform actions (search, write, read)
- [comparison] Prompts provide formatted context and guidance
- [comparison] Tools are called by agents; prompts guide what to call
- [workflow] Agent reads prompt → understands pattern → calls tools

### The Prompt → Tool Flow

#### Example: User Asks Complex Question

```
1. User: "Tell me about our authentication approach"

2. Agent references AI Assistant Guide prompt
   → Sees: "Start with specific searches"
   → Sees: "Follow relation paths"
   → Sees: "Build a complete picture before responding"

3. Agent calls search_notes("authentication approach")
   → Gets 3 entities, 5 observations, 2 relations

4. Agent calls build_context("memory://specs/auth-design", depth=2)
   → Gets primary + related entities via graph traversal

5. Agent calls read_note("specs/auth-design")
   → Gets full markdown content

6. Agent synthesizes comprehensive answer from multiple sources
```

- [pattern] Prompt guides strategy, tools execute tactics
- [result] More thorough responses than single-tool calls

### Prompt-Driven Best Practices

#### From AI Assistant Guide

- [practice] "Proactively Record Context" - Offer to capture discussions
- [practice] "Create a Rich Semantic Graph" - Add 3-5 observations per note
- [practice] "Structure Content Thoughtfully" - Use clear titles and sections
- [practice] "Navigate Knowledge Effectively" - Multi-step search pattern
- [practice] "Help Users Maintain Knowledge" - Suggest organizing topics

#### From Recent Activity Prompt

- [practice] Summarize periodic activity for insights
- [practice] Create high-level connections between topics
- [template] Provides write_note template for activity summaries

#### From Continue Conversation Prompt

- [practice] Pick up where user left off
- [practice] Provide relevant historical context
- [practice] Maintain continuity across sessions

## Error Handling Guidance in Prompts

### Search Prompt Error Responses

- [error] FTS5 syntax errors → Suggests simplifying query
- [error] No results found → Suggests broader search strategies
- [error] Server errors → Suggests alternative approaches
- [error] Permission errors → Suggests checking project access
- [guidance] Each error provides 5-10 alternative approaches
- [guidance] Includes examples of valid search syntax

### AI Assistant Guide Error Patterns

#### Missing Content

```python
try:
    content = await read_note("Document")
except:
    results = await search_notes("Document")
    if results and results.primary_results:
        content = await read_note(results.primary_results[0].permalink)
```

- [pattern] Try exact match → fallback to search → read result

#### Forward References

```python
response = await write_note(..., verbose=True)
forward_refs = [r.get('to_name') for r in response.get('relations', [])
                if not r.get('target_id')]

if forward_refs:
    print(f"Forward references: {forward_refs}")
    print("Would you like me to create these notes now?")
```

- [pattern] Detect unresolved relations → inform user → offer to create

#### Sync Issues

```python
activity = await recent_activity(timeframe="1 hour")
if not activity or not activity.primary_results:
    print("You might need to run 'basic-memory sync'.")
```

- [pattern] Check recent activity → detect stale data → suggest sync

## Prompt Enhancement Patterns

### Recent Activity Suggests Capture

- [example] After showing recent activity, suggests creating summary note
- [template] Provides complete write_note example
- [benefit] Encourages meta-notes that connect activities

### Continue Conversation Provides Context

- [example] Loads historical context for topic
- [benefit] Enables seamless multi-session workflows
- [pattern] Agent doesn't need to re-ask for background

### Search Provides Recovery Strategies

- [example] Invalid syntax → provides 6 valid syntax examples
- [example] No results → provides 6 alternative strategies
- [benefit] Agent learns better search techniques from errors

## The Philosophy Behind Prompts

### Knowledge Graph > Isolated Notes

- [quote] "A knowledge graph with 10 heavily connected notes is more valuable than 20 isolated notes" (AI Assistant Guide line 31-32)
- [implication] Prompts emphasize creating relations
- [implication] Multi-step patterns encouraged (search → context → read)
- [result] Agents build comprehensive understanding, not surface-level answers

### Files Are Truth

- [quote] "All knowledge lives in local files on the user's computer" (line 163)
- [implication] Users can edit outside agent interaction
- [implication] Agents should verify with recent_activity
- [result] Respectful coexistence with user file system

### Proactive Recording

- [instruction] "Always consider recording context when users make decisions or reach conclusions" (lines 115-121)
- [protocol] Ask permission → capture if agreed → confirm when done
- [result] Knowledge base grows naturally through conversation

## Comparing the Prompts

| Prompt | Primary Use | Returns | Tools It Suggests |
|--------|-------------|---------|-------------------|
| **AI Assistant Guide** | General reference | 412 lines of best practices | All tools with patterns |
| **Continue Conversation** | Session continuity | Historical context on topic | search_notes, build_context |
| **Recent Activity** | Change awareness | Summary of updates + capture suggestion | recent_activity, write_note |
| **Search** | Find content | Formatted results with recovery strategies | search_notes, build_context |

## How to Use Prompts as an Agent

### 1. Reference AI Assistant Guide First

- [step] Load memory://ai_assistant_guide resource
- [step] Understand core tools and patterns
- [step] Follow best practices for navigation

### 2. Use Specific Prompts for Workflows

- [workflow] User wants to continue → use continue_conversation prompt
- [workflow] User asks "what's new?" → use recent_activity prompt
- [workflow] User asks complex question → use search prompt + follow deep search pattern

### 3. Follow the Guidance

- [pattern] Prompts say "search then build_context" → do both
- [pattern] Prompts say "build complete picture" → use multiple tools
- [pattern] Prompts suggest write_note template → use it

### 4. Learn from Error Responses

- [learning] Search fails → read error guidance → try suggested alternatives
- [learning] Forward references → inform user → offer to create
- [learning] Stale data → suggest sync

## Enhancement Opportunities

- [suggestion] Add explicit "Deep Search Checklist" to AI Assistant Guide
- [suggestion] Create prompt for "Explore Topic Deeply" that orchestrates multi-tool workflow
- [suggestion] Add decision tree for when to use depth=1 vs depth=2 vs depth=3
- [suggestion] Create prompt for "Summarize Knowledge Graph" showing connections
- [suggestion] Add "Debug Search" prompt that explains why no results found

## Relations

- related-to [[Deep Search Pattern - How AI Agents Are Guided to Explore]]
- related-to [[How Search Actually Works - A Practical Deep Dive]]
- related-to [[How Basic Memory Guides Intelligent Note Creation and Search]]
- implements [[MCP Protocol]]
- uses [[AI Assistant Guide]]
