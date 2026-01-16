---
title: Basic Memory Question Templates - When and How to Use Them
type: note
permalink: guides/basic-memory-question-templates-when-and-how-to-use-them
tags:
- basic-memory,questions,examples,conversation-continuation
---

A categorized list of example questions for effectively using Basic Memory, organized by scenario and use case.

## Conversation Continuation

### Picking Up Where You Left Off

**Use when:** Starting a new conversation but want to continue previous work

**Examples:**

- [example] "Continue our discussion about [topic] from last week"
- [example] "Pick up where we left off with [project name]"
- [example] "What were we working on in the [project name] project?"
- [example] "Catch me up on what we discussed about [topic] recently"
- [example] "Resume our conversation about implementing [feature]"

**What Claude does:**
- Searches recent_activity for mentions of the topic
- Loads relevant observations and context
- Summarizes what was discussed
- Continues from that point

### Reviewing Recent Work

**Use when:** Want to see what you've been learning or working on

**Examples:**

- [example] "What have I learned in the last week?"
- [example] "Show me everything I documented about [topic] this month"
- [example] "What problems did I solve in the last 3 days?"
- [example] "What decisions have I made about [project] recently?"
- [example] "Summarize my recent notes about [topic]"

**Parameters to adjust:**
- Timeframes: "1d", "3d", "7d", "2w", "1m", "3m"
- Topics: Be specific or broad

### Deep Dives

**Use when:** Need comprehensive information on a topic you've documented

**Examples:**

- [example] "Tell me everything I know about [technology/concept]"
- [example] "What patterns have I documented for [problem type]?"
- [example] "Show me all my notes related to [project or topic]"
- [example] "What solutions have I documented for [specific problem]?"
- [example] "What are all the technologies I've noted for [project]?"

**What Claude does:**
- Performs comprehensive search across all notes
- Groups related observations by category
- Shows connections between concepts

## Project-Specific Questions

### Getting Project Context

**Use when:** Starting work on a project

**Examples:**

- [example] "What's my tech stack for [project name]?"
- [example] "What conventions am I following in [project name]?"
- [example] "What decisions have I made about [project name]?"
- [example] "Remind me of the architecture for [project name]"
- [example] "What are the key requirements for [project name]?"

**Best practice:** Create a project note first with [stack], [pattern], [convention] observations

### Getting Unstuck

**Use when:** Facing a problem you may have solved before

**Examples:**

- [example] "Have I solved [specific problem] before?"
- [example] "What's my usual approach to [type of problem]?"
- [example] "Show me similar problems I've debugged"
- [example] "What patterns do I use for [specific task]?"
- [example] "How did I handle [situation] last time?"

**What Claude does:**
- Searches for [problem], [solution], [pattern] observations
- Finds related debugging sessions
- Suggests approaches based on your history

## Learning and Knowledge Retrieval

### Concept Lookup

**Use when:** Need to recall something you learned

**Examples:**

- [example] "What do I know about [concept]?"
- [example] "Explain [technology] based on my notes"
- [example] "What are the key benefits of [tool/approach] I documented?"
- [example] "What gotchas did I note about [technology]?"
- [example] "What limitations did I document for [tool]?"

### Comparison Queries

**Use when:** Need to compare options or recall trade-offs

**Examples:**

- [example] "What are the pros and cons of [option A] vs [option B] in my notes?"
- [example] "Why did I choose [technology] over [alternative]?"
- [example] "Compare my notes on [approach A] and [approach B]"
- [example] "What trade-offs did I document for [decision]?"
- [example] "What alternatives did I consider for [choice]?"

## Documentation Requests

### Creating New Notes

**Use when:** Want Claude to help structure new knowledge

**Examples:**

- [example] "Create a Basic Memory note documenting [what I just learned]"
- [example] "Help me document this debugging session in Basic Memory"
- [example] "Create a project note for [new project] with my tech stack"
- [example] "Document this decision about [topic] with rationale"
- [example] "Turn this conversation into a structured note"

**What Claude does:**
- Structures content with [category] observations
- Creates appropriate relations
- Suggests relevant tags

### Updating Existing Notes

**Use when:** Have new information to add to existing knowledge

**Examples:**

- [example] "Add this new learning to my [topic] note"
- [example] "Update my [project] note with these new requirements"
- [example] "Add these gotchas to my [technology] documentation"
- [example] "Append this solution to my [problem type] notes"

## Search and Discovery

### Finding Specific Information

**Use when:** Looking for something specific

**Examples:**

- [example] "Search my notes for [keyword or phrase]"
- [example] "Find all mentions of [term] in my notes"
- [example] "Where did I document [specific thing]?"
- [example] "Show me notes tagged with [tag]"
- [example] "Find notes in the [folder name] folder"

### Exploring Connections

**Use when:** Want to understand relationships in your knowledge

**Examples:**

- [example] "What concepts are related to [topic]?"
- [example] "Show me the knowledge graph around [concept]"
- [example] "What notes link to [specific note]?"
- [example] "How does [concept A] connect to [concept B] in my notes?"

## Time-Based Queries

### Recent Activity

**Use when:** Want to see what changed recently

**Examples:**

- [example] "What did I learn today?"
- [example] "Show me notes updated in the last 3 days"
- [example] "What's new in my knowledge base this week?"
- [example] "What have I been working on this month?"

**Timeframe syntax:**
- Hours: "2h", "6h", "12h"
- Days: "1d", "3d", "7d"  
- Weeks: "1w", "2w"
- Months: "1m", "3m", "6m"
- Or natural: "today", "yesterday", "last week", "this month"

### Historical Lookups

**Use when:** Need to recall older information

**Examples:**

- [example] "What was I working on in October?"
- [example] "Show me notes from 6 months ago about [topic]"
- [example] "What did I learn about [topic] when I first started?"
- [example] "Find the decision we made about [thing] back in [timeframe]"

## Synthesis and Analysis

### Pattern Recognition

**Use when:** Want to identify patterns in your work

**Examples:**

- [example] "What patterns do I commonly use for [task type]?"
- [example] "What mistakes do I repeatedly encounter with [technology]?"
- [example] "What's my usual debugging approach?"
- [example] "What conventions do I follow across projects?"

### Knowledge Gaps

**Use when:** Identifying what you don't know

**Examples:**

- [example] "What don't I know about [topic]?"
- [example] "What aspects of [technology] haven't I documented?"
- [example] "What questions about [topic] remain unanswered in my notes?"

## Best Practices for Questions

### Be Specific with Context

**Less effective:**
- [antipattern] "What did we talk about?"
- [antipattern] "Show me my notes"

**More effective:**
- [pattern] "What did we discuss about authentication last Tuesday?"
- [pattern] "Show me my React notes from this month"

### Use Timeframes When Relevant

**Examples:**
- [pattern] "What have I learned about Docker in the last 2 weeks?"
- [pattern] "Show me recent decisions about the API design"

### Reference Specific Projects or Topics

**Examples:**
- [pattern] "Based on my Project Alpha notes, help me solve [problem]"
- [pattern] "Using my Next.js patterns, suggest an approach for [task]"

### Combine Searches

**Examples:**
- [pattern] "What problems and solutions have I documented about React hooks?"
- [pattern] "Show me decisions and their reasons for [project name]"

## Question Templates by Observation Type

### For [problem] observations
- "What problems have I documented about [topic]?"
- "Show me unsolved problems in [project]"

### For [solution] observations
- "How did I solve [specific problem]?"
- "What solutions work for [problem type]?"

### For [pattern] observations
- "What patterns do I use for [task]?"
- "Show me all design patterns I've documented"

### For [decision] observations
- "Why did I decide to [choice]?"
- "What decisions affected [project]?"

### For [learning] observations
- "What have I learned about [topic]?"
- "Show me my key insights about [technology]"

## Advanced Usage

### Combining Multiple Sources

**Examples:**
- [example] "Based on my notes about [tech A] and [tech B], which should I use for [use case]?"
- [example] "Compare what I learned about [approach A] with my experience using [approach B]"

### Context-Aware Requests

**Examples:**
- [example] "Given my stack and patterns, how should I implement [feature]?"
- [example] "Considering my previous solutions, what's the best approach for [problem]?"

### Meta Questions

**Examples:**
- [example] "What topics do I document most frequently?"
- [example] "What areas of knowledge am I building?"
- [example] "What's the structure of my knowledge base?"

## Relations

- related_to [[Basic Memory]]
- related_to [[Conversation Patterns]]
- related_to [[Knowledge Retrieval]]
- complements [[How to Interact with Basic Memory - Practical Prompt Examples]]