---
title: 'SPEC-19: Claude Skills Integration Investigation'
type: spec
permalink: specs/spec-19-claude-skills-integration
tags:
- agent-skills
- mcp
- integration
- anthropic
---

# SPEC-19: Claude Skills Integration Investigation

## Why

Anthropic recently released **Agent Skills** - organized folders of instructions, scripts, and resources that agents can discover and load dynamically to perform better at specific tasks. Skills extend Claude's capabilities by packaging expertise into composable resources, transforming general-purpose agents into specialized agents.

**Key Insight:** Agent Skills and Basic Memory share fundamental design principles:
- Both use Markdown files as the primary format
- Both enable knowledge discovery and navigation
- Both focus on providing structured context to AI agents
- Both support local-first, file-based knowledge management

This natural alignment suggests significant integration opportunities that could enhance both systems.

**Reference:** [Equipping agents for the real world with agent skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)

## What

This spec investigates how Basic Memory can integrate with Anthropic's Agent Skills framework to:
1. **Export Basic Memory knowledge as Agent Skills** - Transform knowledge graphs into discoverable skills
2. **Import Agent Skills into Basic Memory** - Treat skills as first-class entities in the knowledge graph
3. **Enhance MCP capabilities** - Leverage skills to provide better agent guidance
4. **Create skill-aware tools** - Build tools that understand and utilize the skills format

**Affected Areas:**
- MCP server implementation (tool and resource exposure)
- Knowledge graph structure (skill entity types)
- Export/import workflows (skill format conversion)
- Documentation and user guides
- Potential new CLI commands for skill management

## How (High Level)

### Understanding Agent Skills Format

Based on Anthropic's announcement, Agent Skills are:
- **Stored as Markdown files** that can reference other files
- **Organized in folders** for discovery
- **Dynamically loadable** by agents based on task requirements
- **Composable** - can build on and reference other skills
- **Include instructions, scripts, and resources**

### Integration Approach 1: Basic Memory → Agent Skills Export

**Concept:** Convert Basic Memory knowledge into Agent Skills format

#### Use Cases
- Transform project documentation into agent skills
- Export domain expertise as discoverable skills
- Create task-specific skills from procedure notes
- Package troubleshooting guides as skills

#### Implementation Strategy
- [ ] Create `export_skills` CLI command
- [ ] Define skill mapping from Basic Memory entities
- [ ] Generate skill directory structure
- [ ] Handle cross-references and relations
- [ ] Support filtering by tags/entity types

#### Example Mapping

**Basic Memory Entity:**
```markdown
---
title: API Authentication Guide
type: guide
permalink: api-authentication-guide
tags:
  - api
  - authentication
  - security
---

# API Authentication Guide

## Observations
- [principle] Always use JWT tokens for stateless authentication #security
- [implementation] Tokens expire after 1 hour #api-design
- [gotcha] Remember to refresh tokens before expiry #best-practice

## Relations
- implements [[Security Specification]]
- uses [[JWT Library]]
- relates_to [[User Management]]
```

**Exported as Agent Skill:**
```markdown
# API Authentication Skill

## Purpose
Provides guidance on implementing JWT-based API authentication in our system.

## When to Use
- Building new API endpoints
- Implementing authentication flows
- Troubleshooting auth issues

## Key Principles
- Always use JWT tokens for stateless authentication
- Tokens expire after 1 hour - implement refresh logic
- Remember to refresh tokens before expiry to avoid disruption

## Related Skills
- [[Security Specification]]
- [[JWT Library]]
- [[User Management]]

## Resources
[Link to implementation examples]
```

### Integration Approach 2: Agent Skills → Basic Memory Import

**Concept:** Import existing Agent Skills into Basic Memory knowledge graph

#### Use Cases
- Centralize skills in searchable knowledge base
- Connect skills to other knowledge entities
- Track skill usage and evolution
- Enable full-text search across skills
- Visualize skill relationships in canvas

#### Implementation Strategy
- [ ] Create `import skills` CLI command
- [ ] Parse skill markdown format
- [ ] Convert to Basic Memory entity format
- [ ] Preserve cross-references as relations
- [ ] Add skill-specific entity type
- [ ] Enable skill-aware search filters

#### Example Flow
```bash
# Import skills directory
basic-memory import skills --source ~/agent-skills --folder skills

# Skills become searchable entities
basic-memory tools search --query "authentication" --types skill

# Skills appear in knowledge graph with relations
```

### Integration Approach 3: Skill-Aware MCP Resources

**Concept:** Expose Basic Memory knowledge as dynamically discoverable MCP resources that follow Agent Skills patterns

#### Current State Analysis

Basic Memory already has skill-like resources:
- `ai_assistant_guide()` - Provides tool usage guidance
- `continue_conversation()` - Context continuation patterns
- `search()` - Formatted search results
- `recent_activity()` - Activity-based context

**Enhancement Opportunity:** Structure these resources using Agent Skills principles:
- Clear "when to use" guidance
- Composable components
- Task-specific instructions
- Dynamic discovery based on context

#### Implementation Strategy
- [ ] Audit existing MCP resources against skills framework
- [ ] Add skill-style metadata (purpose, when-to-use, prerequisites)
- [ ] Create resource discovery tool
- [ ] Enable context-aware resource suggestions
- [ ] Document resource composition patterns

### Integration Approach 4: Skill Generation Tools

**Concept:** Add MCP tools that help agents create and manage skills

#### New MCP Tools
```python
@mcp.tool()
async def create_skill(
    title: str,
    purpose: str,
    instructions: str,
    when_to_use: str,
    project: str,
    related_skills: list[str] = None
) -> str:
    """Create a new agent skill in the knowledge base.

    Converts skill metadata into Basic Memory entity with proper
    formatting for both Basic Memory and Agent Skills consumption.
    """
    pass

@mcp.tool()
async def list_skills(
    project: str,
    category: str = None,
    tags: list[str] = None
) -> str:
    """Discover available skills in the knowledge base.

    Returns formatted list of skills with metadata for
    agent decision-making about which skills to load.
    """
    pass

@mcp.tool()
async def export_skill(
    identifier: str,
    output_dir: str,
    format: str = "anthropic"
) -> str:
    """Export a Basic Memory entity as an Agent Skill.

    Converts entity format to standard Agent Skills format
    with proper structure and cross-references.
    """
    pass
```

### Integration Approach 5: Unified Knowledge + Skills Format

**Concept:** Evolve Basic Memory's markdown format to natively support both knowledge graph and skills patterns

#### Proposed Entity Type: `skill`

```markdown
---
title: Database Query Optimization Skill
type: skill
permalink: db-query-optimization-skill
tags:
  - database
  - performance
  - postgresql
skill_metadata:
  purpose: Optimize slow database queries
  when_to_use: Query times exceed 100ms or N+1 patterns detected
  prerequisites:
    - Database access
    - Query execution plan
  difficulty: intermediate
---

# Database Query Optimization Skill

## Purpose
Optimize slow database queries in PostgreSQL through systematic analysis and tuning.

## When to Use
- Query execution times exceed 100ms
- Detecting N+1 query patterns
- High database CPU usage
- Slow API response times

## Prerequisites
- [ ] Database access credentials
- [ ] Ability to run EXPLAIN ANALYZE
- [ ] Understanding of query patterns

## Instructions

### Step 1: Identify Slow Queries
- [technique] Use pg_stat_statements to find slow queries #postgresql
- [technique] Look for high call counts combined with long execution times #analysis
- [tool] Enable query logging with log_min_duration_statement #configuration

### Step 2: Analyze Query Plans
- [technique] Run EXPLAIN ANALYZE on slow queries #debugging
- [technique] Look for Sequential Scans on large tables #red-flag
- [technique] Check for missing indexes #optimization

### Step 3: Apply Optimizations
- [technique] Add indexes on frequently filtered columns #indexing
- [technique] Rewrite queries to use indexes effectively #query-optimization
- [technique] Consider query result caching #caching

## Common Patterns

### N+1 Query Problem
- [problem] Loading collection in loop causes N+1 queries #anti-pattern
- [solution] Use eager loading with joins or subqueries #fix (Reduces queries from N+1 to 1)

### Missing Index
- [problem] Sequential scan on large table #performance-issue
- [solution] Create index on filtered/sorted columns #fix (Can improve by 100x+)

## Relations
- requires [[PostgreSQL Knowledge]]
- uses [[Database Profiling Tools]]
- prevents [[API Performance Issues]]
- documented_in [[Performance Optimization Guide]]

## Resources
- PostgreSQL EXPLAIN documentation
- pg_stat_statements reference
- Index design guidelines

## Examples
[Concrete code examples would go here]
```

**Key Features:**
- Native `type: skill` entity type
- `skill_metadata` in frontmatter for discovery
- Instructions structured as observations (maintains knowledge graph)
- Relations connect to broader knowledge
- Follows both Basic Memory and Agent Skills patterns

## How to Evaluate

### Phase 1: Research & Validation ✅
- [x] Document Agent Skills format and capabilities
- [x] Analyze Basic Memory's current architecture
- [x] Identify integration opportunities
- [ ] Review Anthropic's skills documentation in detail (when available)
- [ ] Create proof-of-concept skill format examples

### Phase 2: Export Capability
- [ ] Implement `basic-memory export skills` command
- [ ] Test with real Basic Memory projects
- [ ] Validate exported skills work with Claude
- [ ] Document export format and mapping rules
- [ ] Create export templates for common patterns

### Phase 3: Import Capability
- [ ] Implement `basic-memory import skills` command
- [ ] Test with Anthropic's example skills (when available)
- [ ] Verify imported skills maintain structure
- [ ] Enable search and discovery of imported skills
- [ ] Document import workflow

### Phase 4: Enhanced MCP Resources
- [ ] Audit existing MCP resources
- [ ] Add skill-style metadata and structure
- [ ] Implement resource discovery tool
- [ ] Test with real agent workflows
- [ ] Document best practices for resource composition

### Phase 5: Native Skill Support
- [ ] Add `skill` entity type to schema
- [ ] Implement skill-specific tools
- [ ] Create skill templates
- [ ] Update documentation
- [ ] Build example skills library

## Open Questions

1. **Format Specification**: What is the exact Agent Skills markdown format?
   - Need detailed spec from Anthropic
   - File structure conventions
   - Metadata requirements
   - Cross-reference syntax

2. **Discovery Mechanism**: How do agents discover and load skills?
   - File system conventions?
   - Manifest file?
   - MCP resource exposure?
   - Dynamic querying?

3. **Skill Composition**: How do skills reference and build on each other?
   - WikiLink style references?
   - Import mechanisms?
   - Dependency management?

4. **Version Management**: How are skills versioned and updated?
   - Git-based versioning?
   - Version metadata in frontmatter?
   - Breaking change handling?

5. **Skill Scope**: What's the granularity of a skill?
   - Task-specific vs domain-specific?
   - Single file vs directory structure?
   - When to split vs combine skills?

## Benefits

### For Basic Memory Users
- **Knowledge as Skills**: Transform existing knowledge into actionable skills
- **Better Agent Performance**: Agents get structured, task-specific guidance
- **Skill Library**: Build and share reusable skills
- **Unified System**: One knowledge base serves multiple purposes

### For Agent Skills Users
- **Knowledge Graph**: Connect skills through semantic relations
- **Full-Text Search**: Find skills across entire knowledge base
- **Versioning**: Track skill evolution through git
- **Visualization**: See skill relationships in canvas
- **Cloud Sync**: Share skills across devices

### For the Ecosystem
- **Interoperability**: Bridge between knowledge management and agent skills
- **Best Practices**: Demonstrate skill creation patterns
- **Community**: Enable skill sharing and collaboration
- **Innovation**: Enable new use cases at intersection

## Risks and Considerations

### Technical Risks
- **Format Divergence**: If Agent Skills format evolves, maintain compatibility
- **Performance**: Skill export/import must be fast enough for large knowledge bases
- **Data Loss**: Ensure bidirectional conversion preserves information

### Design Risks
- **Over-Engineering**: Keep it simple - don't force-fit concepts
- **Confusion**: Clear distinction between knowledge entities and skills
- **Fragmentation**: Avoid creating parallel systems

### Mitigation Strategies
- Start with simple export/import (Phase 2-3)
- Get user feedback before native skill support (Phase 5)
- Maintain backward compatibility with existing entities
- Document clear use cases for when to use skills vs standard entities

## Next Steps

1. **Immediate** (Next Sprint):
   - [ ] Research detailed Agent Skills specification
   - [ ] Create proof-of-concept skill examples
   - [ ] Validate approach with community feedback
   - [ ] Update this spec based on findings

2. **Short Term** (1-2 months):
   - [ ] Implement basic export functionality
   - [ ] Implement basic import functionality
   - [ ] Test with real projects
   - [ ] Document workflows

3. **Long Term** (3-6 months):
   - [ ] Add native skill entity type
   - [ ] Build skill-specific MCP tools
   - [ ] Create example skills library
   - [ ] Enable skill marketplace/sharing

## References

- [Anthropic: Agent Skills Announcement](https://www.anthropic.com/news/skills)
- [Anthropic: Equipping agents for the real world with agent skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)
- [Basic Memory: AI Assistant Guide](https://github.com/basicmachines-co/basic-memory/blob/main/docs/ai-assistant-guide-extended.md)
- [Model Context Protocol](https://modelcontextprotocol.io/)

## Related Specs

- [[SPEC-1: Specification-Driven Development Process]]
- [[SPEC-6: Explicit Project Parameter Architecture]]
- [[SPEC-16: MCP Cloud Service Consolidation]]

---

**Status**: Investigation phase - gathering requirements and validating approach
**Owner**: TBD
**Created**: 2025-10-16
**Last Updated**: 2025-10-16
