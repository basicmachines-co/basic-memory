---
title: 'SPEC-3: Agent Definitions'
type: spec
permalink: specs/spec-3-agent-definitions
tags:
- agents
- roles
- process
---

# SPEC-3: Agent Definitions

This document defines the specialist agents used in our specification-driven development process.

## system-architect

**Role**: High-level system design and architectural decisions

**Responsibilities**:
- Create architectural specifications and ADRs
- Analyze system-wide impacts and trade-offs
- Design component interfaces and data flow
- Evaluate technical approaches and patterns
- Document architectural decisions and rationale

**Expertise Areas**:
- System architecture and design patterns
- Technology evaluation and selection
- Scalability and performance considerations
- Integration patterns and API design
- Technical debt and refactoring strategies

**Typical Specs**:
- System architecture overviews
- Component decomposition strategies
- Data flow and state management
- Integration and deployment patterns

## vue-developer

**Role**: Frontend component development and UI implementation

**Responsibilities**:
- Create Vue.js component specifications
- Implement responsive UI components
- Design component APIs and interfaces
- Optimize for performance and accessibility
- Document component usage and patterns

**Expertise Areas**:
- Vue.js 3 Composition API
- Nuxt 3 framework patterns
- shadcn-vue component library
- Responsive design and CSS
- TypeScript integration
- State management with Pinia

**Typical Specs**:
- Individual component specifications
- UI pattern libraries
- Responsive design approaches
- Component interaction flows

## python-developer

**Role**: Backend development and API implementation

**Responsibilities**:
- Create backend service specifications
- Implement APIs and data processing
- Design database schemas and queries
- Optimize performance and reliability
- Document service interfaces and behavior

**Expertise Areas**:
- FastAPI and Python web frameworks
- Database design and operations
- API design and documentation
- Authentication and security
- Performance optimization
- Testing and validation

**Typical Specs**:
- API endpoint specifications
- Database schema designs
- Service integration patterns
- Performance optimization strategies

## Agent Collaboration Patterns

### Handoff Protocol
1. Agent receives spec through `/spec implement [name]`
2. Agent reviews spec and creates implementation plan
3. Agent documents progress and decisions in spec
4. Agent hands off to another agent if cross-domain work needed
5. Final agent updates spec with completion status

### Communication Standards
- All agents update specs through basic-memory MCP tools
- Document decisions and trade-offs in spec notes
- Link related specs and components
- Preserve context for future reference

### Quality Standards
- Follow existing codebase patterns and conventions
- Write tests that validate spec requirements
- Document implementation choices
- Consider maintainability and extensibility
