# trk

Spec-driven traceability CLI for software projects.

Tracks the links between **specs**, **tickets**, **code**, and **tests**. Zero dependencies (Python 3.10+ stdlib only).

> ~~Vibe coded~~ Agentically developed with love with [Claude Code](https://claude.com/claude-code).

## Why

Most project management tools track *tasks*. trk tracks the **traceability graph** — which spec section led to which ticket, which ticket links to which code, and where the gaps are.

```
Spec (what to build)
  -> Tickets (work items)
    -> Code (implementation)
    -> Tests (proof it works)
```

trk answers questions like:
- "What spec covers this code?" (`trk trace path/to/file.py`)
- "Which specs have no implementation tickets?" (`trk gaps`)
- "What code is orphaned — not linked from any ticket?" (`trk orphans`)
- "How complete is this spec?" (`trk completeness my-spec`)

## Install

```bash
# With uv (recommended):
uv tool install trk

# With pipx:
pipx install trk

# Or just clone and run directly:
python3 path/to/trk/src/trk/__init__.py ls
```

## Quick Start

### 1. Add `trk.yaml` to your project root

```yaml
# trk.yaml - minimal config
tickets_dir: .tickets
specs_dir: docs/spec
```

### 2. Create the directory structure

```
your-project/
├── trk.yaml
├── .tickets/
│   ├── TEMPLATE.md          # Optional: ticket template
│   └── impl/                # Implementation tickets
│       └── my-feature/
│           └── README.md
├── docs/
│   └── spec/
│       └── MY_FEATURE.md    # Specs with {#anchor} sections
```

### 3. Run trk

```bash
trk init    # Project overview (great for agent onboarding)
trk ls      # List all specs with ticket counts
trk open    # See all open tickets
```

## Configuration

trk looks for `trk.yaml` by walking up from your current directory. All paths are relative to the config file.

```yaml
# trk.yaml - full config

# Required
tickets_dir: .tickets          # Contains impl/ subdirectory
specs_dir: docs/spec           # Markdown specs with {#anchor} sections

# Optional features (comment out to disable)
use_cases_dir: docs/use_cases  # Use case tracking
roadmap_file: docs/ROADMAP.md  # Milestone tracking
benchmarks_dir: benchmarks     # Benchmark management
contacts_dir: docs/contacts    # CRM / contact management
decisions_dir: docs/decisions  # ADR cross-references
template: .tickets/TEMPLATE.md # Ticket creation template

# Code directories to scan (for audit/orphan analysis)
# Format: "path:pattern1,pattern2"
code_dirs:
  - "src:*.py"
  - "lib:*.ts,*.tsx"

# Test directories to scan
test_dirs:
  - "tests:test_*.py"
  - "src:*.test.ts"

# Directories to skip during scanning
skip_dirs:
  - node_modules
  - __pycache__
  - .venv

# Optional: Python file with custom validators for requirement checking
# validators_file: trk_validators.py
```

## Validators (Optional)

For use case requirement checking (`trk uc show`), you can provide project-specific validators. Create a Python file that exports a `VALIDATORS` dict:

```python
# trk_validators.py
from pathlib import Path

def check_ops(root: Path, ref: str) -> bool:
    """Check if an operation exists. ref format: 'domain:op_name'"""
    domain, op = ref.split(":", 1)
    ops_dir = root / "domains" / domain / "operations"
    return ops_dir.exists() and len(list(ops_dir.rglob(f"{op}.yaml"))) > 0

VALIDATORS = {
    "ops": check_ops,
}
```

Then reference it in `trk.yaml`:
```yaml
validators_file: trk_validators.py
```

Each validator receives `(project_root: Path, requirement_string: str)` and returns `bool`.

## Agent Onboarding

When an AI agent starts working on your project, it can run:

```bash
trk init
```

This prints:
- Project name and root
- Reading order (CLAUDE.md, specs, ADRs)
- Ticket summary (open/closed counts)
- Active work areas
- Key commands to get started

## Commands

### Ticket Management
| Command | Description |
|---------|-------------|
| `trk ls` | List all specs with ticket counts |
| `trk ls <spec>` | List tickets for a specific spec |
| `trk open` | List all open tickets |
| `trk show <spec>/<ticket>` | Show ticket details |
| `trk close <spec>/<ticket> -c path` | Close with code link |
| `trk new <name>` | Create a new ticket from template |
| `trk summary` | Overall completion summary |
| `trk dashboard` | Visual progress dashboard |

### Spec Management
| Command | Description |
|---------|-------------|
| `trk scaffold <spec>` | Create tickets from spec sections |
| `trk specs` | List all spec files |
| `trk completeness <spec>` | Section-level coverage check |
| `trk status` | Show status for all specs |

### Graph Traversal
| Command | Description |
|---------|-------------|
| `trk trace <path>` | What spec/ticket covers this code? |
| `trk graph <spec>` | Full graph: spec -> tickets -> code |
| `trk graph -r <path>` | Reverse: what specs touch this dir? |
| `trk related <spec>` | Specs that link to/from this spec |

### Audit
| Command | Description |
|---------|-------------|
| `trk audit` | Code coverage and orphan analysis |
| `trk orphans` | List orphan code directories |
| `trk match` | Suggest orphan -> ticket matches |
| `trk validate` | Verify all `[[type:id]]` links resolve |
| `trk gaps` | Specs without tickets |

### Use Cases, Roadmap, CRM, Benchmarks
| Command | Description |
|---------|-------------|
| `trk uc ls` | List use cases with completion % |
| `trk roadmap` | Milestone overview |
| `trk crm ls` | Contact list |
| `trk bench ls` | Benchmark status |

Run `trk help` for the full command reference.

## Spec Format

Specs are markdown files with `{#anchor}` section tags:

```markdown
---
spec: my-feature
---

# My Feature

## Authentication {#auth}

Users must authenticate via OAuth2...

## Data Model {#data-model}

The following tables are required...
```

The `{#anchor}` tags are what trk uses to create per-section tickets and track completeness.

## Link Syntax

trk uses `[[type:id]]` links for cross-referencing:

- `[[spec:my-feature]]` — link to a spec
- `[[spec:my-feature#auth]]` — link to a spec section
- `[[adr:0005]]` — link to an ADR
- `[[code:src/auth.py]]` — link to code
- `[[ticket:my-feature/auth]]` — link to a ticket

## License

Apache-2.0
