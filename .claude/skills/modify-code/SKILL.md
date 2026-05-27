---
description: Use before modifying code. Checks whether CLAUDE.md, skills, schemas, docs, and tests need updates before implementation.
---

# Modify Code Workflow

Before changing code:

1. Understand the requested change.
2. Identify affected areas:
   - Python Probe SDK
   - Control Server
   - Dashboard
   - shared schemas
   - examples
   - docs
   - tests
   - CLAUDE.md
   - SKILL.md files
3. Decide whether persistent instructions need updates.
   - If a project-wide rule changes, update `CLAUDE.md`.
   - If a repeated workflow changes, update the relevant `SKILL.md`.
   - If a schema or contract changes, update `schema-change` related docs and tests.
4. Make instruction updates first when needed.
5. Implement the code change.
6. Add or update tests when behavior changes.
7. Run relevant checks.
8. Report:
   - changed files
   - tests run
   - tests not run and why
   - risks or follow-up work
