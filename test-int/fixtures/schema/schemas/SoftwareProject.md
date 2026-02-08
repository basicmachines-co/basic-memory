---
title: SoftwareProject
type: schema
entity: SoftwareProject
version: 1
schema:
  name: string, project name
  language?: string, primary programming language
  license?: string, open source license type
  repo_url?: string, repository URL
  maintained_by?: Person, primary maintainer
settings:
  validation: warn
---

# SoftwareProject

A software project or library tracked in the knowledge base. Can be open source
or proprietary. Links to the people who maintain it and the technologies used.

## Observations
- [purpose] Tracks software projects and their key metadata
- [convention] repo_url should be a full URL including protocol
