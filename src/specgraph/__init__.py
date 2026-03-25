#!/usr/bin/env python3
"""
specgraph - Spec-driven traceability CLI for software projects.

Tracks the links between specs, tickets, code, and tests.
Zero dependencies (Python 3.10+ stdlib only).

Configure per-project via specgraph.yaml in your project root.

TICKET MANAGEMENT:
    specgraph ls                       # List all specs with ticket counts
    specgraph ls <spec>                # List tickets for a spec
    specgraph ls --status=open         # Filter by status
    specgraph show <spec>/<ticket>     # Show ticket details
    specgraph summary                  # Overall summary
    specgraph open                     # List all open tickets
    specgraph close <spec>/<ticket>    # Close a ticket as implemented
    specgraph close <t> -c path/code   # Close with code link
    specgraph close <t> -t path/test   # Close with test link

SPEC MANAGEMENT:
    specgraph scaffold <spec>          # Create tickets from spec sections
    specgraph scaffold --all           # Scaffold all specs in queue
    specgraph status                   # Show status for all specs
    specgraph status <spec>            # Show status for one spec
    specgraph queue                    # Show spec queue vs complete
    specgraph next                     # Show next spec to process
    specgraph complete <spec>          # Move spec from queue to complete

GRAPH TRAVERSAL:
    specgraph trace <path>             # Reverse lookup: what spec/ticket covers this code?
    specgraph graph <spec>             # Show full graph: spec -> tickets -> code/tests
    specgraph graph --reverse <path>   # What specs touch this directory?
    specgraph related <spec>           # Show specs that link to/from this spec

USE CASE TRACKING:
    specgraph uc ls                    # List all use cases with completion %
    specgraph uc show <id>             # Show use case with requirement status
    specgraph uc gaps <id>             # Show only missing requirements
    specgraph uc new <name>            # Create a new use case from template

ROADMAP:
    specgraph roadmap                  # All milestones with reqs + ticket status
    specgraph roadmap <id>             # Detail view for one milestone
    specgraph roadmap --deadlines      # External deadlines sorted by date

FILTERING:
    specgraph ls --milestone <id>      # Tickets assigned to a milestone
    specgraph ls --service <name>      # Tickets touching a service
    specgraph ls --domain <name>       # Tickets in a domain
    specgraph ls --demand UC-XXX       # Tickets serving a use case (via demand: frontmatter)

CRM (Contact Management):
    specgraph crm ls                   # List all contacts sorted by tier
    specgraph crm ls --status warm     # Filter by status
    specgraph crm ls --category investor  # Filter by category
    specgraph crm ls --tier 1          # Filter by tier
    specgraph crm show <name>          # Show full contact details
    specgraph crm new <name>           # Create contact from template
    specgraph crm follow-ups           # Show contacts with pending next_action

BENCHMARKS:
    specgraph bench ls                 # List all benchmarks with status
    specgraph bench run                # Run all active benchmarks
    specgraph bench run <id>           # Run a specific benchmark
    specgraph bench run --milestone <id>  # Run benchmarks for a milestone
    specgraph bench run --uc UC-XXX   # Run benchmarks for a use case
    specgraph bench status             # Show latest pass/fail for all
    specgraph bench compare <id>       # Detailed metrics vs. thresholds

VALIDATION & AUDIT:
    specgraph audit                    # Audit code coverage and orphans
    specgraph audit --verbose          # Show all linked paths
    specgraph orphans                  # List orphan directories by module
    specgraph orphans --min-files 3    # Only show dirs with 3+ files
    specgraph match                    # Suggest orphan -> ticket matches
    specgraph match --script           # Output as copy-paste commands
    specgraph validate                 # Validate all links resolve
    specgraph gaps                     # Specs in queue without tickets
    specgraph prune                    # Remove non-actionable tickets
    specgraph prune --dry-run          # Preview what would be deleted

AGENT ONBOARDING:
    specgraph init                     # Print project overview for agents

Examples:
    specgraph ls artifact-catalog
    specgraph show artifact-catalog/security
    specgraph scaffold WORKSPACE
    specgraph scaffold --all
    specgraph status workspace
    specgraph complete workspace
"""

import argparse
import importlib.util
import sys
import re
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CONFIG_FILE = "specgraph.yaml"

# Default config values (used when specgraph.yaml omits a key)
DEFAULT_CONFIG = {
    "tickets_dir": ".tickets",
    "specs_dir": "docs/spec",
    "use_cases_dir": "docs/use_cases",
    "roadmap_file": "docs/ROADMAP.md",
    "benchmarks_dir": "benchmarks",
    "contacts_dir": "docs/contacts",
    "decisions_dir": "docs/decisions",
    "template": ".tickets/TEMPLATE.md",
    "code_dirs": [],
    "test_dirs": [],
    "skip_dirs": ["node_modules", "__pycache__", ".venv", "venv"],
    "validators_file": "",
}


def find_project_root() -> Optional[Path]:
    """Walk up from cwd looking for specgraph.yaml."""
    cwd = Path.cwd()
    for parent in [cwd] + list(cwd.parents):
        if (parent / CONFIG_FILE).exists():
            return parent
    return None


def parse_config_yaml(path: Path) -> dict:
    """Parse a simple YAML file (no external deps).

    Handles:
      key: value        -> str
      key:              -> start of list
        - item          -> list item
    """
    result = {}
    current_key = None
    current_list = None

    for line in path.read_text().split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # List item
        if line.startswith("  - "):
            if current_key is not None and current_list is not None:
                val = line[4:].strip().strip('"').strip("'")
                current_list.append(val)
            continue

        # Key: value
        match = re.match(r'^(\w[\w_-]*)\s*:\s*(.*)$', line)
        if match:
            key, value = match.groups()
            value = value.strip().strip('"').strip("'")

            # Save previous list
            if current_key is not None and current_list is not None:
                result[current_key] = current_list

            if value == "" or value == "[]":
                current_key = key
                current_list = []
            else:
                result[key] = value
                current_key = None
                current_list = None

    # Save final list
    if current_key is not None and current_list is not None:
        result[current_key] = current_list

    return result


def load_config(project_root: Path) -> dict:
    """Load config from specgraph.yaml, falling back to defaults."""
    config_path = project_root / CONFIG_FILE
    if config_path.exists():
        raw = parse_config_yaml(config_path)
    else:
        raw = {}

    config = {}
    for key, default in DEFAULT_CONFIG.items():
        config[key] = raw.get(key, default)

    return config


def parse_dir_spec(spec: str) -> tuple:
    """Parse 'path:pattern1,pattern2' into (path, [patterns])."""
    if ":" in spec:
        path, patterns_str = spec.split(":", 1)
        patterns = [p.strip() for p in patterns_str.split(",")]
    else:
        path = spec
        patterns = ["*.py"]
    return path, patterns


# ---------------------------------------------------------------------------
# Validator plugin system
# ---------------------------------------------------------------------------

def load_validators(project_root: Path, config: dict) -> dict:
    """Load validator functions from the project's validators file.

    The file should export a VALIDATORS dict mapping category names
    to callables: (project_root: Path, ref: str) -> bool

    The built-in 'tickets' validator is always available.
    """
    validators = {}

    validators_file = config.get("validators_file", "")
    if validators_file:
        vpath = project_root / validators_file
        if vpath.exists():
            try:
                spec = importlib.util.spec_from_file_location(
                    "specgraph_validators", str(vpath))
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                validators = dict(getattr(mod, "VALIDATORS", {}))
            except Exception as e:
                print(f"Warning: could not load validators from {vpath}: {e}",
                      file=sys.stderr)

    # Built-in: ticket_closed validator (always available)
    if "tickets" not in validators:
        validators["tickets"] = lambda root, name: _check_ticket_closed(name)

    return validators


# ---------------------------------------------------------------------------
# Resolve paths from config
# ---------------------------------------------------------------------------

# These globals are set by _init_paths() which is called from main().
# They exist as module-level vars so all the cmd_* functions can use them
# without threading a config object through every call.

PROJECT_ROOT: Path = Path(".")
TICKETS_DIR: Path = Path(".")
IMPL_TICKETS_DIR: Path = Path(".")
SPEC_DIR: Path = Path(".")
SPEC_QUEUE: Path = Path(".")
SPEC_COMPLETE: Path = Path(".")
SPEC_TICKETS_DIR: Path = Path(".")
USE_CASES_DIR: Path = Path(".")
ROADMAP_FILE: Path = Path(".")
BENCH_DIR: Path = Path(".")
CONTACTS_DIR: Path = Path(".")
DECISIONS_DIR: Path = Path(".")
TEMPLATE_PATH: Path = Path(".")
CODE_DIRS: list = []
TEST_DIRS: list = []
SKIP_DIRS: list = []
UC_CHECKERS: dict = {}


def _init_paths(project_root: Path, config: dict):
    """Set module-level path globals from config."""
    global PROJECT_ROOT, TICKETS_DIR, IMPL_TICKETS_DIR, SPEC_DIR
    global SPEC_QUEUE, SPEC_COMPLETE, SPEC_TICKETS_DIR
    global USE_CASES_DIR, ROADMAP_FILE, BENCH_DIR, CONTACTS_DIR
    global DECISIONS_DIR, TEMPLATE_PATH, CODE_DIRS, TEST_DIRS, SKIP_DIRS
    global UC_CHECKERS

    PROJECT_ROOT = project_root
    TICKETS_DIR = project_root / config["tickets_dir"]
    IMPL_TICKETS_DIR = TICKETS_DIR / "impl"
    SPEC_DIR = project_root / config["specs_dir"]

    # Legacy compat: queue/complete aliases point to same dir
    SPEC_QUEUE = SPEC_DIR
    SPEC_COMPLETE = SPEC_DIR
    SPEC_TICKETS_DIR = IMPL_TICKETS_DIR

    USE_CASES_DIR = project_root / config["use_cases_dir"]
    ROADMAP_FILE = project_root / config["roadmap_file"]
    BENCH_DIR = project_root / config["benchmarks_dir"]
    CONTACTS_DIR = project_root / config["contacts_dir"]
    DECISIONS_DIR = project_root / config["decisions_dir"]
    TEMPLATE_PATH = project_root / config["template"]

    CODE_DIRS = []
    for spec_str in config.get("code_dirs", []):
        path, patterns = parse_dir_spec(spec_str)
        CODE_DIRS.append((project_root / path, patterns))

    TEST_DIRS = []
    for spec_str in config.get("test_dirs", []):
        path, patterns = parse_dir_spec(spec_str)
        TEST_DIRS.append((project_root / path, patterns))

    SKIP_DIRS = config.get("skip_dirs", DEFAULT_CONFIG["skip_dirs"])

    # Load validators
    UC_CHECKERS = load_validators(project_root, config)


# ---------------------------------------------------------------------------
# Link patterns
# ---------------------------------------------------------------------------

LINK_PATTERN = re.compile(r'\[\[(\w+):([^\]]+)\]\]')
SECTION_PATTERN = re.compile(r'^##\s+(.+?)\s*\{#([a-z0-9-]+)\}\s*$', re.MULTILINE)

# Anchors that are context/documentation, not actionable work
SKIP_ANCHORS = {
    "overview",
    "summary",
    "the-problem",
    "why-this-matters",
    "core-insight",
    "goals",
    "references",
    "related-docs",
}


# ---------------------------------------------------------------------------
# Iterators
# ---------------------------------------------------------------------------

def iter_all_ticket_dirs():
    """Iterate over all ticket directories from impl/."""
    if IMPL_TICKETS_DIR.exists():
        for ticket_dir in sorted(IMPL_TICKETS_DIR.iterdir()):
            if ticket_dir.is_dir() and not ticket_dir.name.startswith('.'):
                yield ticket_dir


def iter_spec_files():
    """Iterate over all spec files in docs/spec/."""
    if SPEC_DIR.exists():
        for spec_file in sorted(SPEC_DIR.glob("*.md")):
            yield spec_file


@dataclass
class SpecSection:
    """A section within a spec file."""
    id: str
    title: str
    spec_file: Path
    status: Optional[str] = None
    code_links: list = field(default_factory=list)
    test_links: list = field(default_factory=list)
    blocks: list = field(default_factory=list)


@dataclass
class Spec:
    """A parsed spec file."""
    id: str
    file: Path
    title: str
    sections: list = field(default_factory=list)


def should_skip_path(path: Path) -> bool:
    """Check if a path should be skipped."""
    path_str = str(path)
    return any(skip_dir in path_str for skip_dir in SKIP_DIRS) or path.name.startswith('_')


def iter_code_files():
    """Iterate over all code files."""
    for code_dir, patterns in CODE_DIRS:
        if code_dir.exists():
            for pattern in patterns:
                for f in code_dir.rglob(pattern):
                    if should_skip_path(f):
                        continue
                    yield f


def iter_test_files():
    """Iterate over all test files."""
    for test_dir, patterns in TEST_DIRS:
        if test_dir.exists():
            for pattern in patterns:
                for f in test_dir.rglob(pattern):
                    if should_skip_path(f):
                        continue
                    yield f


def resolve_link(link: str) -> Optional[Path]:
    """Resolve a typed link to a file path."""
    if ':' not in link:
        return None

    link_type, link_id = link.split(':', 1)

    # Handle section references
    if '#' in link_id:
        link_id = link_id.split('#')[0]

    if link_type == 'spec':
        for spec_dir in [SPEC_QUEUE, SPEC_COMPLETE]:
            candidate = spec_dir / f"{link_id.upper()}.md"
            if candidate.exists():
                return candidate
        return SPEC_QUEUE / f"{link_id.upper()}.md"
    elif link_type == 'code':
        path = link_id.lstrip('/')
        # Code links may include the project dir name as prefix
        # (e.g., "myproject/src/foo.py"), so resolve from parent
        return PROJECT_ROOT.parent / path
    elif link_type == 'adr':
        if DECISIONS_DIR.exists():
            matches = list(DECISIONS_DIR.glob(f"{link_id}-*.md"))
            return matches[0] if matches else None
        return None
    elif link_type == 'ticket':
        return SPEC_TICKETS_DIR / f"{link_id}.md"

    return None


def extract_links(text: str) -> list:
    """Extract all [[type:id]] links from text."""
    return LINK_PATTERN.findall(text)


def parse_section_metadata(content: str, section_start: int, next_section_start: int) -> dict:
    """Parse metadata table after a section heading."""
    section_content = content[section_start:next_section_start]

    lines = section_content.split('\n')
    table_lines = []
    in_table = False

    for line in lines:
        if '|' in line and ('---' in line or line.strip().startswith('|')):
            in_table = True
        if in_table:
            if '|' in line:
                table_lines.append(line)
            else:
                break

    if len(table_lines) < 3:
        return {}

    header = [h.strip() for h in table_lines[0].split('|')[1:-1]]
    data = [d.strip() for d in table_lines[2].split('|')[1:-1]]

    result = {}
    for h, d in zip(header, data):
        h_lower = h.lower().replace(' ', '_')
        result[h_lower] = d

    return result


def parse_spec_file_full(spec_path: Path) -> Optional[Spec]:
    """Parse a spec file with section metadata."""
    if not spec_path.exists():
        return None

    content = spec_path.read_text()
    fm = parse_frontmatter(spec_path)

    spec_id = fm.get('spec', spec_path.stem.lower().replace('_', '-'))
    title = get_title(spec_path)

    spec = Spec(id=spec_id, file=spec_path, title=title)

    sections = list(SECTION_PATTERN.finditer(content))

    for i, match in enumerate(sections):
        section_title = match.group(1)
        section_id = match.group(2)
        section_start = match.end()
        next_section_start = sections[i + 1].start() if i + 1 < len(sections) else len(content)

        metadata = parse_section_metadata(content, section_start, next_section_start)

        section = SpecSection(
            id=section_id,
            title=section_title,
            spec_file=spec_path,
            status=metadata.get('status'),
        )

        for key in ['code', 'tests', 'blocks']:
            value = metadata.get(key, '')
            links = extract_links(value)
            if key == 'code':
                section.code_links = [f"{t}:{i}" for t, i in links]
            elif key == 'tests':
                section.test_links = [f"{t}:{i}" for t, i in links]
            elif key == 'blocks':
                section.blocks = [f"{t}:{i}" for t, i in links]

        spec.sections.append(section)

    return spec


def load_all_specs() -> list:
    """Load all spec files from spec directories."""
    specs = []

    for spec_dir in [SPEC_QUEUE, SPEC_COMPLETE]:
        if not spec_dir.exists():
            continue
        for spec_file in spec_dir.glob("*.md"):
            spec = parse_spec_file_full(spec_file)
            if spec:
                specs.append(spec)

    return specs


def parse_frontmatter(path: Path) -> dict:
    """Parse YAML frontmatter from markdown file (simple parser, no yaml dep)."""
    try:
        content = path.read_text()
        if not content.startswith("---"):
            return {}

        end = content.find("\n---", 3)
        if end == -1:
            return {}

        frontmatter = content[4:end].strip()

        result = {}
        current_key = None
        current_list = None

        for line in frontmatter.split("\n"):
            if not line.strip():
                continue

            # List item
            if line.startswith("  - "):
                if current_key and current_list is not None:
                    current_list.append(line[4:].strip().strip('"'))
                continue

            # Key: value
            match = re.match(r'^(\w[\w-]*)\s*:\s*(.*)$', line)
            if match:
                key, value = match.groups()
                value = value.strip().strip('"')

                if current_key and current_list is not None:
                    result[current_key] = current_list

                if value == "" or value == "[]":
                    current_key = key
                    current_list = []
                else:
                    result[key] = value
                    current_key = None
                    current_list = None

        if current_key and current_list is not None:
            result[current_key] = current_list

        return result
    except Exception:
        return {}


def parse_uc_frontmatter(path: Path) -> dict:
    """Parse use case frontmatter including nested requires: block."""
    result = parse_frontmatter(path)

    try:
        content = path.read_text()
        if not content.startswith("---"):
            return result

        end = content.find("\n---", 3)
        if end == -1:
            return result

        fm_text = content[4:end]
    except Exception:
        return result

    requires = {}
    current_category = None

    in_requires = False
    for line in fm_text.split("\n"):
        if re.match(r'^requires:\s*$', line):
            in_requires = True
            continue

        if not in_requires:
            continue

        cat_match = re.match(r'^  (\w[\w-]*):\s*$', line)
        if cat_match:
            current_category = cat_match.group(1)
            requires[current_category] = []
            continue

        item_match = re.match(r'^    - (.+)$', line)
        if item_match and current_category is not None:
            requires[current_category].append(item_match.group(1).strip().strip('"'))
            continue

        if line.strip() and not line.startswith("  "):
            break

    if requires:
        result["requires"] = requires

    # Third pass: extract metrics: block
    metrics = {}
    current_category = None
    current_item = None
    in_metrics = False

    for line in fm_text.split("\n"):
        if re.match(r'^metrics:\s*$', line):
            in_metrics = True
            continue

        if not in_metrics:
            continue

        cat_match = re.match(r'^  (\w[\w-]*):\s*$', line)
        if cat_match:
            if current_item and current_category:
                metrics.setdefault(current_category, []).append(current_item)
            current_category = cat_match.group(1)
            current_item = None
            continue

        item_match = re.match(r'^    - (\w[\w-]*):\s*(.+)$', line)
        if item_match and current_category is not None:
            if current_item:
                metrics.setdefault(current_category, []).append(current_item)
            key, val = item_match.groups()
            current_item = {key: val.strip().strip('"')}
            continue

        kv_match = re.match(r'^      (\w[\w-]*):\s*(.+)$', line)
        if kv_match and current_item is not None:
            key, val = kv_match.groups()
            current_item[key] = val.strip().strip('"')
            continue

        if line.strip() and not line.startswith("  "):
            break

    if current_item and current_category:
        metrics.setdefault(current_category, []).append(current_item)

    if metrics:
        result["metrics"] = metrics

    return result


# ---------------------------------------------------------------------------
# Use case requirement validators
# ---------------------------------------------------------------------------

def _check_ticket_closed(name: str) -> bool:
    """Check if an impl ticket exists and has status: closed."""
    readme = IMPL_TICKETS_DIR / name / "README.md"
    if not readme.exists():
        return False
    fm = parse_frontmatter(readme)
    return fm.get("status") == "closed"


def validate_uc_requirements(requires: dict) -> dict:
    """Validate all requirements. Returns {category: [(item, bool), ...]}."""
    results = {}
    for category, items in requires.items():
        checker = UC_CHECKERS.get(category)
        if checker is None:
            results[category] = [(item, False) for item in items]
        else:
            checked = []
            for item in items:
                try:
                    result = checker(PROJECT_ROOT, item)
                except TypeError:
                    # Backwards compat: ticket_closed only takes name
                    result = checker(item)
                checked.append((item, result))
            results[category] = checked
    return results


def get_title(path: Path) -> str:
    """Extract first H1 title from markdown."""
    try:
        content = path.read_text()
        for line in content.split("\n"):
            if line.startswith("# "):
                return line[2:].strip()
        return path.stem
    except Exception:
        return path.stem


def list_specs() -> list[dict]:
    """List all ticket directories from impl/."""
    specs = []

    for ticket_dir in iter_all_ticket_dirs():
        readme = ticket_dir / "README.md"
        if not readme.exists():
            continue

        fm = parse_frontmatter(readme)

        counts = {"closed": 0, "deferred": 0, "in-progress": 0, "open": 0}
        tickets = []

        for ticket_path in ticket_dir.glob("*.md"):
            if ticket_path.name == "README.md":
                continue

            tfm = parse_frontmatter(ticket_path)
            status = tfm.get("status", "unknown")
            if status in counts:
                counts[status] += 1

            tickets.append({
                "name": ticket_path.stem,
                "status": status,
                "path": ticket_path,
                "frontmatter": tfm,
            })

        specs.append({
            "name": ticket_dir.name,
            "path": ticket_dir,
            "readme": readme,
            "frontmatter": fm,
            "tickets": tickets,
            "counts": counts,
        })

    return specs


def _match_frontmatter_filter(fm: dict, service: str = None, domain: str = None, milestone: str = None, demand: str = None) -> bool:
    """Check if a ticket dir's frontmatter matches the given filters."""
    if milestone and fm.get("milestone") != milestone:
        return False
    if service:
        services = fm.get("services", [])
        if not isinstance(services, list):
            services = [services]
        if service not in services:
            return False
    if domain:
        domains = fm.get("domains", [])
        if not isinstance(domains, list):
            domains = [domains]
        if domain not in domains:
            return False
    if demand:
        demands = fm.get("demand", [])
        if not isinstance(demands, list):
            demands = [demands]
        if demand not in demands:
            return False
    return True


def cmd_ls(args):
    """List specs or tickets."""
    specs = list_specs()

    if not specs:
        print(f"No specs found in {SPEC_TICKETS_DIR}")
        return 1

    status_filter = args.status
    service_filter = getattr(args, 'service', None)
    domain_filter = getattr(args, 'domain', None)
    milestone_filter = getattr(args, 'milestone', None)
    demand_filter = getattr(args, 'demand', None)
    has_meta_filter = service_filter or domain_filter or milestone_filter or demand_filter

    if has_meta_filter:
        specs = [s for s in specs if _match_frontmatter_filter(
            s["frontmatter"], service_filter, domain_filter, milestone_filter, demand_filter)]

    if args.spec:
        spec = next((s for s in specs if s["name"] == args.spec), None)
        if not spec:
            print(f"Spec not found: {args.spec}")
            print(f"Available: {', '.join(s['name'] for s in specs)}")
            return 1

        print(f"# {spec['name']}")
        if spec["frontmatter"].get("spec"):
            print(f"  Spec: {spec['frontmatter']['spec']}")
        print()

        tickets = spec["tickets"]
        if status_filter:
            tickets = [t for t in tickets if t["status"] == status_filter]

        if not tickets:
            print(f"  No tickets" + (f" with status={status_filter}" if status_filter else ""))
            return 0

        for status in ["open", "in-progress", "closed", "deferred"]:
            status_tickets = [t for t in tickets if t["status"] == status]
            if status_tickets:
                print(f"  {status.upper()}:")
                for t in sorted(status_tickets, key=lambda x: x["name"]):
                    print(f"    - {t['name']}")

        return 0

    # List all specs
    print(f"{'TICKET DIR':<32} {'CLOSED':>7} {'DEFERRED':>9} {'IN-PROG':>8} {'OPEN':>6}")
    print("-" * 64)

    total = {"closed": 0, "deferred": 0, "in-progress": 0, "open": 0}
    unscoped = []

    for spec in specs:
        c = spec["counts"]
        total_tasks = sum(c.values())

        if status_filter:
            if c.get(status_filter, 0) == 0:
                continue

        flag = ""
        if total_tasks == 0 and spec["frontmatter"].get("status") in ("open", "in-progress"):
            flag = "  <- unscoped"
            unscoped.append(spec["name"])

        print(f"{spec['name']:<32} {c['closed']:>7} {c.get('deferred', 0):>9} {c['in-progress']:>8} {c['open']:>6}{flag}")

        for k in total:
            total[k] += c.get(k, 0)

    print("-" * 64)
    print(f"{'TOTAL':<32} {total['closed']:>7} {total.get('deferred', 0):>9} {total['in-progress']:>8} {total['open']:>6}")

    if unscoped:
        print(f"\n  {len(unscoped)} unscoped ticket(s) need task breakdown: {', '.join(unscoped)}")

    return 0


def cmd_show(args):
    """Show ticket details."""
    parts = args.ticket.split("/")
    if len(parts) != 2:
        print(f"Invalid ticket format: {args.ticket}")
        print("Expected: <spec>/<ticket>")
        return 1

    spec_name, ticket_name = parts
    ticket_path = SPEC_TICKETS_DIR / spec_name / f"{ticket_name}.md"

    if not ticket_path.exists():
        print(f"Ticket not found: {ticket_path}")
        return 1

    fm = parse_frontmatter(ticket_path)
    title = get_title(ticket_path)

    print(f"# {title}")
    print(f"  ID: {fm.get('id', 'unknown')}")
    print(f"  Status: {fm.get('status', 'unknown')}")
    print(f"  Priority: {fm.get('priority', '-')}")

    if fm.get("spec"):
        print(f"  Spec: {fm['spec']}")

    code = fm.get("code")
    if code:
        print(f"  Code:")
        if isinstance(code, list):
            for c in code:
                print(f"    - {c}")
        else:
            print(f"    - {code}")

    tests = fm.get("tests")
    if tests:
        print(f"  Tests:")
        if isinstance(tests, list):
            for t in tests:
                print(f"    - {t}")
        else:
            print(f"    - {tests}")

    deps = fm.get("deps")
    if deps:
        print(f"  Deps:")
        if isinstance(deps, list):
            for d in deps:
                print(f"    - {d}")
        else:
            print(f"    - {deps}")

    print()
    print(f"  Path: {ticket_path}")

    return 0


def cmd_summary(args):
    """Show overall summary."""
    specs = list_specs()

    total = {"closed": 0, "deferred": 0, "in-progress": 0, "open": 0}
    ticket_count = 0

    for spec in specs:
        for k in total:
            total[k] += spec["counts"].get(k, 0)
        ticket_count += len(spec["tickets"])

    project_name = PROJECT_ROOT.name
    print(f"Traceability Summary ({project_name})")
    print(f"================================")
    print(f"  Specs with tickets: {len(specs)}")
    print(f"  Total tickets:      {ticket_count}")
    print()
    print(f"  Status breakdown:")
    print(f"    Closed:      {total['closed']:>4}")
    print(f"    Deferred:    {total['deferred']:>4}")
    print(f"    In Progress: {total['in-progress']:>4}")
    print(f"    Open:        {total['open']:>4}")

    if ticket_count > 0:
        pct = total["closed"] / ticket_count * 100
        print()
        print(f"  Completion: {pct:.1f}%")

    return 0


def cmd_dashboard(args):
    """Show dashboard with per-spec status."""
    specs = list_specs()

    if not specs:
        print(f"No specs found in {SPEC_TICKETS_DIR}")
        return 1

    total_closed = 0
    total_open = 0
    total_tickets = 0

    done = []
    almost = []
    progress = []
    early = []

    for spec in specs:
        closed = spec["counts"].get("closed", 0)
        deferred_count = spec["counts"].get("deferred", 0)
        in_prog = spec["counts"].get("in-progress", 0)
        open_count = spec["counts"].get("open", 0)
        total = closed + deferred_count + in_prog + open_count

        total_closed += closed
        total_open += open_count + in_prog
        total_tickets += total

        spec_info = {
            "name": spec["name"],
            "closed": closed,
            "open": open_count + in_prog,
            "total": total,
            "pct": (closed / total * 100) if total > 0 else 0,
        }

        if open_count + in_prog == 0:
            done.append(spec_info)
        elif open_count + in_prog <= 3 and spec_info["pct"] >= 50:
            almost.append(spec_info)
        elif closed > 0:
            progress.append(spec_info)
        else:
            early.append(spec_info)

    done.sort(key=lambda x: -x["pct"])
    almost.sort(key=lambda x: -x["pct"])
    progress.sort(key=lambda x: -x["pct"])
    early.sort(key=lambda x: -x["pct"])

    completion_pct = (total_closed / total_tickets * 100) if total_tickets > 0 else 0
    print("=" * 60)
    print(f"TRACEABILITY DASHBOARD")
    print("=" * 60)
    print(f"Overall: {total_closed}/{total_tickets} tickets ({completion_pct:.0f}%)")
    print()

    def print_section(title, items, icon):
        if not items:
            return
        print(f"{title}:")
        for s in items:
            bar_len = int(s["pct"] / 5)
            bar = "#" * bar_len + "." * (20 - bar_len)
            print(f"  {icon} {s['name']:<25} {bar} {s['closed']:>2}/{s['total']:<2} ({s['pct']:.0f}%)")
        print()

    print_section("DONE (all tickets closed)", done, "[x]")
    print_section("ALMOST (1-3 tickets left)", almost, "[~]")
    print_section("IN PROGRESS", progress, "[ ]")
    print_section("EARLY (mostly open)", early, "[ ]")

    print("SUGGESTED ACTIONS:")
    if almost:
        top = almost[0]
        print(f"  - Close out {top['name']} ({top['open']} tickets left)")
    if done:
        print(f"  - Run: specgraph complete <spec> for {len(done)} done specs")
    if early:
        top_early = early[0]
        print(f"  - Triage {top_early['name']} ({top_early['open']} open tickets)")

    return 0


def cmd_open(args):
    """List all open tickets, including unscoped ticket dirs."""
    specs = list_specs()

    print("OPEN TICKETS")
    print("=" * 60)

    count = 0
    for spec in specs:
        open_tickets = [t for t in spec["tickets"] if t["status"] == "open"]
        if open_tickets:
            print(f"\n{spec['name']}:")
            for t in sorted(open_tickets, key=lambda x: x["name"]):
                print(f"  - {t['name']}")
                count += 1

    unscoped = []
    for spec in specs:
        total_tasks = sum(spec["counts"].values())
        if total_tasks == 0 and spec["frontmatter"].get("status") in ("open", "in-progress"):
            unscoped.append(spec)

    if unscoped:
        print(f"\nUNSCOPED (need task breakdown):")
        for spec in unscoped:
            title = spec["frontmatter"].get("title", spec["name"])
            priority = spec["frontmatter"].get("priority", "?")
            print(f"  - {spec['name']} (P{priority}): {title}")
        count += len(unscoped)

    print()
    print(f"Total: {count} open tickets ({len(unscoped)} unscoped)")

    return 0


def cmd_gaps(args):
    """Show specs in queue without tickets."""
    specs = list_specs()
    spec_names = {s["name"] for s in specs}

    queue_dir = SPEC_DIR
    if not queue_dir.exists():
        print(f"Specs dir not found: {queue_dir}")
        return 1

    gaps = []
    for spec_file in queue_dir.glob("*.md"):
        name = spec_file.stem.lower().replace("_", "-")
        if name not in spec_names:
            gaps.append(spec_file.stem)

    if not gaps:
        print("No gaps - all specs have tickets!")
        return 0

    print(f"SPECS WITHOUT TICKETS ({len(gaps)}):")
    print()
    for g in sorted(gaps):
        print(f"  - {g}")

    return 0


def find_spec_file(spec_name: str) -> Optional[Path]:
    """Find spec file by name."""
    filename = spec_name.upper().replace("-", "_") + ".md"

    for dir in [SPEC_QUEUE, SPEC_COMPLETE]:
        candidate = dir / filename
        if candidate.exists():
            return candidate

    for dir in [SPEC_QUEUE, SPEC_COMPLETE]:
        candidate = dir / (spec_name + ".md")
        if candidate.exists():
            return candidate

    return None


def parse_spec_sections(spec_path: Path) -> list[dict]:
    """Parse spec file and extract sections with {#anchor} tags."""
    content = spec_path.read_text()
    sections = []

    pattern = r'^(#{1,3})\s+(?:\d+\.\s+)?(.+?)\s*\{#([\w-]+)\}\s*$'

    for match in re.finditer(pattern, content, re.MULTILINE):
        level = len(match.group(1))
        title = match.group(2).strip()
        anchor = match.group(3)

        sections.append({
            "level": level,
            "title": title,
            "anchor": anchor,
        })

    return sections


def create_ticket_file(spec_name: str, section: dict, spec_id: str) -> Path:
    """Create a single ticket file for a section."""
    ticket_dir = SPEC_TICKETS_DIR / spec_name
    ticket_path = ticket_dir / f"{section['anchor']}.md"

    content = f"""---
id: {spec_name}/{section['anchor']}
status: open
priority: 2
spec: "[[spec:{spec_id}#{section['anchor']}]]"
code: []
tests: []
deps: []
created: {__import__('datetime').date.today().isoformat()}
---

# {section['title']}

> Section from [[spec:{spec_id}#{section['anchor']}]]

## Status

Not started.

## Acceptance Criteria

- [ ] TODO: Define acceptance criteria
"""

    ticket_path.write_text(content)
    return ticket_path


def create_readme_file(spec_name: str, spec_id: str, sections: list[dict], spec_title: str) -> Path:
    """Create README.md for the spec ticket directory."""
    ticket_dir = SPEC_TICKETS_DIR / spec_name
    readme_path = ticket_dir / "README.md"

    section_list = "\n".join(f"- [ ] {spec_name}/{s['anchor']}" for s in sections)

    content = f"""---
id: {spec_name}
status: open
priority: 2
spec: "[[spec:{spec_id}]]"
created: {__import__('datetime').date.today().isoformat()}
---

# {spec_title}

Tickets for [[spec:{spec_id}]].

## Sections

{section_list}
"""

    readme_path.write_text(content)
    return readme_path


def cmd_close(args):
    """Close a ticket (mark as implemented) and optionally link code/tests."""
    parts = args.ticket.split("/")
    if len(parts) != 2:
        print(f"Invalid ticket format: {args.ticket}")
        print("Expected: <spec>/<ticket>")
        return 1

    spec_name, ticket_name = parts
    ticket_path = SPEC_TICKETS_DIR / spec_name / f"{ticket_name}.md"

    if not ticket_path.exists():
        print(f"Ticket not found: {ticket_path}")
        return 1

    content = ticket_path.read_text()

    # Update status
    content = re.sub(r'^status:\s*\w+', 'status: closed', content, flags=re.MULTILINE)

    # Add code links if provided
    if args.code:
        code_yaml = "\n".join(f'  - "{c}"' for c in args.code)
        new_content = re.sub(r'^code:\s*\[\]', f'code:\n{code_yaml}', content, flags=re.MULTILINE)
        if new_content == content:
            lines = content.split('\n')
            new_lines = []
            in_code_section = False
            code_added = False
            for i, line in enumerate(lines):
                new_lines.append(line)
                if line.startswith('code:'):
                    in_code_section = True
                elif in_code_section and not line.startswith('  - '):
                    if not code_added:
                        new_lines.pop()
                        for c in args.code:
                            new_lines.append(f'  - "{c}"')
                        new_lines.append(line)
                        code_added = True
                    in_code_section = False
            content = '\n'.join(new_lines)
        else:
            content = new_content

    # Add test links if provided
    if args.test:
        test_yaml = "\n".join(f'  - "{t}"' for t in args.test)
        new_content = re.sub(r'^tests:\s*\[\]', f'tests:\n{test_yaml}', content, flags=re.MULTILINE)
        if new_content == content:
            lines = content.split('\n')
            new_lines = []
            in_tests_section = False
            tests_added = False
            for i, line in enumerate(lines):
                new_lines.append(line)
                if line.startswith('tests:'):
                    in_tests_section = True
                elif in_tests_section and not line.startswith('  - '):
                    if not tests_added:
                        new_lines.pop()
                        for t in args.test:
                            new_lines.append(f'  - "{t}"')
                        new_lines.append(line)
                        tests_added = True
                    in_tests_section = False
            content = '\n'.join(new_lines)
        else:
            content = new_content

    ticket_path.write_text(content)
    print(f"Closed: {args.ticket}")
    if args.code:
        for c in args.code:
            print(f"  code: {c}")
    if args.test:
        for t in args.test:
            print(f"  test: {t}")

    return 0


def cmd_defer(args):
    """Defer a ticket (mark as future/someday) with an optional reason."""
    parts = args.ticket.split("/")
    if len(parts) != 2:
        print(f"Invalid ticket format: {args.ticket}")
        print("Expected: <spec>/<ticket>")
        return 1

    spec_name, ticket_name = parts
    ticket_path = SPEC_TICKETS_DIR / spec_name / f"{ticket_name}.md"

    if not ticket_path.exists():
        print(f"Ticket not found: {ticket_path}")
        return 1

    content = ticket_path.read_text()

    content = re.sub(r'^status:\s*[\w-]+', 'status: deferred', content, flags=re.MULTILINE)

    if args.reason:
        reason_text = f"\n> **Deferred:** {args.reason}\n"
        content = re.sub(r'(^# .+$)', r'\1' + reason_text, content, count=1, flags=re.MULTILINE)

    ticket_path.write_text(content)
    print(f"Deferred: {args.ticket}")
    if args.reason:
        print(f"  reason: {args.reason}")

    return 0


def cmd_deferred(args):
    """List all deferred tickets."""
    specs = list_specs()

    print("DEFERRED TICKETS")
    print("=" * 60)

    count = 0
    for spec in specs:
        deferred_tickets = [t for t in spec["tickets"] if t["status"] == "deferred"]
        if deferred_tickets:
            print(f"\n{spec['name']}:")
            for t in sorted(deferred_tickets, key=lambda x: x["name"]):
                print(f"  - {t['name']}")
                count += 1

    print()
    print(f"Total: {count} deferred tickets")

    return 0


def _get_linked_paths(specs: list) -> tuple[set, set]:
    """Extract all code/test paths linked from tickets."""
    linked_code = set()
    linked_tests = set()

    for spec in specs:
        for ticket in spec["tickets"]:
            code = ticket["frontmatter"].get("code", [])
            tests = ticket["frontmatter"].get("tests", [])
            if code and code != []:
                if isinstance(code, list):
                    linked_code.update(code)
                else:
                    linked_code.add(code)
            if tests and tests != []:
                if isinstance(tests, list):
                    linked_tests.update(tests)
                else:
                    linked_tests.add(tests)

    return linked_code, linked_tests


def _get_orphan_analysis(linked_code: set, linked_tests: set) -> tuple[set, set, dict]:
    """Analyze which files are orphans (not linked from tickets)."""
    all_code_files = set()

    # Collect all code files using configured patterns
    for code_dir, patterns in CODE_DIRS:
        if not code_dir.exists():
            continue
        for pattern in patterns:
            for f in code_dir.rglob(pattern):
                if should_skip_path(f):
                    continue
                path_str = str(f.relative_to(PROJECT_ROOT))
                all_code_files.add(path_str)

    # Check which files are covered by linked paths
    covered_files = set()
    for code_file in all_code_files:
        for linked in linked_code | linked_tests:
            linked_norm = linked.rstrip("/")
            if linked_norm.startswith("[["):
                continue
            if code_file.startswith(linked_norm) or linked_norm in code_file:
                covered_files.add(code_file)
                break

    orphan_files = all_code_files - covered_files

    orphan_dirs = {}
    for f in orphan_files:
        dir_path = str(Path(f).parent)
        orphan_dirs[dir_path] = orphan_dirs.get(dir_path, 0) + 1

    return all_code_files, covered_files, orphan_dirs


def cmd_audit(args):
    """Audit code coverage and find orphans."""
    specs = list_specs()

    total_closed = 0
    with_code = 0
    with_tests = 0

    for spec in specs:
        for ticket in spec["tickets"]:
            if ticket["frontmatter"].get("status") == "closed":
                total_closed += 1
                code = ticket["frontmatter"].get("code", [])
                tests = ticket["frontmatter"].get("tests", [])
                if code and code != []:
                    with_code += 1
                if tests and tests != []:
                    with_tests += 1

    linked_code, linked_tests = _get_linked_paths(specs)
    all_code_files, covered_files, orphan_dirs = _get_orphan_analysis(linked_code, linked_tests)
    orphan_files = all_code_files - covered_files

    print("=" * 60)
    print("TRACEABILITY AUDIT REPORT")
    print("=" * 60)
    print()

    total_tickets = sum(len(s["tickets"]) for s in specs)
    print(f"TICKET COVERAGE:")
    print(f"  Total tickets:     {total_tickets}")
    pct_closed = (total_closed * 100 // total_tickets) if total_tickets > 0 else 0
    print(f"  Closed:            {total_closed} ({pct_closed}%)")
    print(f"  With code links:   {with_code} ({with_code * 100 // max(total_closed, 1)}% of closed)")
    print(f"  With test links:   {with_tests} ({with_tests * 100 // max(total_closed, 1)}% of closed)")
    print()

    print(f"LINKED PATHS ({len(linked_code)} code, {len(linked_tests)} test):")
    if args.verbose:
        for path in sorted(p for p in linked_code if not p.startswith("[[")):
            print(f"  code: {path}")
        for path in sorted(p for p in linked_tests if not p.startswith("[[")):
            print(f"  test: {path}")
    else:
        print(f"  (use --verbose to list all paths)")
    print()

    print(f"ORPHAN ANALYSIS:")
    print(f"  Total code files:  {len(all_code_files)}")
    print(f"  Covered by links:  {len(covered_files)} ({len(covered_files) * 100 // max(len(all_code_files), 1)}%)")
    print(f"  Orphan files:      {len(orphan_files)} ({len(orphan_files) * 100 // max(len(all_code_files), 1)}%)")
    print()

    if args.verbose and orphan_dirs:
        print("  Top orphan directories:")
        for dir_path, count in sorted(orphan_dirs.items(), key=lambda x: -x[1])[:20]:
            print(f"    {dir_path}: {count} files")

    return 0


def cmd_orphans(args):
    """List orphan code directories not linked from any ticket."""
    specs = list_specs()
    linked_code, linked_tests = _get_linked_paths(specs)
    all_code_files, covered_files, orphan_dirs = _get_orphan_analysis(linked_code, linked_tests)

    if not orphan_dirs:
        print("No orphan directories found - all code is linked!")
        return 0

    sorted_dirs = sorted(orphan_dirs.items(), key=lambda x: -x[1])

    min_files = args.min_files or 1
    sorted_dirs = [(d, c) for d, c in sorted_dirs if c >= min_files]

    print(f"ORPHAN DIRECTORIES (not linked from any ticket)")
    print(f"{'='*60}")
    print()

    by_module = {}
    for dir_path, count in sorted_dirs:
        parts = dir_path.split("/")
        if len(parts) >= 2:
            module = "/".join(parts[:2])
        else:
            module = parts[0]
        if module not in by_module:
            by_module[module] = []
        by_module[module].append((dir_path, count))

    for module in sorted(by_module.keys()):
        dirs = by_module[module]
        total = sum(c for _, c in dirs)
        print(f"{module}/ ({total} files)")
        for dir_path, count in dirs:
            rel_path = dir_path[len(module)+1:] if dir_path.startswith(module + "/") else dir_path
            if rel_path:
                print(f"  {rel_path}: {count}")
        print()

    total_orphan = sum(orphan_dirs.values())
    print(f"Total: {total_orphan} orphan files in {len(orphan_dirs)} directories")
    print()
    print("To link orphan code to specs:")
    print("  1. Add {#anchor} to spec section")
    print("  2. Run: specgraph scaffold <spec>")
    print("  3. Run: specgraph close <spec>/<ticket> -c <path>")

    return 0


def _normalize_name(name: str) -> set:
    """Convert a name to searchable keywords."""
    normalized = name.lower().replace("-", " ").replace("_", " ").replace("/", " ")
    words = set(normalized.split())
    words -= {"src", "tests", "test", "the", "a", "an", "of", "to", "in"}
    return words


def _get_path_segments(path: str) -> list:
    """Get meaningful path segments from a path."""
    segments = path.lower().replace("_", "-").split("/")
    skip = {"src", "tests", "test"}
    return [s for s in segments if s and s not in skip]


def _score_match(orphan_path: str, ticket_spec: str, ticket_name: str) -> tuple:
    """Score how well an orphan path matches a ticket."""
    orphan_segments = _get_path_segments(orphan_path)
    spec_normalized = ticket_spec.lower().replace("_", "-")
    ticket_normalized = ticket_name.lower().replace("_", "-")

    score = 0
    reasons = []

    for seg in orphan_segments:
        if seg == spec_normalized or spec_normalized.startswith(seg) or seg.startswith(spec_normalized):
            score += 10
            reasons.append(f"spec={spec_normalized}")
            break

    for seg in orphan_segments:
        if seg == ticket_normalized or ticket_normalized in seg or seg in ticket_normalized:
            score += 5
            reasons.append(f"ticket={ticket_normalized}")
            break

    orphan_words = _normalize_name(orphan_path)
    ticket_words = _normalize_name(ticket_spec) | _normalize_name(ticket_name)
    overlap = orphan_words & ticket_words
    if overlap:
        score += len(overlap)
        if not reasons:
            reasons.append(f"keywords={','.join(sorted(overlap))}")

    reason = "; ".join(reasons) if reasons else "none"
    return (score, reason)


def cmd_match(args):
    """Suggest matches between orphan directories and open tickets."""
    specs = list_specs()
    linked_code, linked_tests = _get_linked_paths(specs)
    all_code_files, covered_files, orphan_dirs = _get_orphan_analysis(linked_code, linked_tests)

    open_tickets = []
    for spec in specs:
        for ticket in spec["tickets"]:
            if ticket["status"] == "open":
                open_tickets.append({
                    "id": f"{spec['name']}/{ticket['name']}",
                    "spec": spec["name"],
                    "name": ticket["name"],
                    "path": ticket["path"],
                })

    if not orphan_dirs:
        print("No orphan directories to match!")
        return 0

    if not open_tickets:
        print("No open tickets to match against!")
        return 0

    sorted_orphans = sorted(orphan_dirs.items(), key=lambda x: -x[1])

    min_files = args.min_files or 3
    sorted_orphans = [(d, c) for d, c in sorted_orphans if c >= min_files]

    print(f"SUGGESTED MATCHES (orphan dirs with {min_files}+ files)")
    print("=" * 70)
    print()

    matched = []
    unmatched = []

    for orphan_path, file_count in sorted_orphans:
        scores = []
        for ticket in open_tickets:
            score, reason = _score_match(orphan_path, ticket["spec"], ticket["name"])
            if score > 0:
                scores.append((score, reason, ticket))

        scores.sort(key=lambda x: -x[0])

        if scores:
            best_score, best_reason, best_ticket = scores[0]
            matched.append((orphan_path, file_count, best_ticket, best_score, best_reason))
        else:
            unmatched.append((orphan_path, file_count))

    if matched:
        print("MATCHES FOUND:")
        print()
        for orphan_path, file_count, ticket, score, reason in matched:
            print(f"  {orphan_path} ({file_count} files)")
            print(f"    -> {ticket['id']} ({reason})")
            print(f"    specgraph close {ticket['id']} -c {orphan_path}")
            print()

    if unmatched:
        print("NO MATCHES (may need new spec sections):")
        print()
        for orphan_path, file_count in unmatched:
            print(f"  {orphan_path} ({file_count} files)")
        print()

    print(f"Summary: {len(matched)} matched, {len(unmatched)} unmatched")

    if args.script:
        print()
        if args.group:
            by_ticket = {}
            for orphan_path, file_count, ticket, score, reason in matched:
                tid = ticket['id']
                if tid not in by_ticket:
                    by_ticket[tid] = []
                by_ticket[tid].append(orphan_path)

            print("# Consolidated by ticket:")
            for tid, paths in sorted(by_ticket.items()):
                code_flags = " ".join(f"-c {p}" for p in paths)
                print(f"specgraph close {tid} {code_flags}")
        else:
            print("# Copy/paste to close matched tickets:")
            for orphan_path, file_count, ticket, score, reason in matched:
                print(f"specgraph close {ticket['id']} -c {orphan_path}")

    return 0


def cmd_prune(args):
    """Remove non-actionable tickets (overview, summary, etc.)."""
    specs = list_specs()

    if not specs:
        print(f"No specs found in {SPEC_TICKETS_DIR}")
        return 1

    to_delete = []

    for spec in specs:
        for ticket in spec["tickets"]:
            if ticket["name"] in SKIP_ANCHORS:
                to_delete.append(ticket["path"])

    if not to_delete:
        print("No non-actionable tickets found.")
        return 0

    print(f"Found {len(to_delete)} non-actionable tickets:")
    for path in to_delete:
        print(f"  - {path.parent.name}/{path.stem}")

    if args.dry_run:
        print()
        print("Dry run - no files deleted. Run without --dry-run to delete.")
        return 0

    print()
    for path in to_delete:
        path.unlink()
        print(f"  Deleted: {path.parent.name}/{path.stem}")

    print()
    print(f"Deleted {len(to_delete)} tickets.")

    return 0


def cmd_status(args):
    """Show status for specs and their sections."""
    specs = load_all_specs()

    if not specs:
        print(f"No specs found in {SPEC_QUEUE} or {SPEC_COMPLETE}")
        return 1

    ref = args.spec if hasattr(args, 'spec') and args.spec else None

    for spec in specs:
        if ref and not spec.id.startswith(ref) and ref not in spec.id:
            continue

        print(f"\n{spec.title} (spec:{spec.id})")
        print("=" * 60)

        if not spec.sections:
            print("  (no sections with {#anchor} found)")
            continue

        for section in spec.sections:
            status = section.status or 'unknown'
            status_icon = {
                'verified': '+',
                'implemented': '~',
                'in-progress': 'o',
                'specified': '.',
                'not-started': ' ',
                'n/a': '-',
            }.get(status, '?')

            print(f"  {status_icon} {section.id}: {status}")

            if args.verbose:
                if section.code_links:
                    print(f"      Code: {', '.join(section.code_links)}")
                if section.test_links:
                    print(f"      Tests: {', '.join(section.test_links)}")
                if section.blocks:
                    print(f"      Blocks: {', '.join(section.blocks)}")

    return 0


def cmd_validate(args):
    """Validate all links resolve to real files."""
    specs = load_all_specs()

    broken = []
    for spec in specs:
        for section in spec.sections:
            for link in section.code_links + section.test_links + section.blocks:
                resolved = resolve_link(link)
                if resolved and not resolved.exists():
                    broken.append((f"spec:{spec.id}#{section.id}", link, resolved))

    if not broken:
        print("All links resolve")
        return 0

    print(f"Found {len(broken)} broken links:\n")
    for location, link, resolved in broken:
        print(f"  {location}: {link} -> {resolved}")

    return 1


def _normalize_path_for_match(path: str) -> str:
    """Normalize a path for matching."""
    path = path.strip().lstrip("/")
    if path.startswith("[["):
        return ""
    return path


def _path_matches(linked_path: str, query_path: str) -> bool:
    """Check if a linked path covers the query path."""
    linked = _normalize_path_for_match(linked_path)
    if not linked:
        return False

    query = query_path.strip().lstrip("/")

    if query.startswith(linked) or linked.startswith(query):
        return True

    if linked.endswith("/"):
        return query.startswith(linked)

    return False


def cmd_trace(args):
    """Reverse lookup: given a code file/directory, show what spec/ticket covers it."""
    query_path = args.path

    specs = list_specs()

    if not specs:
        print(f"No specs found in {SPEC_TICKETS_DIR}")
        return 1

    matches = []

    for spec in specs:
        for ticket in spec["tickets"]:
            fm = ticket["frontmatter"]
            code_links = fm.get("code", [])
            test_links = fm.get("tests", [])

            if not isinstance(code_links, list):
                code_links = [code_links] if code_links else []
            if not isinstance(test_links, list):
                test_links = [test_links] if test_links else []

            matched_links = []

            for link in code_links:
                if _path_matches(link, query_path):
                    matched_links.append(("code", link))

            for link in test_links:
                if _path_matches(link, query_path):
                    matched_links.append(("test", link))

            if matched_links:
                matches.append({
                    "spec": spec["name"],
                    "ticket": ticket["name"],
                    "status": ticket["status"],
                    "links": matched_links,
                    "spec_link": fm.get("spec", ""),
                })

    if not matches:
        print(f"No coverage found for: {query_path}")
        print()
        print("This path is not linked from any ticket.")
        print("Use 'specgraph orphans' to see all unlinked code.")
        return 1

    print(f"Coverage for: {query_path}")
    print("=" * 60)
    print()

    by_spec = {}
    for m in matches:
        spec = m["spec"]
        if spec not in by_spec:
            by_spec[spec] = []
        by_spec[spec].append(m)

    for spec_name, spec_matches in sorted(by_spec.items()):
        print(f"{spec_name}/")
        for m in spec_matches:
            status_icon = {"closed": "[closed]", "open": "[open]", "in-progress": "[in-progress]", "deferred": "[deferred]"}.get(m["status"], "[?]")
            print(f"  {m['ticket']} {status_icon}")
            for link_type, link_path in m["links"]:
                print(f"    {link_type}: {link_path}")
        print()

    total = len(matches)
    closed = sum(1 for m in matches if m["status"] == "closed")
    print(f"Total: {total} ticket(s) ({closed} closed)")

    return 0


def cmd_graph(args):
    """Show full graph for a spec, or reverse graph for a path."""
    if args.reverse:
        return _cmd_graph_reverse(args)

    spec_name = args.spec
    if not spec_name:
        print("Specify a spec name or use --reverse <path>")
        return 1

    specs = list_specs()

    spec = next((s for s in specs if s["name"] == spec_name or s["name"].replace("-", "_") == spec_name.replace("-", "_")), None)
    if not spec:
        print(f"Spec not found: {spec_name}")
        print(f"Available: {', '.join(s['name'] for s in specs)}")
        return 1

    print(f"{spec['name']} (spec)")

    tickets = spec["tickets"]
    for i, ticket in enumerate(sorted(tickets, key=lambda t: t["name"])):
        is_last_ticket = (i == len(tickets) - 1)
        branch = "`-- " if is_last_ticket else "|-- "
        child_prefix = "    " if is_last_ticket else "|   "

        status = ticket["status"]
        status_str = f"[{status}]"
        print(f"{branch}{ticket['name']} {status_str}")

        fm = ticket["frontmatter"]
        code_links = fm.get("code", [])
        test_links = fm.get("tests", [])

        if not isinstance(code_links, list):
            code_links = [code_links] if code_links else []
        if not isinstance(test_links, list):
            test_links = [test_links] if test_links else []

        code_links = [c for c in code_links if c and not c.startswith("[[")]
        test_links = [t for t in test_links if t and not t.startswith("[[")]

        total_links = len(code_links) + len(test_links)
        link_idx = 0

        for code in code_links:
            link_idx += 1
            is_last_link = (link_idx == total_links)
            link_branch = "`-- " if is_last_link else "|-- "
            print(f"{child_prefix}{link_branch}code: {code}")

        for test in test_links:
            link_idx += 1
            is_last_link = (link_idx == total_links)
            link_branch = "`-- " if is_last_link else "|-- "
            print(f"{child_prefix}{link_branch}tests: {test}")

    print()
    total = len(tickets)
    closed = sum(1 for t in tickets if t["status"] == "closed")
    deferred = sum(1 for t in tickets if t["status"] == "deferred")
    open_count = sum(1 for t in tickets if t["status"] == "open")
    in_progress = sum(1 for t in tickets if t["status"] == "in-progress")

    parts = [f"{closed} closed"]
    if deferred:
        parts.append(f"{deferred} deferred")
    parts.extend([f"{in_progress} in-progress", f"{open_count} open"])
    print(f"Summary: {total} tickets ({', '.join(parts)})")

    return 0


def _cmd_graph_reverse(args):
    """Reverse graph: find all specs that touch a directory."""
    query_path = args.reverse

    specs = list_specs()

    if not specs:
        print(f"No specs found in {SPEC_TICKETS_DIR}")
        return 1

    by_spec = {}

    for spec in specs:
        matching_tickets = []

        for ticket in spec["tickets"]:
            fm = ticket["frontmatter"]
            code_links = fm.get("code", [])
            test_links = fm.get("tests", [])

            if not isinstance(code_links, list):
                code_links = [code_links] if code_links else []
            if not isinstance(test_links, list):
                test_links = [test_links] if test_links else []

            has_match = False
            for link in code_links + test_links:
                if _path_matches(link, query_path):
                    has_match = True
                    break

            if has_match:
                matching_tickets.append(ticket)

        if matching_tickets:
            by_spec[spec["name"]] = {
                "tickets": matching_tickets,
                "spec": spec,
            }

    if not by_spec:
        print(f"No specs touch: {query_path}")
        return 1

    print(f"Specs touching: {query_path}")
    print("=" * 60)
    print()

    for spec_name in sorted(by_spec.keys()):
        data = by_spec[spec_name]
        tickets = data["tickets"]
        closed = sum(1 for t in tickets if t["status"] == "closed")
        total = len(tickets)

        print(f"{spec_name}: {total} ticket(s) ({closed} closed)")
        for t in sorted(tickets, key=lambda x: x["name"]):
            status_icon = {"closed": "[closed]", "open": "[open]", "in-progress": "[in-progress]", "deferred": "[deferred]"}.get(t["status"], "[?]")
            print(f"  - {t['name']} {status_icon}")
        print()

    print(f"Total: {len(by_spec)} spec(s)")

    return 0


def cmd_related(args):
    """Show specs that link to this spec, and what this spec links to."""
    target_spec = args.spec.lower().replace("_", "-")

    all_spec_files = []
    for spec_dir in [SPEC_QUEUE, SPEC_COMPLETE]:
        if spec_dir.exists():
            all_spec_files.extend(spec_dir.glob("*.md"))

    incoming = []
    outgoing = []

    target_file = None

    for spec_file in all_spec_files:
        spec_id = spec_file.stem.lower().replace("_", "-")
        content = spec_file.read_text()

        if spec_id == target_spec:
            target_file = spec_file
            links = LINK_PATTERN.findall(content)
            for link_type, link_id in links:
                if link_type == "spec":
                    link_id = link_id.split("#")[0].lower().replace("_", "-")
                    if link_id != target_spec:
                        outgoing.append(link_id)
        else:
            links = LINK_PATTERN.findall(content)
            for link_type, link_id in links:
                if link_type == "spec":
                    link_id = link_id.split("#")[0].lower().replace("_", "-")
                    if link_id == target_spec:
                        incoming.append(spec_id)
                        break

    if not target_file:
        print(f"Spec not found: {args.spec}")
        print(f"Looked in: {SPEC_QUEUE}, {SPEC_COMPLETE}")
        return 1

    print(f"Related specs for: {target_spec}")
    print("=" * 60)
    print()

    incoming = sorted(set(incoming))
    outgoing = sorted(set(outgoing))

    if incoming:
        print(f"INCOMING (specs that reference {target_spec}):")
        for spec_id in incoming:
            print(f"  <- {spec_id}")
        print()
    else:
        print(f"INCOMING: (none)")
        print()

    if outgoing:
        print(f"OUTGOING (specs that {target_spec} references):")
        for spec_id in outgoing:
            print(f"  -> {spec_id}")
        print()
    else:
        print(f"OUTGOING: (none)")
        print()

    print(f"Summary: {len(incoming)} incoming, {len(outgoing)} outgoing")

    return 0


def cmd_coverage(args):
    """Show which specs have impl ticket coverage."""
    spec_files = list(iter_spec_files())
    if not spec_files:
        print(f"No specs found in {SPEC_DIR}")
        return 1

    spec_coverage = {f.stem.lower(): [] for f in spec_files}

    specs = list_specs()
    for spec in specs:
        for ticket in spec["tickets"]:
            links = ticket["frontmatter"].get("links", [])
            if isinstance(links, list):
                for link in links:
                    if isinstance(link, str) and "docs/spec/" in link:
                        match = re.search(r'docs/spec/([A-Z_]+)\.md', link)
                        if match:
                            spec_name = match.group(1).lower()
                            if spec_name in spec_coverage:
                                spec_coverage[spec_name].append(f"{spec['name']}/{ticket['name']}")

    covered = [(s, refs) for s, refs in spec_coverage.items() if refs]
    uncovered = [s for s, refs in spec_coverage.items() if not refs]

    print("SPEC COVERAGE FROM IMPL TICKETS")
    print("=" * 60)
    print()

    print(f"COVERED ({len(covered)}/{len(spec_files)} specs):")
    for spec_name, refs in sorted(covered, key=lambda x: -len(x[1])):
        print(f"  {spec_name:<28} {len(refs):>3} ticket(s)")
        if args.verbose:
            for ref in refs[:5]:
                print(f"    - {ref}")
            if len(refs) > 5:
                print(f"    ... and {len(refs) - 5} more")

    print()
    print(f"UNCOVERED ({len(uncovered)} specs):")
    for spec_name in sorted(uncovered):
        print(f"  {spec_name}")

    print()
    print(f"Summary: {len(covered)}/{len(spec_files)} specs covered ({len(covered) * 100 // len(spec_files)}%)")

    return 0


def cmd_specs(args):
    """List all spec files."""
    spec_files = list(iter_spec_files())
    if not spec_files:
        print(f"No specs found in {SPEC_DIR}")
        return 1

    print(f"SPECS ({len(spec_files)} files)")
    print("=" * 40)
    for spec_file in spec_files:
        print(f"  {spec_file.stem}")

    return 0


def cmd_completeness(args):
    """Check section-level completeness for a spec."""
    spec_name = args.spec.upper().replace("-", "_")

    spec_file = SPEC_DIR / f"{spec_name}.md"
    if not spec_file.exists():
        spec_file = SPEC_DIR / f"{args.spec}.md"
        if not spec_file.exists():
            print(f"Spec not found: {args.spec}")
            print(f"Available specs:")
            for f in sorted(SPEC_DIR.glob("*.md")):
                print(f"  {f.stem.lower()}")
            return 1

    content = spec_file.read_text()
    section_pattern = re.compile(r'\{#([\w-]+)\}')
    spec_sections = set(section_pattern.findall(content))

    if not spec_sections:
        print(f"No {{#anchor}} sections found in {spec_file.name}")
        print("Add section anchors like: ## Section Title {{#section-id}}")
        return 1

    spec_sections -= SKIP_ANCHORS

    if not spec_sections:
        print(f"No actionable sections in {spec_file.name} (all filtered)")
        return 0

    spec_id = spec_file.stem.lower().replace("_", "-")
    spec_refs = {}
    file_level_refs = []

    for anchor in spec_sections:
        spec_refs[anchor] = []

    for ticket_dir in iter_all_ticket_dirs():
        readme = ticket_dir / "README.md"
        if not readme.exists():
            continue

        ticket_name = ticket_dir.name

        all_ticket_files = [readme] + list(ticket_dir.glob("*.md"))

        for ticket_file in all_ticket_files:
            if ticket_file.name == "README.md":
                file_label = ticket_name
            else:
                file_label = f"{ticket_name}/{ticket_file.stem}"

            try:
                file_content = ticket_file.read_text()
            except Exception:
                continue

            file_patterns = [
                f"docs/spec/{spec_file.name}",
                f"docs/spec/{spec_file.stem}.md",
                f"[[spec:{spec_id}]]",
            ]
            has_file_ref = any(p in file_content for p in file_patterns)
            if has_file_ref and file_label not in file_level_refs:
                file_level_refs.append(file_label)

            for anchor in spec_sections:
                patterns = [
                    f"docs/spec/{spec_file.name}#{anchor}",
                    f"docs/spec/{spec_file.stem}#{anchor}",
                    f"[[spec:{spec_id}#{anchor}]]",
                    f"#{anchor}",
                ]
                for pattern in patterns:
                    if pattern in file_content:
                        if file_label not in spec_refs[anchor]:
                            spec_refs[anchor].append(file_label)
                        break

    covered = [(a, refs) for a, refs in spec_refs.items() if refs]
    uncovered = [a for a, refs in spec_refs.items() if not refs]

    total = len(spec_sections)
    covered_count = len(covered)
    pct = (covered_count * 100 // total) if total > 0 else 0

    print(f"{spec_file.stem} completeness: {covered_count}/{total} sections ({pct}%)")
    print("=" * 60)

    if file_level_refs:
        print()
        print(f"FILE-LEVEL LINKS ({len(file_level_refs)} tickets reference this spec):")
        for ref in sorted(file_level_refs)[:10]:
            print(f"  ~ {ref}")
        if len(file_level_refs) > 10:
            print(f"  ... and {len(file_level_refs) - 10} more")
        print()
        print("  These tickets should add section anchors to links:")
        print(f"  e.g., docs/spec/{spec_file.name}#section-name")
    print()

    if covered:
        print("COVERED SECTIONS:")
        for anchor, refs in sorted(covered):
            refs_str = ", ".join(refs[:3])
            if len(refs) > 3:
                refs_str += f" (+{len(refs) - 3} more)"
            print(f"  + #{anchor:<30} -> {refs_str}")
        print()

    if uncovered:
        print("UNCOVERED SECTIONS:")
        for anchor in sorted(uncovered):
            print(f"  o #{anchor}")
        print()

    if args.verbose and covered:
        print("DETAILS:")
        for anchor, refs in sorted(covered):
            print(f"  #{anchor}:")
            for ref in refs:
                print(f"    - {ref}")
        print()

    if uncovered:
        if file_level_refs:
            print(f"Suggestion: Add section anchors to links in: {', '.join(file_level_refs[:3])}")
        else:
            print(f"Suggestion: Create impl tickets with links to {len(uncovered)} uncovered sections")
    else:
        print("All sections covered!")

    return 0


def cmd_new(args):
    """Create a new impl ticket from template."""
    from datetime import date

    name = args.name.lower().replace(" ", "-").replace("_", "-")
    ticket_dir = IMPL_TICKETS_DIR / name

    if ticket_dir.exists() and not args.force:
        print(f"Ticket directory already exists: {ticket_dir}")
        print("Use --force to overwrite")
        return 1

    # Read template
    if not TEMPLATE_PATH.exists():
        print(f"Template not found: {TEMPLATE_PATH}")
        return 1

    template = TEMPLATE_PATH.read_text()

    title = args.title or name.replace("-", " ").title()
    today = date.today().isoformat()

    content = template.replace("<feature-name>", name)
    content = content.replace("<Feature Title>", title)
    content = content.replace("<DATE>", today)

    ticket_dir.mkdir(parents=True, exist_ok=True)
    readme = ticket_dir / "README.md"
    readme.write_text(content)

    print(f"Created: {ticket_dir}/")
    print(f"  README.md - Edit user stories and tasks")
    print()
    print("Next steps:")
    print(f"  1. Edit {readme}")
    print(f"  2. Define user stories (P1 = MVP)")
    print(f"  3. Break down tasks with IDs")
    print(f"  4. Use 'specgraph ls {name}' to track progress")

    return 0


def cmd_queue(args):
    """Show the spec queue status."""
    print("Note: queue/complete structure removed. Use 'specgraph specs' to list all specs.")
    print("      Use 'specgraph coverage' to see which specs have impl tickets.")
    return cmd_specs(args)


def cmd_next(args):
    """Show the next spec to process from the queue."""
    if not SPEC_QUEUE.exists():
        print("No specs directory")
        return 1

    queue_specs = sorted(SPEC_QUEUE.glob("*.md"))
    if not queue_specs:
        print("Queue is empty! All specs complete.")
        return 0

    next_spec = queue_specs[0]
    print(f"Next spec: {next_spec.stem.lower()}")
    print(f"File: {next_spec}")
    print()
    print("To process:")
    print(f"  1. Add frontmatter + section anchors")
    print(f"  2. Run: specgraph scaffold {next_spec.stem.lower()}")
    print(f"  3. Close tickets as you implement")
    print(f"  4. Run: specgraph complete {next_spec.stem.lower()}")

    return 0


def cmd_complete(args):
    """Move a spec from queue to complete."""
    spec_name = args.spec.upper().replace("-", "_")

    spec_file = SPEC_QUEUE / f"{spec_name}.md"
    if not spec_file.exists():
        spec_file = SPEC_QUEUE / f"{args.spec}.md"
        if not spec_file.exists():
            print(f"Spec not found in queue: {args.spec}")
            print(f"Looked for: {SPEC_QUEUE / spec_name}.md")
            return 1

    spec = parse_spec_file_full(spec_file)
    if not spec:
        print(f"Could not parse spec: {spec_file}")
        return 1

    if not spec.sections:
        print(f"Spec has no sections with {{#anchor}} - needs processing first")
        return 1

    dest = SPEC_COMPLETE / spec_file.name
    SPEC_COMPLETE.mkdir(parents=True, exist_ok=True)
    spec_file.rename(dest)
    print(f"Moved {spec_file.stem} to complete/")

    remaining = list(SPEC_QUEUE.glob("*.md"))
    print(f"\nQueue: {len(remaining)} specs remaining")

    return 0


def cmd_scaffold(args):
    """Create ticket stubs from spec sections."""
    if args.all:
        if not SPEC_DIR.exists():
            print(f"Specs dir not found: {SPEC_DIR}")
            return 1

        spec_files = list(SPEC_DIR.glob("*.md"))
        if not spec_files:
            print("No specs found")
            return 0

        print(f"Scaffolding {len(spec_files)} specs...")
        print()

        total_tickets = 0
        for spec_file in sorted(spec_files):
            count = scaffold_spec(spec_file, dry_run=args.dry_run)
            total_tickets += count

        print()
        print(f"{'Would create' if args.dry_run else 'Created'} {total_tickets} tickets across {len(spec_files)} specs")
        return 0

    if not args.spec:
        print("Specify a spec name or use --all")
        return 1

    spec_file = find_spec_file(args.spec)
    if not spec_file:
        print(f"Spec not found: {args.spec}")
        return 1

    scaffold_spec(spec_file, dry_run=args.dry_run)
    return 0


def scaffold_spec(spec_file: Path, dry_run: bool = False) -> int:
    """Scaffold tickets for a single spec."""
    sections = parse_spec_sections(spec_file)
    if not sections:
        print(f"  {spec_file.stem}: No sections with {{#anchor}} found, skipping")
        return 0

    sections = [s for s in sections if s["anchor"] not in SKIP_ANCHORS]
    if not sections:
        print(f"  {spec_file.stem}: No actionable sections (all skipped), skipping")
        return 0

    fm = parse_frontmatter(spec_file)
    spec_id = fm.get("spec", spec_file.stem.lower().replace("_", "-"))
    spec_title = get_title(spec_file)

    spec_name = spec_file.stem.lower().replace("_", "-")
    ticket_dir = SPEC_TICKETS_DIR / spec_name

    if ticket_dir.exists():
        existing = len(list(ticket_dir.glob("*.md"))) - 1
        print(f"  {spec_file.stem}: Already has {existing} tickets, skipping")
        return 0

    if dry_run:
        print(f"  {spec_file.stem}: Would create {len(sections)} tickets")
        for s in sections:
            print(f"    - {s['anchor']}: {s['title']}")
        return len(sections)

    ticket_dir.mkdir(parents=True, exist_ok=True)

    create_readme_file(spec_name, spec_id, sections, spec_title)

    for section in sections:
        create_ticket_file(spec_name, section, spec_id)

    print(f"  {spec_file.stem}: Created {len(sections)} tickets")

    return len(sections)


# ---------------------------------------------------------------------------
# Use cases
# ---------------------------------------------------------------------------

def cmd_uc_ls(args):
    """List all use cases with completion percentage."""
    if not USE_CASES_DIR.exists():
        print(f"No use cases directory: {USE_CASES_DIR}")
        return 1

    uc_files = sorted(USE_CASES_DIR.glob("*.md"))
    if not uc_files:
        print("No use cases found.")
        return 0

    bench_results = _load_bench_results()

    print(f"{'ID':<10} {'TITLE':<35} {'PRI':<5} {'STATUS':<10} {'DONE':<8} {'BENCH'}")
    print("-" * 80)

    for uc_file in uc_files:
        fm = parse_uc_frontmatter(uc_file)
        uc_id = fm.get("id", uc_file.stem)
        title = fm.get("title", uc_file.stem)
        status = fm.get("status", "unknown")
        priority = fm.get("priority", "-")

        requires = fm.get("requires", {})
        if requires:
            results = validate_uc_requirements(requires)
            total = sum(len(items) for items in results.values())
            done = sum(1 for items in results.values() for _, ok in items if ok)
        else:
            total = 0
            done = 0

        done_str = f"{done}/{total}" if total > 0 else "-"

        matched = _reverse_lookup_benchmarks(uc_id, requires)
        if matched:
            n_pass = sum(1 for b in matched if bench_results.get(b["id"], {}).get("passed"))
            bench_str = f"{n_pass}/{len(matched)}"
        else:
            bench_str = "-"

        display_title = title[:33] + ".." if len(title) > 35 else title
        print(f"{uc_id:<10} {display_title:<35} {priority:<5} {status:<10} {done_str:<8} {bench_str}")

    return 0


def cmd_uc_show(args):
    """Show one use case with all requirements and their status."""
    uc_file = _find_uc_file(args.uc_id)
    if not uc_file:
        return 1

    fm = parse_uc_frontmatter(uc_file)
    uc_id = fm.get("id", uc_file.stem)
    title = fm.get("title", uc_file.stem)
    status = fm.get("status", "unknown")
    persona = fm.get("persona", "")

    priority = fm.get("priority", "-")

    print(f"{uc_id}: {title}")
    print(f"Status: {status}  Priority: {priority}")
    if persona:
        print(f"Persona: {persona}")
    print()

    requires = fm.get("requires", {})
    if not requires:
        print("No requirements defined.")
        return 0

    results = validate_uc_requirements(requires)
    total = sum(len(items) for items in results.values())
    done = sum(1 for items in results.values() for _, ok in items if ok)

    print(f"Requirements: {done}/{total} satisfied")
    print()

    for category, items in results.items():
        cat_done = sum(1 for _, ok in items if ok)
        print(f"  {category}: ({cat_done}/{len(items)})")
        for item, ok in items:
            icon = "[x]" if ok else "[ ]"
            print(f"    {icon} {item}")
        print()

    # Demand coverage
    if SPEC_TICKETS_DIR.exists():
        demand_tickets = {"open": [], "in-progress": [], "closed": [], "deferred": []}
        for ticket_dir in sorted(SPEC_TICKETS_DIR.iterdir()):
            readme = ticket_dir / "README.md"
            if not readme.exists():
                continue
            tfm = parse_frontmatter(readme)
            demands = tfm.get("demand", [])
            if not isinstance(demands, list):
                demands = [demands]
            if uc_id in demands:
                t_status = tfm.get("status", "open")
                if t_status in demand_tickets:
                    demand_tickets[t_status].append(ticket_dir.name)
                else:
                    demand_tickets.setdefault("open", []).append(ticket_dir.name)

        total_demand = sum(len(v) for v in demand_tickets.values())
        if total_demand > 0:
            print(f"Demand Coverage: {total_demand} tickets")
            for s in ["closed", "in-progress", "open", "deferred"]:
                if demand_tickets[s]:
                    names = ", ".join(demand_tickets[s])
                    print(f"  {s}: {len(demand_tickets[s])}  ({names})")
            print()

    # Metrics
    uc_metrics = fm.get("metrics", {})
    if uc_metrics:
        bench_results = _load_bench_results()
        print("Metrics:")
        for category, items in uc_metrics.items():
            print(f"  {category}:")
            for m in items:
                m_id = m.get("id", "?")
                target = m.get("target", "")
                benchmark = m.get("benchmark", "")
                parts = [f"{m_id}"]
                if target:
                    parts.append(f'"{target}"')
                if benchmark:
                    br = bench_results.get(benchmark)
                    if br:
                        if br.get("skipped"):
                            result_str = "SKIP"
                        elif br.get("passed"):
                            result_str = "PASS"
                        else:
                            result_str = "FAIL"
                    else:
                        result_str = "-"
                    parts.append(f"(bench: {benchmark} -> {result_str})")
                print(f"    - {' '.join(parts)}")
        print()

    # Benchmarks reverse-lookup
    matched_benchmarks = _reverse_lookup_benchmarks(uc_id, requires)
    if matched_benchmarks:
        bench_results = _load_bench_results()
        n_pass = 0
        n_fail = 0
        n_notrun = 0
        bench_lines = []
        for b in matched_benchmarks:
            br = bench_results.get(b["id"])
            if br:
                if br.get("skipped"):
                    result_str = " -  "
                    n_notrun += 1
                elif br.get("passed"):
                    result_str = "PASS"
                    n_pass += 1
                else:
                    result_str = "FAIL"
                    n_fail += 1
            else:
                result_str = " -  "
                n_notrun += 1

            desc = f"(op: {b['op']})" if b["type"] == "op" else "(workflow)"
            bench_lines.append(f"  [{result_str}] {b['id']} {desc}")

        total_bench = len(matched_benchmarks)
        print(f"Benchmarks: {total_bench} total ({n_pass} pass, {n_fail} fail, {n_notrun} not-run)")
        for line in bench_lines:
            print(line)
        print()

    return 0


def cmd_uc_gaps(args):
    """Show only missing requirements for a use case."""
    uc_file = _find_uc_file(args.uc_id)
    if not uc_file:
        return 1

    fm = parse_uc_frontmatter(uc_file)
    title = fm.get("title", uc_file.stem)

    requires = fm.get("requires", {})
    if not requires:
        print("No requirements defined.")
        return 0

    results = validate_uc_requirements(requires)
    total = sum(len(items) for items in results.values())
    missing = sum(1 for items in results.values() for _, ok in items if not ok)

    if missing == 0:
        print(f"No gaps! All {total} requirements satisfied for {title}.")
        return 0

    print(f"GAPS for {title} ({missing}/{total} missing):")
    print()

    for category, items in results.items():
        gaps = [item for item, ok in items if not ok]
        if gaps:
            print(f"  {category}:")
            for item in gaps:
                print(f"    - {item}")
            print()

    return 0


def cmd_uc_new(args):
    """Create a new use case file from template."""
    from datetime import date

    name = args.name.lower().replace(" ", "-").replace("_", "-")
    uc_file = USE_CASES_DIR / f"{name}.md"

    if uc_file.exists() and not args.force:
        print(f"Use case already exists: {uc_file}")
        print("Use --force to overwrite")
        return 1

    existing_ids = []
    if USE_CASES_DIR.exists():
        for f in USE_CASES_DIR.glob("*.md"):
            fm = parse_uc_frontmatter(f)
            uc_id = fm.get("id", "")
            match = re.match(r'UC-(\d+)', uc_id)
            if match:
                existing_ids.append(int(match.group(1)))

    next_num = max(existing_ids, default=0) + 1
    uc_id = f"UC-{next_num:03d}"

    title = args.title or name.replace("-", " ").title()
    priority = args.priority or "P2"

    content = f"""---
id: {uc_id}
title: "{title}"
status: research
priority: {priority}
persona: ""
requires:
  ops:
    # - domain:op_name
  connectors:
    # - connector-name
  datasources:
    # - source/dataset
  views:
    # - view_name
  tickets:
    # - ticket-name
---

# {uc_id}: {title}

## Persona

**Who** is the user and what is their context?

## Problem

What problem are they trying to solve? What does their current workflow look like?

## Workflow

### Phase 1: ...

Describe the first phase of the user's workflow.

**Platform requirements:**
- `op: ...`
- `views: ...`

## Success Criteria

1. ...
"""

    USE_CASES_DIR.mkdir(parents=True, exist_ok=True)
    uc_file.write_text(content)

    print(f"Created: {uc_file}")
    print(f"  ID: {uc_id}")
    print(f"  Priority: {priority}")
    print()
    print("Next steps:")
    print(f"  1. Edit {uc_file}")
    print(f"  2. Fill in persona, problem, workflow")
    print(f"  3. Uncomment/add requirements in requires: block")
    print(f"  4. Run: specgraph uc show {uc_id}")

    return 0


def _find_uc_file(uc_id: str) -> Optional[Path]:
    """Find a use case file by ID or filename stem."""
    if not USE_CASES_DIR.exists():
        print(f"No use cases directory: {USE_CASES_DIR}")
        return None

    for uc_file in USE_CASES_DIR.glob("*.md"):
        fm = parse_uc_frontmatter(uc_file)
        if fm.get("id", "").upper() == uc_id.upper():
            return uc_file

    for uc_file in USE_CASES_DIR.glob("*.md"):
        if uc_file.stem == uc_id or uc_file.stem == uc_id.lower():
            return uc_file

    print(f"Use case not found: {uc_id}")
    available = [f.stem for f in USE_CASES_DIR.glob("*.md")]
    if available:
        print(f"Available: {', '.join(available)}")
    return None


def cmd_uc(args):
    """Dispatch use case subcommands."""
    uc_commands = {
        "ls": cmd_uc_ls,
        "show": cmd_uc_show,
        "gaps": cmd_uc_gaps,
        "new": cmd_uc_new,
    }
    if not hasattr(args, "uc_command") or args.uc_command is None:
        return cmd_uc_ls(args)
    return uc_commands[args.uc_command](args)


# ---------------------------------------------------------------------------
# Roadmap
# ---------------------------------------------------------------------------

def parse_roadmap_milestones() -> list[dict]:
    """Parse milestones from roadmap file."""
    if not ROADMAP_FILE.exists():
        return []

    content = ROADMAP_FILE.read_text()
    milestones = []

    milestone_pattern = re.compile(
        r'^### M(\d+):\s+(.+?)\s*\{#([a-z0-9-]+)\}\s*$', re.MULTILINE)

    matches = list(milestone_pattern.finditer(content))

    for i, match in enumerate(matches):
        num = match.group(1)
        title = match.group(2)
        ms_id = match.group(3)
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        body = content[start:end]

        milestone = {
            "num": int(num),
            "id": ms_id,
            "title": title,
            "target": "",
            "services": [],
            "domains": [],
            "deadlines": [],
            "depends_on": [],
            "requires": {},
        }

        target_match = re.search(r'\*\*Target:\*\*\s*(.+)', body)
        if target_match:
            milestone["target"] = target_match.group(1).strip()

        svc_match = re.search(r'\*\*Services:\*\*\s*(.+)', body)
        if svc_match:
            milestone["services"] = [s.strip() for s in svc_match.group(1).split(",")]

        dom_match = re.search(r'\*\*Domains:\*\*\s*(.+)', body)
        if dom_match:
            milestone["domains"] = [d.strip() for d in dom_match.group(1).split(",")]

        dep_match = re.search(r'\*\*Depends on:\*\*\s*(.+)', body)
        if dep_match:
            dep_text = dep_match.group(1)
            milestone["depends_on"] = re.findall(r'\[\[milestone:([^\]]+)\]\]', dep_text)

        deadline_pattern = re.compile(r'^- (\d{4}-\d{2}-\d{2}):\s*(.+)', re.MULTILINE)
        for dm in deadline_pattern.finditer(body):
            milestone["deadlines"].append({"date": dm.group(1), "desc": dm.group(2).strip()})

        req_match = re.search(r'```yaml\n(.*?)```', body, re.DOTALL)
        if req_match:
            requires = {}
            current_cat = None
            for line in req_match.group(1).strip().split("\n"):
                line = line.split("#")[0].rstrip()
                cat_m = re.match(r'^(\w[\w_-]*):\s*$', line)
                if cat_m:
                    current_cat = cat_m.group(1)
                    requires[current_cat] = []
                    continue
                item_m = re.match(r'^  - (.+)$', line)
                if item_m and current_cat is not None:
                    requires[current_cat].append(item_m.group(1).strip())
            milestone["requires"] = requires

        milestones.append(milestone)

    return milestones


def _milestone_ticket_counts(ms_id: str, specs: list[dict]) -> dict:
    """Count tickets assigned to a milestone via frontmatter."""
    counts = {"closed": 0, "deferred": 0, "in-progress": 0, "open": 0, "total_dirs": 0}
    for s in specs:
        if s["frontmatter"].get("milestone") == ms_id:
            counts["total_dirs"] += 1
            for k in ["closed", "deferred", "in-progress", "open"]:
                counts[k] += s["counts"].get(k, 0)
    return counts


def _check_milestone_requires(requires: dict) -> dict:
    """Check milestone requirements using the same validators as use cases."""
    results = {}
    for category, items in requires.items():
        checker = UC_CHECKERS.get(category)
        if checker is None:
            results[category] = [(item, False) for item in items]
        else:
            checked = []
            for item in items:
                try:
                    result = checker(PROJECT_ROOT, item)
                except TypeError:
                    result = checker(item)
                checked.append((item, result))
            results[category] = checked
    return results


def cmd_roadmap(args):
    """Show roadmap milestones with status."""
    milestones = parse_roadmap_milestones()
    if not milestones:
        print(f"No milestones found in {ROADMAP_FILE}")
        return 1

    specs = list_specs()
    ms_id = getattr(args, 'milestone_id', None)

    if getattr(args, 'deadlines', False):
        all_deadlines = []
        for ms in milestones:
            for dl in ms["deadlines"]:
                all_deadlines.append((dl["date"], f"M{ms['num']}", ms["id"], dl["desc"]))
        all_deadlines.sort()
        print(f"{'DATE':<12} {'MS':<5} {'MILESTONE':<26} DESCRIPTION")
        print("-" * 80)
        for date, num, mid, desc in all_deadlines:
            print(f"{date:<12} {num:<5} {mid:<26} {desc}")
        return 0

    if ms_id:
        ms = next((m for m in milestones if m["id"] == ms_id), None)
        if not ms:
            print(f"Milestone not found: {ms_id}")
            print(f"Available: {', '.join(m['id'] for m in milestones)}")
            return 1

        print(f"M{ms['num']}: {ms['title']}")
        print(f"Target: {ms['target']}")
        if ms["depends_on"]:
            print(f"Depends on: {', '.join(ms['depends_on'])}")
        if ms["services"]:
            print(f"Services: {', '.join(ms['services'])}")
        if ms["domains"]:
            print(f"Domains: {', '.join(ms['domains'])}")
        print()

        if ms["requires"]:
            results = _check_milestone_requires(ms["requires"])
            total = sum(len(items) for items in results.values())
            done = sum(1 for items in results.values() for _, ok in items if ok)
            print(f"Requirements: {done}/{total} satisfied")
            print()
            for category, items in results.items():
                cat_done = sum(1 for _, ok in items if ok)
                print(f"  {category}: ({cat_done}/{len(items)})")
                for item, ok in items:
                    icon = "[x]" if ok else "[ ]"
                    print(f"    {icon} {item}")
                print()

        assigned = [s for s in specs if s["frontmatter"].get("milestone") == ms_id]
        if assigned:
            print(f"Tickets ({len(assigned)}):")
            for s in assigned:
                c = s["counts"]
                total_stories = c["closed"] + c.get("deferred", 0) + c["in-progress"] + c["open"]
                if total_stories > 0:
                    print(f"  {s['name']:<30} {c['closed']}/{total_stories} closed")
                else:
                    print(f"  {s['name']:<30} (no stories)")

        if ms["deadlines"]:
            print()
            print("Deadlines:")
            for dl in ms["deadlines"]:
                print(f"  {dl['date']}  {dl['desc']}")

        return 0

    # Overview
    print(f"{'MS':<5} {'ID':<28} {'TARGET':<10} {'REQS':<8} {'TICKETS':<10} {'NEXT DEADLINE'}")
    print("-" * 90)

    for ms in milestones:
        if ms["requires"]:
            results = _check_milestone_requires(ms["requires"])
            total_reqs = sum(len(items) for items in results.values())
            done_reqs = sum(1 for items in results.values() for _, ok in items if ok)
            req_str = f"{done_reqs}/{total_reqs}"
        else:
            req_str = "-"

        tc = _milestone_ticket_counts(ms["id"], specs)
        if tc["total_dirs"] > 0:
            ticket_str = f"{tc['total_dirs']} dirs"
        else:
            ticket_str = "-"

        if ms["deadlines"]:
            next_dl = min(ms["deadlines"], key=lambda d: d["date"])
            dl_str = next_dl["date"]
        else:
            dl_str = "-"

        print(f"M{ms['num']:<4} {ms['id']:<28} {ms['target']:<10} {req_str:<8} {ticket_str:<10} {dl_str}")

    return 0


# ---------------------------------------------------------------------------
# Benchmarks
# ---------------------------------------------------------------------------

def cmd_bench(args):
    """Benchmark management commands."""
    bench_cmd = getattr(args, 'bench_command', None)

    if bench_cmd == "ls":
        return cmd_bench_ls(args)
    elif bench_cmd == "run":
        return cmd_bench_run(args)
    elif bench_cmd == "status":
        return cmd_bench_status(args)
    elif bench_cmd == "compare":
        return cmd_bench_compare(args)
    else:
        print("Usage: specgraph bench {ls|run|status|compare}")
        print()
        print("  ls       List all benchmarks with status")
        print("  run      Run benchmarks")
        print("  status   Show latest pass/fail for all")
        print("  compare  Detailed metrics for one benchmark")
        return 1


def _parse_bench_yaml_simple(path: Path) -> dict:
    """Parse benchmark YAML file with minimal parsing."""
    result = {}
    content = path.read_text()
    has_pipeline = False

    for line in content.split("\n"):
        if line.startswith("pipeline:"):
            has_pipeline = True
        m = re.match(r'^(\w[\w_-]*)\s*:\s*(.+)$', line)
        if m:
            key, val = m.groups()
            if "#" in val:
                val = val[:val.index("#")]
            val = val.strip().strip('"').strip("'")
            if key in ("id", "status", "op", "use_case", "milestone", "description"):
                result[key] = val

    result["_has_pipeline"] = has_pipeline
    return result


def cmd_bench_ls(args):
    """List all benchmarks with their status."""
    ops_dir = BENCH_DIR / "ops"
    wf_dir = BENCH_DIR / "workflows"
    yamls = sorted(ops_dir.glob("*.yaml")) if ops_dir.exists() else []
    yamls += sorted(wf_dir.glob("*.yaml")) if wf_dir.exists() else []

    if not yamls:
        print("No benchmarks found.")
        return 1

    results = _load_bench_results()

    print(f"{'ID':<32} {'TYPE':<10} {'STATUS':<10} {'LAST RUN':<12} {'RESULT'}")
    print("-" * 85)

    for path in yamls:
        spec = _parse_bench_yaml_simple(path)
        bench_id = spec.get("id", path.stem)
        bench_type = "workflow" if spec.get("_has_pipeline") else "op"
        status = spec.get("status", "draft")

        latest = results.get(bench_id)
        if latest:
            last_run = latest.get("date", "-")
            if latest.get("skipped"):
                result_str = f"SKIP ({latest.get('skip_reason', '')})"
            elif latest.get("passed"):
                result_str = "PASS"
            else:
                result_str = "FAIL"
        else:
            last_run = "-"
            result_str = "-"

        print(f"{bench_id:<32} {bench_type:<10} {status:<10} {last_run:<12} {result_str}")

    return 0


def cmd_bench_run(args):
    """Run benchmarks."""
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

    try:
        from benchmarks import runner
    except ImportError as e:
        print(f"Cannot import benchmark runner: {e}")
        print("Run with: uv run python specgraph bench run")
        return 1

    bench_id = getattr(args, 'bench_id', None)
    milestone = getattr(args, 'milestone', None)
    uc = getattr(args, 'uc', None)

    results = runner.run_benchmarks(
        bench_id=bench_id,
        milestone=milestone,
        use_case=uc,
    )

    if not results:
        print("No benchmarks matched the filter.")
        return 1

    passed_count = 0
    failed_count = 0
    skipped_count = 0

    for result in results:
        saved = runner.save_result(result)

        if result.skipped:
            skipped_count += 1
            print(f"  SKIP  {result.id}  ({result.skip_reason})")
            continue

        if result.error:
            failed_count += 1
            print(f"  ERR   {result.id}  ({result.error})")
            continue

        if result.passed:
            passed_count += 1
            mark = "PASS"
        else:
            failed_count += 1
            mark = "FAIL"

        print(f"  {mark}  {result.id}  ({result.duration_ms}ms)")
        for m in result.metrics:
            icon = "+" if m.passed else "x"
            print(f"         {icon} {m.name}: {m.value} (threshold: {m.threshold})")

    print()
    total = passed_count + failed_count + skipped_count
    print(f"{total} benchmarks: {passed_count} passed, {failed_count} failed, {skipped_count} skipped")

    return 0 if failed_count == 0 else 1


def cmd_bench_status(args):
    """Show latest pass/fail for all benchmarks."""
    results = _load_bench_results()

    if not results:
        print("No benchmark results found. Run: specgraph bench run")
        return 1

    print(f"{'ID':<32} {'DATE':<12} {'TIME':<8} {'RESULT':<8} {'METRICS'}")
    print("-" * 75)

    for bench_id, result in sorted(results.items()):
        date_str = result.get("date", "-")
        duration = result.get("duration_ms", 0)
        time_str = f"{duration}ms" if duration else "-"

        if result.get("skipped"):
            result_str = "SKIP"
            metrics_str = result.get("skip_reason", "")
        elif result.get("error"):
            result_str = "ERR"
            metrics_str = result.get("error", "")[:40]
        elif result.get("passed"):
            result_str = "PASS"
            metrics = result.get("metrics", [])
            metrics_str = f"{len(metrics)} metrics, all passing"
        else:
            result_str = "FAIL"
            metrics = result.get("metrics", [])
            failed = [m for m in metrics if not m.get("passed")]
            metrics_str = f"{len(failed)}/{len(metrics)} metrics failing"

        print(f"{bench_id:<32} {date_str:<12} {time_str:<8} {result_str:<8} {metrics_str}")

    return 0


def cmd_bench_compare(args):
    """Show detailed metrics for one benchmark."""
    bench_id = getattr(args, 'bench_id', None)
    if not bench_id:
        print("Usage: specgraph bench compare <bench-id>")
        return 1

    results = _load_bench_results()
    result = results.get(bench_id)

    if not result:
        print(f"No results found for {bench_id}. Run: specgraph bench run {bench_id}")
        return 1

    print(f"Benchmark: {result['id']}")
    print(f"Date: {result.get('date', '-')}")
    print(f"Duration: {result.get('duration_ms', 0)}ms")
    print(f"Result: {'PASS' if result.get('passed') else 'FAIL'}")
    print()

    if result.get("error"):
        print(f"Error: {result['error']}")
        return 1

    if result.get("skipped"):
        print(f"Skipped: {result.get('skip_reason')}")
        return 0

    metrics = result.get("metrics", [])
    if not metrics:
        print("No metrics recorded.")
        return 0

    print(f"{'METRIC':<24} {'VALUE':<16} {'THRESHOLD':<16} {'RESULT'}")
    print("-" * 65)

    for m in metrics:
        icon = "PASS" if m.get("passed") else "FAIL"
        val_str = str(m.get("value", "-"))
        thr_str = str(m.get("threshold", "-"))
        print(f"{m['name']:<24} {val_str:<16} {thr_str:<16} {icon}")

    return 0


def _load_bench_results() -> dict:
    """Load latest benchmark results (no runner dependency)."""
    import json as _json

    results_dir = BENCH_DIR / "results"
    if not results_dir.exists():
        return {}

    day_dirs = sorted(
        [d for d in results_dir.iterdir() if d.is_dir() and d.name[0].isdigit()],
        reverse=True,
    )

    results = {}
    for day_dir in day_dirs:
        for result_file in day_dir.glob("*.json"):
            bench_id = result_file.stem
            if bench_id not in results:
                try:
                    results[bench_id] = _json.loads(result_file.read_text())
                except _json.JSONDecodeError:
                    continue

    return results


def _reverse_lookup_benchmarks(uc_id: str, requires: dict) -> list:
    """Find benchmarks linked to a use case."""
    ops_dir = BENCH_DIR / "ops"
    wf_dir = BENCH_DIR / "workflows"
    yamls = sorted(ops_dir.glob("*.yaml")) if ops_dir.exists() else []
    yamls += sorted(wf_dir.glob("*.yaml")) if wf_dir.exists() else []

    if not yamls:
        return []

    required_op_names = set()
    for op_ref in requires.get("ops", []):
        if ":" in op_ref:
            required_op_names.add(op_ref.split(":", 1)[1])
        else:
            required_op_names.add(op_ref)

    matched = []
    for path in yamls:
        try:
            spec = _parse_bench_yaml_simple(path)
        except Exception:
            continue

        bench_id = spec.get("id", path.stem)
        bench_type = "workflow" if spec.get("_has_pipeline") else "op"
        status = spec.get("status", "draft")
        bench_op = spec.get("op", "")

        if bench_type == "workflow" and spec.get("use_case") == uc_id:
            matched.append({"id": bench_id, "type": bench_type, "status": status, "op": bench_op})
            continue

        if bench_type == "op" and bench_op:
            for op_name in required_op_names:
                if op_name in bench_op:
                    matched.append({"id": bench_id, "type": bench_type, "status": status, "op": bench_op})
                    break

    return matched


# ---------------------------------------------------------------------------
# CRM (Contact Management)
# ---------------------------------------------------------------------------

def _load_contacts() -> list[dict]:
    """Load all contact files."""
    if not CONTACTS_DIR.exists():
        return []

    contacts = []
    # Support both flat and subdirectory layouts
    for f in sorted(CONTACTS_DIR.rglob("*.md")):
        if f.name == "TEMPLATE.md":
            continue
        fm = parse_frontmatter(f)
        fm["_path"] = f
        fm["_stem"] = f.stem
        contacts.append(fm)
    return contacts


def cmd_crm_ls(args):
    """List all contacts, sorted by tier then name."""
    contacts = _load_contacts()
    if not contacts:
        print("No contacts found.")
        return 0

    status_filter = getattr(args, "status", None)
    category_filter = getattr(args, "category", None)
    tier_filter = getattr(args, "tier", None)

    if status_filter:
        contacts = [c for c in contacts if c.get("status") == status_filter]
    if category_filter:
        contacts = [c for c in contacts if c.get("category") == category_filter]
    if tier_filter:
        contacts = [c for c in contacts if c.get("tier") == tier_filter]

    if not contacts:
        print("No contacts match filters.")
        return 0

    def sort_key(c):
        try:
            t = int(c.get("tier", 9))
        except (ValueError, TypeError):
            t = 9
        return (t, c.get("name", ""))

    contacts.sort(key=sort_key)

    print(f"{'TIER':<5} {'NAME':<22} {'ORG':<20} {'CAT':<10} {'STATUS':<12} {'NEXT ACTION'}")
    print("-" * 100)

    for c in contacts:
        name = c.get("name", c["_stem"])
        org = c.get("org", "")
        cat = c.get("category", "")
        status = c.get("status", "")
        tier = c.get("tier", "-")
        next_action = c.get("next_action", "")

        display_name = name[:20] + ".." if len(name) > 22 else name
        display_org = org[:18] + ".." if len(org) > 20 else org
        display_action = next_action[:40] + ".." if len(next_action) > 42 else next_action

        print(f"  {tier:<3} {display_name:<22} {display_org:<20} {cat:<10} {status:<12} {display_action}")

    print(f"\n{len(contacts)} contacts")
    return 0


def cmd_crm_show(args):
    """Show full details for a contact."""
    contact_name = args.contact_name.lower()

    contact_file = None
    for f in CONTACTS_DIR.rglob("*.md"):
        if f.name == "TEMPLATE.md":
            continue
        if f.stem == contact_name:
            contact_file = f
            break
        fm = parse_frontmatter(f)
        if fm.get("name", "").lower().replace(" ", "-") == contact_name:
            contact_file = f
            break
        if contact_name in fm.get("name", "").lower():
            contact_file = f
            break

    if not contact_file:
        print(f"Contact not found: {contact_name}")
        available = [f.stem for f in CONTACTS_DIR.rglob("*.md") if f.name != "TEMPLATE.md"]
        if available:
            print(f"Available: {', '.join(sorted(available))}")
        return 1

    fm = parse_frontmatter(contact_file)
    content = contact_file.read_text()

    name = fm.get("name", contact_file.stem)
    org = fm.get("org", "")
    role = fm.get("role", "")
    print(f"{name}")
    if role and org:
        print(f"  {role} @ {org}")
    elif org:
        print(f"  {org}")

    print()
    for key in ["category", "status", "tier", "met", "last_contact", "next_action", "email", "phone", "web"]:
        val = fm.get(key, "")
        if val:
            print(f"  {key}: {val}")
    print()

    end = content.find("\n---", 3)
    if end != -1:
        body = content[end + 4:].strip()
        if body:
            print(body)

    return 0


def cmd_crm_new(args):
    """Create a new contact from template."""
    name_slug = args.name.lower().replace(" ", "-").replace("_", "-")
    contact_file = CONTACTS_DIR / f"{name_slug}.md"

    if contact_file.exists() and not getattr(args, "force", False):
        print(f"Contact already exists: {contact_file}")
        print("Use --force to overwrite")
        return 1

    template_file = CONTACTS_DIR / "TEMPLATE.md"
    if template_file.exists():
        content = template_file.read_text()
    else:
        content = """---
name: ""
org: ""
role: ""
category: ""
status: ""
tier: 0
met: ""
last_contact: ""
next_action: ""
email: ""
phone: ""
web: ""
---

## Context

## Notes
"""

    display_name = args.name.replace("-", " ").title() if "-" in args.name else args.name
    content = content.replace('name: ""', f'name: "{display_name}"', 1)

    CONTACTS_DIR.mkdir(parents=True, exist_ok=True)
    contact_file.write_text(content)

    print(f"Created: {contact_file}")
    print(f"Edit the file to fill in details.")
    return 0


def cmd_crm_follow_ups(args):
    """Show contacts with pending next_action."""
    contacts = _load_contacts()
    actionable = [c for c in contacts if c.get("next_action")]

    if not actionable:
        print("No pending follow-ups.")
        return 0

    def sort_key(c):
        try:
            t = int(c.get("tier", 9))
        except (ValueError, TypeError):
            t = 9
        return (t, c.get("name", ""))

    actionable.sort(key=sort_key)

    print(f"{'TIER':<5} {'NAME':<22} {'STATUS':<12} {'NEXT ACTION'}")
    print("-" * 80)

    for c in actionable:
        name = c.get("name", c["_stem"])
        status = c.get("status", "")
        tier = c.get("tier", "-")
        next_action = c.get("next_action", "")

        display_name = name[:20] + ".." if len(name) > 22 else name
        display_action = next_action[:50] + ".." if len(next_action) > 52 else next_action

        print(f"  {tier:<3} {display_name:<22} {status:<12} {display_action}")

    print(f"\n{len(actionable)} follow-ups")
    return 0


def cmd_crm(args):
    """Dispatch CRM subcommands."""
    crm_commands = {
        "ls": cmd_crm_ls,
        "show": cmd_crm_show,
        "new": cmd_crm_new,
        "follow-ups": cmd_crm_follow_ups,
    }
    cmd = getattr(args, "crm_command", None)
    if cmd is None:
        return cmd_crm_ls(args)
    return crm_commands[cmd](args)


# ---------------------------------------------------------------------------
# Agent onboarding
# ---------------------------------------------------------------------------

def cmd_init(args):
    """Print project overview for agent onboarding."""
    project_name = PROJECT_ROOT.name
    print(f"Project: {project_name}")
    print(f"Root: {PROJECT_ROOT}")
    print()

    # Reading order
    reading_order = []
    claude_md = PROJECT_ROOT / "CLAUDE.md"
    if claude_md.exists():
        reading_order.append(("CLAUDE.md", "Project constitution"))
    vision_md = PROJECT_ROOT / "docs" / "VISION.md"
    if vision_md.exists():
        reading_order.append(("docs/VISION.md", "Vision & principles"))

    spec_count = len(list(SPEC_DIR.glob("*.md"))) if SPEC_DIR.exists() else 0
    if spec_count > 0:
        reading_order.append((str(SPEC_DIR.relative_to(PROJECT_ROOT)) + "/", f"{spec_count} specs"))

    if DECISIONS_DIR.exists():
        adr_count = len(list(DECISIONS_DIR.glob("*.md")))
        if adr_count > 0:
            reading_order.append((str(DECISIONS_DIR.relative_to(PROJECT_ROOT)) + "/", f"{adr_count} ADRs"))

    if reading_order:
        print("Reading Order:")
        for i, (path, desc) in enumerate(reading_order, 1):
            print(f"  {i}. {path} ({desc})")
        print()

    # Ticket summary
    specs = list_specs()
    if specs:
        total = {"closed": 0, "in-progress": 0, "open": 0, "deferred": 0}
        for spec in specs:
            for k in total:
                total[k] += spec["counts"].get(k, 0)

        ticket_count = sum(total.values())
        print(f"Tickets: {ticket_count} total")
        print(f"  {total['closed']} closed, {total['in-progress']} in-progress, {total['open']} open")
        if total['deferred']:
            print(f"  {total['deferred']} deferred")
        print()

        # Top open areas
        open_specs = [(s["name"], s["counts"].get("open", 0) + s["counts"].get("in-progress", 0))
                      for s in specs if s["counts"].get("open", 0) + s["counts"].get("in-progress", 0) > 0]
        open_specs.sort(key=lambda x: -x[1])

        if open_specs:
            print("Active Work:")
            for name, count in open_specs[:8]:
                print(f"  {name}: {count} open")
            if len(open_specs) > 8:
                print(f"  ... and {len(open_specs) - 8} more")
            print()

    # Commands
    print("Commands:")
    print("  specgraph open                  # See all open tickets")
    print("  specgraph ls <spec>             # Tickets for a spec")
    print("  specgraph show <spec>/<ticket>  # Ticket details")
    print("  specgraph dashboard             # Visual progress overview")
    print("  specgraph help                  # Full command reference")

    return 0


# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------

def cmd_help(args):
    """Show detailed help with command groups."""
    help_text = """
specgraph - Spec-driven traceability CLI

TICKET MANAGEMENT
  ls                  List all specs with ticket counts
  ls <spec>           List tickets for a specific spec
  ls -s open          Filter by status (open/in-progress/closed/deferred)
  defer <ticket> -r "reason"   Defer a ticket as future/someday
  deferred             List all deferred tickets
  show <spec>/<id>    Show ticket details
  summary             Overall completion summary
  dashboard           Visual dashboard with progress bars
  open                List all open tickets
  close <spec>/<id>   Close a ticket as implemented
    -c <path>         Add code link (repeatable)
    -t <path>         Add test link (repeatable)

SPEC MANAGEMENT
  scaffold <spec>     Create tickets from spec sections with {#anchor}
  scaffold --all      Scaffold all specs
  status              Show status for all specs (from metadata tables)
  status <spec>       Show status for one spec
  queue               Show spec queue vs complete counts
  next                Show next spec to process from queue
  complete <spec>     Move spec from queue to complete/

GRAPH TRAVERSAL
  trace <path>        Reverse lookup: what spec/ticket covers this code?
  graph <spec>        Show full graph: spec -> tickets -> code/tests
  graph -r <path>     Reverse: find all specs that touch this directory
  related <spec>      Show specs that link to/from this spec

VALIDATION & AUDIT
  audit               Audit code coverage and find orphan code
  audit -v            Show all linked paths
  orphans             List orphan directories (code not linked from specs)
  orphans -m 3        Only show dirs with 3+ orphan files
  match               Suggest matches between orphans and open tickets
  match -s            Output as copy-paste script
  validate            Validate all [[type:id]] links resolve to files
  gaps                Show specs that have no tickets
  prune               Remove non-actionable tickets (overview, summary)
  prune -n            Dry run - show what would be deleted

USE CASE TRACKING
  uc ls               List all use cases with completion + bench pass rate
  uc show <id>        Show requirements, demand coverage, metrics, benchmarks
  uc gaps <id>        Show only missing requirements for a use case
  uc new <name>       Create a new use case from template

ROADMAP
  roadmap             All milestones with requirements + ticket status
  roadmap <id>        Detail view for one milestone
  roadmap -d          External deadlines sorted by date

FILTERING (on specgraph ls)
  ls --milestone <id> Tickets assigned to a milestone
  ls --service <name> Tickets touching a service
  ls --domain <name>  Tickets in a domain
  ls --demand UC-XXX  Tickets serving a use case (via demand: frontmatter)

CRM (Contact Management)
  crm ls               List all contacts sorted by tier
  crm ls --status warm Filter by status (active/warm/cold/pending-intro)
  crm ls --category investor  Filter by category
  crm ls --tier 1      Filter by tier
  crm show <name>      Show full contact details
  crm new <name>       Create a new contact from template
  crm follow-ups       Show contacts with pending next_action

BENCHMARKS
  bench ls             List all benchmarks with status
  bench run [id]       Run benchmarks (all or specific)
  bench status         Show latest pass/fail for all
  bench compare <id>   Detailed metrics for one benchmark

AGENT ONBOARDING
  init                 Print project overview and reading order

WORKFLOW: NEW SPEC
  1. specgraph next                           # See next spec to process
  2. specgraph scaffold <spec>                # Create tickets from spec
  3. specgraph ls <spec> --status=open        # See what needs doing
  4. specgraph close <spec>/<id> -c path      # Close tickets with code links
  5. specgraph audit                           # Check coverage
  6. specgraph complete <spec>                 # Move spec to complete/

WORKFLOW: LINK ORPHAN CODE
  1. specgraph orphans --min-files 3          # See what's orphaned
  2. specgraph match                          # Get suggested matches
  3. specgraph match -s -g                    # Get copy-paste commands
  4. specgraph close <spec>/<id> -c path      # Run the commands you agree with
  5. specgraph audit                          # Verify improvement

WORKFLOW: INVESTIGATE CODE
  1. specgraph trace path/to/file.py          # What ticket covers this file?
  2. specgraph graph layer-management         # See full spec graph
  3. specgraph graph -r src/                  # What specs touch src/?
  4. specgraph related layer-management       # What specs link to this one?
"""
    print(help_text)
    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Spec-driven traceability CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # init command
    subparsers.add_parser("init", help="Print project overview for agent onboarding")

    # ls command
    ls_parser = subparsers.add_parser("ls", help="List specs or tickets")
    ls_parser.add_argument("spec", nargs="?", help="Spec name to list tickets for")
    ls_parser.add_argument("--status", "-s", choices=["open", "in-progress", "closed", "deferred"],
                          help="Filter by status")
    ls_parser.add_argument("--service", help="Filter by service (e.g., orchestrator)")
    ls_parser.add_argument("--domain", help="Filter by domain (e.g., geo)")
    ls_parser.add_argument("--milestone", help="Filter by milestone (e.g., cross-cutting-ops)")
    ls_parser.add_argument("--demand", help="Filter by use case demand (e.g., UC-004)")

    # show command
    show_parser = subparsers.add_parser("show", help="Show ticket details")
    show_parser.add_argument("ticket", help="Ticket ID (spec/ticket)")

    # summary command
    subparsers.add_parser("summary", help="Show overall summary")

    # dashboard command
    subparsers.add_parser("dashboard", help="Show dashboard with per-spec status and progress bars")

    # open command
    subparsers.add_parser("open", help="List all open tickets")

    # gaps command
    subparsers.add_parser("gaps", help="Show specs without tickets")

    # scaffold command
    scaffold_parser = subparsers.add_parser("scaffold", help="Create tickets from spec sections")
    scaffold_parser.add_argument("spec", nargs="?", help="Spec name (e.g., WORKSPACE or workspace)")
    scaffold_parser.add_argument("--all", "-a", action="store_true", help="Scaffold all specs")
    scaffold_parser.add_argument("--dry-run", "-n", action="store_true", help="Show what would be created")

    # close command
    close_parser = subparsers.add_parser("close", help="Close a ticket (mark as implemented)")
    close_parser.add_argument("ticket", help="Ticket ID (spec/ticket)")
    close_parser.add_argument("--code", "-c", action="append", help="Add code link (can be repeated)")
    close_parser.add_argument("--test", "-t", action="append", help="Add test link (can be repeated)")

    # defer command
    defer_parser = subparsers.add_parser("defer", help="Defer a ticket (mark as future/someday)")
    defer_parser.add_argument("ticket", help="Ticket ID (spec/ticket)")
    defer_parser.add_argument("--reason", "-r", help="Reason for deferral")

    # deferred command
    subparsers.add_parser("deferred", help="List all deferred tickets")

    # audit command
    audit_parser = subparsers.add_parser("audit", help="Audit code coverage and find orphans")
    audit_parser.add_argument("--verbose", "-v", action="store_true", help="Show detailed paths")

    # orphans command
    orphans_parser = subparsers.add_parser("orphans", help="List orphan code directories")
    orphans_parser.add_argument("--min-files", "-m", type=int, default=1, help="Minimum files to show (default: 1)")

    # match command
    match_parser = subparsers.add_parser("match", help="Suggest matches between orphans and open tickets")
    match_parser.add_argument("--min-files", "-m", type=int, default=3, help="Minimum files to consider (default: 3)")
    match_parser.add_argument("--script", "-s", action="store_true", help="Output copy-paste script")
    match_parser.add_argument("--group", "-g", action="store_true", help="Group paths by ticket in script output")

    # prune command
    prune_parser = subparsers.add_parser("prune", help="Remove non-actionable tickets (overview, summary, etc.)")
    prune_parser.add_argument("--dry-run", "-n", action="store_true", help="Show what would be deleted")

    # status command
    status_parser = subparsers.add_parser("status", help="Show status for specs/sections")
    status_parser.add_argument("spec", nargs="?", help="Spec name to show status for")
    status_parser.add_argument("--verbose", "-v", action="store_true", help="Show code/test links")

    # validate command
    subparsers.add_parser("validate", help="Validate all links resolve")

    # queue command
    subparsers.add_parser("queue", help="Show spec queue status")

    # next command
    subparsers.add_parser("next", help="Show next spec to process")

    # complete command
    complete_parser = subparsers.add_parser("complete", help="Move spec from queue to complete")
    complete_parser.add_argument("spec", help="Spec name to mark complete")

    # help command
    subparsers.add_parser("help", help="Show detailed help with examples")

    # trace command
    trace_parser = subparsers.add_parser("trace", help="Reverse lookup: what spec/ticket covers this code?")
    trace_parser.add_argument("path", help="Code file or directory path to look up")

    # graph command
    graph_parser = subparsers.add_parser("graph", help="Show full graph for a spec")
    graph_parser.add_argument("spec", nargs="?", help="Spec name to show graph for")
    graph_parser.add_argument("--reverse", "-r", metavar="PATH", help="Reverse: find specs touching this directory")

    # related command
    related_parser = subparsers.add_parser("related", help="Show specs that link to/from this spec")
    related_parser.add_argument("spec", help="Spec name to find relationships for")

    # coverage command
    coverage_parser = subparsers.add_parser("coverage", help="Show which specs have impl ticket coverage")
    coverage_parser.add_argument("-v", "--verbose", action="store_true", help="Show ticket details")

    # specs command
    subparsers.add_parser("specs", help="List all spec files")

    # completeness command
    completeness_parser = subparsers.add_parser("completeness", help="Check section-level completeness for a spec")
    completeness_parser.add_argument("spec", help="Spec name (e.g., 'VIEWS' or 'views')")
    completeness_parser.add_argument("-v", "--verbose", action="store_true", help="Show detailed coverage")

    # uc command
    uc_parser = subparsers.add_parser("uc", help="Use case tracking")
    uc_subparsers = uc_parser.add_subparsers(dest="uc_command")
    uc_subparsers.add_parser("ls", help="List all use cases with completion")
    uc_show_parser = uc_subparsers.add_parser("show", help="Show one use case with requirement status")
    uc_show_parser.add_argument("uc_id", help="Use case ID (e.g., UC-001) or filename")
    uc_gaps_parser = uc_subparsers.add_parser("gaps", help="Show only missing requirements")
    uc_gaps_parser.add_argument("uc_id", help="Use case ID (e.g., UC-001) or filename")
    uc_new_parser = uc_subparsers.add_parser("new", help="Create a new use case from template")
    uc_new_parser.add_argument("name", help="Use case name (e.g., 'wildfire-risk')")
    uc_new_parser.add_argument("-t", "--title", help="Human-readable title")
    uc_new_parser.add_argument("-p", "--priority", help="Priority (P1/P2/P3, default: P2)")
    uc_new_parser.add_argument("-f", "--force", action="store_true", help="Overwrite if exists")

    # roadmap command
    roadmap_parser = subparsers.add_parser("roadmap", help="Show roadmap milestones with status")
    roadmap_parser.add_argument("milestone_id", nargs="?", help="Milestone ID")
    roadmap_parser.add_argument("--deadlines", "-d", action="store_true", help="Show external deadlines")

    # bench command
    bench_parser = subparsers.add_parser("bench", help="Benchmark management")
    bench_subparsers = bench_parser.add_subparsers(dest="bench_command")
    bench_subparsers.add_parser("ls", help="List all benchmarks with status")
    bench_run_parser = bench_subparsers.add_parser("run", help="Run benchmarks")
    bench_run_parser.add_argument("bench_id", nargs="?", help="Benchmark ID")
    bench_run_parser.add_argument("--milestone", "-m", help="Run benchmarks for a milestone")
    bench_run_parser.add_argument("--uc", help="Run benchmarks for a use case")
    bench_subparsers.add_parser("status", help="Show latest pass/fail for all benchmarks")
    bench_compare_parser = bench_subparsers.add_parser("compare", help="Detailed metrics for one benchmark")
    bench_compare_parser.add_argument("bench_id", help="Benchmark ID")

    # new command
    new_parser = subparsers.add_parser("new", help="Create a new impl ticket from template")
    new_parser.add_argument("name", help="Ticket name (e.g., 'layer-styling')")
    new_parser.add_argument("-t", "--title", help="Human-readable title")
    new_parser.add_argument("-f", "--force", action="store_true", help="Overwrite if exists")

    # crm command
    crm_parser = subparsers.add_parser("crm", help="Contact relationship management")
    crm_subparsers = crm_parser.add_subparsers(dest="crm_command")
    crm_ls_parser = crm_subparsers.add_parser("ls", help="List all contacts")
    crm_ls_parser.add_argument("--status", "-s", help="Filter by status")
    crm_ls_parser.add_argument("--category", "-c", help="Filter by category")
    crm_ls_parser.add_argument("--tier", "-T", help="Filter by tier")
    crm_show_parser = crm_subparsers.add_parser("show", help="Show full contact details")
    crm_show_parser.add_argument("contact_name", help="Contact name or slug")
    crm_new_parser = crm_subparsers.add_parser("new", help="Create a new contact from template")
    crm_new_parser.add_argument("name", help="Contact name (e.g., 'jane-doe')")
    crm_new_parser.add_argument("-f", "--force", action="store_true", help="Overwrite if exists")
    crm_subparsers.add_parser("follow-ups", help="Show contacts with pending next_action")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    # Find project root and initialize paths
    project_root = find_project_root()
    if project_root is None:
        # Fallback: try to find .tickets dir (backwards compat)
        cwd = Path.cwd()
        for parent in [cwd] + list(cwd.parents):
            if (parent / ".tickets").exists():
                project_root = parent
                break

    if project_root is None:
        print(f"Error: no {CONFIG_FILE} found (searched from cwd to /)")
        print(f"Create one with: specgraph help")
        return 1

    config = load_config(project_root)
    _init_paths(project_root, config)

    commands = {
        "init": cmd_init,
        "ls": cmd_ls,
        "show": cmd_show,
        "summary": cmd_summary,
        "dashboard": cmd_dashboard,
        "open": cmd_open,
        "gaps": cmd_gaps,
        "scaffold": cmd_scaffold,
        "close": cmd_close,
        "defer": cmd_defer,
        "deferred": cmd_deferred,
        "audit": cmd_audit,
        "orphans": cmd_orphans,
        "match": cmd_match,
        "prune": cmd_prune,
        "status": cmd_status,
        "validate": cmd_validate,
        "queue": cmd_queue,
        "next": cmd_next,
        "complete": cmd_complete,
        "help": cmd_help,
        "trace": cmd_trace,
        "graph": cmd_graph,
        "related": cmd_related,
        "coverage": cmd_coverage,
        "specs": cmd_specs,
        "completeness": cmd_completeness,
        "new": cmd_new,
        "roadmap": cmd_roadmap,
        "uc": cmd_uc,
        "bench": cmd_bench,
        "crm": cmd_crm,
    }

    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main() or 0)
