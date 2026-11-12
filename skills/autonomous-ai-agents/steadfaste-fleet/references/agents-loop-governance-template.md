<!-- Renamed from skills/autonomous-ai-agents/hermes-cortex/references/agents-loop-governance-template.md in the 2026-11-12 hc→fleet brand sweep (commit 2dc01b0e); content predates the range. -->

# Template Reference: AGENTS-loop-governance.md

A standalone template for adopting loop-governance scoring in any project's
`AGENTS.md` is available in the steadfaste-fleet repo at:

`docs/templates/AGENTS-loop-governance.md`

## When to use

- You're setting up a new project and want loop-governance integration
- You're adopting steadfaste-fleet's scoring system in an existing project
- You need a lightweight drop-in section for AGENTS.md without copying the
  entire steadfaste-fleet AGENTS.md

## What it covers

- Rule #10 with MCP dual-path (MCP tools for agents, CLI for hooks/scripts)
- Session initialization sequence (config_show + cycle_stats + cache_search)
- Multi-file change scoring guidance
- Troubleshooting table for common scoring failures
- Setup instructions (setup.sh, hermes mcp add, ollama pull, install-score-hook)
