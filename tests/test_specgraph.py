"""Tests for specgraph CLI.

All tests use temporary directories with specgraph.yaml configs,
so they're fully isolated from any real project.
"""
import os
import sys
import textwrap
import shutil
from pathlib import Path
from io import StringIO
from contextlib import contextmanager
from unittest import TestCase

# Import the module under test
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
import specgraph


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@contextmanager
def chdir(path):
    """Temporarily change working directory."""
    old = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


@contextmanager
def capture():
    """Capture stdout and stderr."""
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = StringIO()
    sys.stderr = StringIO()
    try:
        yield sys.stdout, sys.stderr
    finally:
        sys.stdout, sys.stderr = old_out, old_err


def write_file(path: Path, content: str):
    """Write a file, creating parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content))


def make_project(tmp: Path, extra_config: str = ""):
    """Create a minimal specgraph project in tmp."""
    write_file(tmp / "specgraph.yaml", f"""\
        tickets_dir: .tickets
        specs_dir: docs/spec
        decisions_dir: docs/decisions
        use_cases_dir: docs/use_cases
        contacts_dir: docs/contacts
        benchmarks_dir: benchmarks
        roadmap_file: docs/ROADMAP.md
        template: .tickets/TEMPLATE.md
        {extra_config}
    """)
    write_file(tmp / ".tickets" / "TEMPLATE.md", """\
        ---
        id: <feature-name>
        status: open
        priority: 2
        created: <DATE>
        ---

        # <Feature Title>
    """)
    (tmp / ".tickets" / "impl").mkdir(parents=True, exist_ok=True)
    (tmp / "docs" / "spec").mkdir(parents=True, exist_ok=True)
    (tmp / "docs" / "decisions").mkdir(parents=True, exist_ok=True)


def init_paths(tmp: Path):
    """Find project root and initialize specgraph paths."""
    config = specgraph.load_config(tmp)
    specgraph._init_paths(tmp, config)


def run_cmd(tmp: Path, argv: list[str]) -> tuple[int, str, str]:
    """Run a specgraph command and return (exit_code, stdout, stderr)."""
    with chdir(tmp):
        init_paths(tmp)
        old_argv = sys.argv
        sys.argv = ["specgraph"] + argv
        with capture() as (out, err):
            try:
                code = specgraph.main()
            except SystemExit as e:
                code = e.code
        sys.argv = old_argv
        return (code or 0, out.getvalue(), err.getvalue())


# ---------------------------------------------------------------------------
# Config & Discovery
# ---------------------------------------------------------------------------

class TestConfig(TestCase):
    def setUp(self):
        self.tmp = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "specgraph_test_config"
        if self.tmp.exists():
            shutil.rmtree(self.tmp)
        self.tmp.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_find_project_root(self):
        make_project(self.tmp)
        sub = self.tmp / "a" / "b" / "c"
        sub.mkdir(parents=True)
        with chdir(sub):
            root = specgraph.find_project_root()
            # Resolve both to handle /tmp -> /private/tmp symlinks
            self.assertEqual(root.resolve(), self.tmp.resolve())

    def test_find_project_root_none(self):
        # Empty dir with no config
        with chdir(self.tmp):
            root = specgraph.find_project_root()
            # May find a parent project — just check it doesn't crash
            # In an isolated environment, this would be None

    def test_load_config_defaults(self):
        write_file(self.tmp / "specgraph.yaml", """\
            tickets_dir: .tickets
            specs_dir: specs
        """)
        config = specgraph.load_config(self.tmp)
        self.assertEqual(config["tickets_dir"], ".tickets")
        self.assertEqual(config["specs_dir"], "specs")
        # Defaults filled in
        self.assertEqual(config["use_cases_dir"], "docs/use_cases")

    def test_parse_config_yaml(self):
        write_file(self.tmp / "specgraph.yaml", """\
            name: test
            items:
              - one
              - two
              - three
        """)
        result = specgraph.parse_config_yaml(self.tmp / "specgraph.yaml")
        self.assertEqual(result["name"], "test")
        self.assertEqual(result["items"], ["one", "two", "three"])

    def test_parse_dir_spec(self):
        path, patterns = specgraph.parse_dir_spec("src:*.py,*.pyi")
        self.assertEqual(path, "src")
        self.assertEqual(patterns, ["*.py", "*.pyi"])

    def test_parse_dir_spec_no_pattern(self):
        path, patterns = specgraph.parse_dir_spec("lib")
        self.assertEqual(path, "lib")
        self.assertEqual(patterns, ["*.py"])


# ---------------------------------------------------------------------------
# Frontmatter Parsing
# ---------------------------------------------------------------------------

class TestFrontmatter(TestCase):
    def setUp(self):
        self.tmp = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "specgraph_test_fm"
        if self.tmp.exists():
            shutil.rmtree(self.tmp)
        self.tmp.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_parse_frontmatter(self):
        write_file(self.tmp / "test.md", """\
            ---
            id: my-ticket
            status: open
            priority: 1
            ---

            # My Ticket
        """)
        fm = specgraph.parse_frontmatter(self.tmp / "test.md")
        self.assertEqual(fm["id"], "my-ticket")
        self.assertEqual(fm["status"], "open")
        self.assertEqual(fm["priority"], "1")

    def test_parse_frontmatter_with_list(self):
        write_file(self.tmp / "test.md", """\
            ---
            id: test
            deps:
              - foo/bar
              - baz/qux
            ---

            # Test
        """)
        fm = specgraph.parse_frontmatter(self.tmp / "test.md")
        self.assertEqual(fm["deps"], ["foo/bar", "baz/qux"])

    def test_parse_frontmatter_empty(self):
        write_file(self.tmp / "test.md", "# No Frontmatter\n")
        fm = specgraph.parse_frontmatter(self.tmp / "test.md")
        self.assertEqual(fm, {})

    def test_parse_uc_frontmatter_requires(self):
        write_file(self.tmp / "uc.md", """\
            ---
            id: UC-001
            title: "Test Use Case"
            status: research
            requires:
              ops:
                - geo:slope
                - geo:clip
              connectors:
                - stac
            ---

            # UC-001: Test
        """)
        fm = specgraph.parse_uc_frontmatter(self.tmp / "uc.md")
        self.assertEqual(fm["id"], "UC-001")
        self.assertEqual(fm["requires"]["ops"], ["geo:slope", "geo:clip"])
        self.assertEqual(fm["requires"]["connectors"], ["stac"])

    def test_get_title(self):
        write_file(self.tmp / "test.md", """\
            ---
            id: test
            ---

            # My Great Title

            Some content.
        """)
        title = specgraph.get_title(self.tmp / "test.md")
        self.assertEqual(title, "My Great Title")


# ---------------------------------------------------------------------------
# Link Parsing
# ---------------------------------------------------------------------------

class TestLinks(TestCase):
    def test_extract_links(self):
        text = "See [[spec:layers#schema]] and [[adr:0005]] for details."
        links = specgraph.extract_links(text)
        self.assertEqual(links, [("spec", "layers#schema"), ("adr", "0005")])

    def test_extract_links_empty(self):
        links = specgraph.extract_links("No links here.")
        self.assertEqual(links, [])

    def test_path_matches_prefix(self):
        self.assertTrue(specgraph._path_matches("src/auth", "src/auth/login.py"))
        self.assertTrue(specgraph._path_matches("src/auth/login.py", "src/auth"))

    def test_path_matches_no_match(self):
        self.assertFalse(specgraph._path_matches("src/auth", "lib/utils"))

    def test_path_matches_wiki_link(self):
        self.assertFalse(specgraph._path_matches("[[spec:foo]]", "src/foo"))


# ---------------------------------------------------------------------------
# Ticket Management
# ---------------------------------------------------------------------------

class TestTickets(TestCase):
    def setUp(self):
        self.tmp = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "specgraph_test_tickets"
        if self.tmp.exists():
            shutil.rmtree(self.tmp)
        self.tmp.mkdir(parents=True)
        make_project(self.tmp)
        init_paths(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_ticket_dir(self, name, readme_status="open", tickets=None):
        """Create a ticket directory with README and optional sub-tickets."""
        d = self.tmp / ".tickets" / "impl" / name
        d.mkdir(parents=True, exist_ok=True)
        write_file(d / "README.md", f"""\
            ---
            id: {name}
            status: {readme_status}
            priority: 1
            ---

            # {name.replace('-', ' ').title()}
        """)
        for tname, tstatus in (tickets or []):
            write_file(d / f"{tname}.md", f"""\
                ---
                id: {name}/{tname}
                status: {tstatus}
                priority: 2
                code: []
                tests: []
                deps: []
                ---

                # {tname}
            """)

    def test_list_specs_empty(self):
        specs = specgraph.list_specs()
        self.assertEqual(specs, [])

    def test_list_specs(self):
        self._make_ticket_dir("auth", tickets=[
            ("login", "closed"),
            ("signup", "open"),
        ])
        specs = specgraph.list_specs()
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0]["name"], "auth")
        self.assertEqual(specs[0]["counts"]["closed"], 1)
        self.assertEqual(specs[0]["counts"]["open"], 1)

    def test_cmd_ls(self):
        self._make_ticket_dir("auth", tickets=[("login", "closed")])
        code, out, _ = run_cmd(self.tmp, ["ls"])
        self.assertEqual(code, 0)
        self.assertIn("auth", out)

    def test_cmd_ls_specific_spec(self):
        self._make_ticket_dir("auth", tickets=[
            ("login", "closed"),
            ("signup", "open"),
        ])
        code, out, _ = run_cmd(self.tmp, ["ls", "auth"])
        self.assertEqual(code, 0)
        self.assertIn("login", out)
        self.assertIn("signup", out)

    def test_cmd_ls_not_found(self):
        self._make_ticket_dir("auth")
        code, out, _ = run_cmd(self.tmp, ["ls", "nonexistent"])
        self.assertEqual(code, 1)
        self.assertIn("not found", out.lower())

    def test_cmd_show(self):
        self._make_ticket_dir("auth", tickets=[("login", "open")])
        code, out, _ = run_cmd(self.tmp, ["show", "auth/login"])
        self.assertEqual(code, 0)
        self.assertIn("login", out)
        self.assertIn("open", out)

    def test_cmd_open(self):
        self._make_ticket_dir("auth", tickets=[
            ("login", "closed"),
            ("signup", "open"),
        ])
        code, out, _ = run_cmd(self.tmp, ["open"])
        self.assertEqual(code, 0)
        self.assertIn("signup", out)
        self.assertNotIn("login", out)

    def test_cmd_close(self):
        self._make_ticket_dir("auth", tickets=[("login", "open")])
        code, out, _ = run_cmd(self.tmp, ["close", "auth/login", "-c", "src/auth.py"])
        self.assertEqual(code, 0)
        self.assertIn("Closed", out)

        # Verify the file was updated
        content = (self.tmp / ".tickets" / "impl" / "auth" / "login.md").read_text()
        self.assertIn("status: closed", content)
        self.assertIn("src/auth.py", content)

    def test_cmd_close_with_tests(self):
        self._make_ticket_dir("auth", tickets=[("login", "open")])
        code, out, _ = run_cmd(self.tmp, ["close", "auth/login", "-c", "src/auth.py", "-t", "tests/test_auth.py"])
        self.assertEqual(code, 0)

        content = (self.tmp / ".tickets" / "impl" / "auth" / "login.md").read_text()
        self.assertIn("status: closed", content)
        self.assertIn("src/auth.py", content)
        self.assertIn("tests/test_auth.py", content)

    def test_cmd_defer(self):
        self._make_ticket_dir("auth", tickets=[("login", "open")])
        code, out, _ = run_cmd(self.tmp, ["defer", "auth/login", "-r", "Not needed yet"])
        self.assertEqual(code, 0)

        content = (self.tmp / ".tickets" / "impl" / "auth" / "login.md").read_text()
        self.assertIn("status: deferred", content)
        self.assertIn("Not needed yet", content)

    def test_cmd_summary(self):
        self._make_ticket_dir("auth", tickets=[
            ("login", "closed"),
            ("signup", "open"),
        ])
        code, out, _ = run_cmd(self.tmp, ["summary"])
        self.assertEqual(code, 0)
        self.assertIn("Specs with tickets: 1", out)
        self.assertIn("Total tickets:      2", out)

    def test_cmd_new(self):
        code, out, _ = run_cmd(self.tmp, ["new", "my-feature", "-t", "My Feature"])
        self.assertEqual(code, 0)
        self.assertIn("Created", out)
        readme = self.tmp / ".tickets" / "impl" / "my-feature" / "README.md"
        self.assertTrue(readme.exists())

    def test_cmd_new_already_exists(self):
        self._make_ticket_dir("auth")
        code, out, _ = run_cmd(self.tmp, ["new", "auth"])
        self.assertEqual(code, 1)
        self.assertIn("already exists", out)


# ---------------------------------------------------------------------------
# Spec Management
# ---------------------------------------------------------------------------

class TestSpecs(TestCase):
    def setUp(self):
        self.tmp = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "specgraph_test_specs"
        if self.tmp.exists():
            shutil.rmtree(self.tmp)
        self.tmp.mkdir(parents=True)
        make_project(self.tmp)
        init_paths(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_spec(self, name, sections=None):
        """Create a spec file with optional {#anchor} sections."""
        content = f"""\
---
spec: {name.lower().replace('_', '-')}
---

# {name.replace('_', ' ').title()}
"""
        for title, anchor in (sections or []):
            content += f"\n## {title} {{#{anchor}}}\n\nSome content.\n"

        write_file(self.tmp / "docs" / "spec" / f"{name}.md", content)

    def test_cmd_specs(self):
        self._make_spec("AUTH")
        self._make_spec("LAYERS")
        code, out, _ = run_cmd(self.tmp, ["specs"])
        self.assertEqual(code, 0)
        self.assertIn("AUTH", out)
        self.assertIn("LAYERS", out)
        self.assertIn("2 files", out)

    def test_parse_spec_sections(self):
        self._make_spec("AUTH", sections=[
            ("Login Flow", "login-flow"),
            ("Token Refresh", "token-refresh"),
        ])
        sections = specgraph.parse_spec_sections(
            self.tmp / "docs" / "spec" / "AUTH.md")
        self.assertEqual(len(sections), 2)
        self.assertEqual(sections[0]["anchor"], "login-flow")
        self.assertEqual(sections[1]["anchor"], "token-refresh")

    def test_cmd_scaffold(self):
        self._make_spec("AUTH", sections=[
            ("Login Flow", "login-flow"),
            ("Token Refresh", "token-refresh"),
        ])
        code, out, _ = run_cmd(self.tmp, ["scaffold", "AUTH"])
        self.assertEqual(code, 0)
        self.assertIn("Created 2 tickets", out)

        ticket_dir = self.tmp / ".tickets" / "impl" / "auth"
        self.assertTrue(ticket_dir.exists())
        self.assertTrue((ticket_dir / "login-flow.md").exists())
        self.assertTrue((ticket_dir / "token-refresh.md").exists())
        self.assertTrue((ticket_dir / "README.md").exists())

    def test_cmd_scaffold_skips_overview(self):
        self._make_spec("AUTH", sections=[
            ("Overview", "overview"),
            ("Login Flow", "login-flow"),
        ])
        code, out, _ = run_cmd(self.tmp, ["scaffold", "AUTH"])
        self.assertEqual(code, 0)
        self.assertIn("Created 1 tickets", out)

        ticket_dir = self.tmp / ".tickets" / "impl" / "auth"
        self.assertFalse((ticket_dir / "overview.md").exists())
        self.assertTrue((ticket_dir / "login-flow.md").exists())

    def test_cmd_scaffold_dry_run(self):
        self._make_spec("AUTH", sections=[("Login", "login")])
        code, out, _ = run_cmd(self.tmp, ["scaffold", "AUTH", "--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn("Would create", out)
        self.assertFalse((self.tmp / ".tickets" / "impl" / "auth").exists())


# ---------------------------------------------------------------------------
# Graph Traversal
# ---------------------------------------------------------------------------

class TestGraph(TestCase):
    def setUp(self):
        self.tmp = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "specgraph_test_graph"
        if self.tmp.exists():
            shutil.rmtree(self.tmp)
        self.tmp.mkdir(parents=True)
        make_project(self.tmp)
        init_paths(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_ticket(self, spec, ticket, status="closed", code=None, tests=None):
        d = self.tmp / ".tickets" / "impl" / spec
        d.mkdir(parents=True, exist_ok=True)
        if not (d / "README.md").exists():
            write_file(d / "README.md", f"""\
                ---
                id: {spec}
                status: open
                ---

                # {spec}
            """)

        lines = [
            "---",
            f"id: {spec}/{ticket}",
            f"status: {status}",
        ]
        if code:
            lines.append("code:")
            for c in code:
                lines.append(f'  - "{c}"')
        else:
            lines.append("code: []")
        if tests:
            lines.append("tests:")
            for t in tests:
                lines.append(f'  - "{t}"')
        else:
            lines.append("tests: []")
        lines.extend(["deps: []", "---", "", f"# {ticket}", ""])

        path = d / f"{ticket}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines))

    def test_cmd_trace(self):
        self._make_ticket("auth", "login", code=["src/auth/login.py"])
        code, out, _ = run_cmd(self.tmp, ["trace", "src/auth/login.py"])
        self.assertEqual(code, 0)
        self.assertIn("auth", out)
        self.assertIn("login", out)

    def test_cmd_trace_not_found(self):
        self._make_ticket("auth", "login", code=["src/auth/login.py"])
        code, out, _ = run_cmd(self.tmp, ["trace", "src/unknown.py"])
        self.assertEqual(code, 1)
        self.assertIn("No coverage", out)

    def test_cmd_graph(self):
        self._make_ticket("auth", "login", status="closed", code=["src/auth.py"])
        self._make_ticket("auth", "signup", status="open")
        code, out, _ = run_cmd(self.tmp, ["graph", "auth"])
        self.assertEqual(code, 0)
        self.assertIn("auth (spec)", out)
        self.assertIn("login", out)
        self.assertIn("signup", out)
        self.assertIn("[closed]", out)
        self.assertIn("[open]", out)


# ---------------------------------------------------------------------------
# Spec Relations
# ---------------------------------------------------------------------------

class TestRelated(TestCase):
    def setUp(self):
        self.tmp = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "specgraph_test_related"
        if self.tmp.exists():
            shutil.rmtree(self.tmp)
        self.tmp.mkdir(parents=True)
        make_project(self.tmp)
        init_paths(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_cmd_related(self):
        write_file(self.tmp / "docs" / "spec" / "AUTH.md", """\
            ---
            spec: auth
            ---

            # Auth

            Depends on [[spec:layers]].
        """)
        write_file(self.tmp / "docs" / "spec" / "LAYERS.md", """\
            ---
            spec: layers
            ---

            # Layers
        """)
        code, out, _ = run_cmd(self.tmp, ["related", "layers"])
        self.assertEqual(code, 0)
        self.assertIn("auth", out)
        self.assertIn("INCOMING", out)


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------

class TestValidate(TestCase):
    def setUp(self):
        self.tmp = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "specgraph_test_validate"
        if self.tmp.exists():
            shutil.rmtree(self.tmp)
        self.tmp.mkdir(parents=True)
        make_project(self.tmp)
        init_paths(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_validate_all_good(self):
        # No specs with links = nothing to break
        code, out, _ = run_cmd(self.tmp, ["validate"])
        self.assertEqual(code, 0)
        self.assertIn("All links resolve", out)


# ---------------------------------------------------------------------------
# Use Cases
# ---------------------------------------------------------------------------

class TestUseCases(TestCase):
    def setUp(self):
        self.tmp = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "specgraph_test_uc"
        if self.tmp.exists():
            shutil.rmtree(self.tmp)
        self.tmp.mkdir(parents=True)
        make_project(self.tmp)
        init_paths(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_cmd_uc_new(self):
        code, out, _ = run_cmd(self.tmp, ["uc", "new", "climate-risk", "-t", "Climate Risk"])
        self.assertEqual(code, 0)
        self.assertIn("UC-001", out)
        uc_file = self.tmp / "docs" / "use_cases" / "climate-risk.md"
        self.assertTrue(uc_file.exists())
        content = uc_file.read_text()
        self.assertIn("UC-001", content)
        self.assertIn("Climate Risk", content)

    def test_cmd_uc_new_increments_id(self):
        run_cmd(self.tmp, ["uc", "new", "first"])
        code, out, _ = run_cmd(self.tmp, ["uc", "new", "second"])
        self.assertEqual(code, 0)
        self.assertIn("UC-002", out)

    def test_cmd_uc_ls(self):
        run_cmd(self.tmp, ["uc", "new", "climate-risk", "-t", "Climate Risk"])
        code, out, _ = run_cmd(self.tmp, ["uc", "ls"])
        self.assertEqual(code, 0)
        self.assertIn("UC-001", out)
        self.assertIn("Climate Risk", out)


# ---------------------------------------------------------------------------
# CRM
# ---------------------------------------------------------------------------

class TestCRM(TestCase):
    def setUp(self):
        self.tmp = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "specgraph_test_crm"
        if self.tmp.exists():
            shutil.rmtree(self.tmp)
        self.tmp.mkdir(parents=True)
        make_project(self.tmp)
        init_paths(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_cmd_crm_new(self):
        code, out, _ = run_cmd(self.tmp, ["crm", "new", "jane-doe"])
        self.assertEqual(code, 0)
        self.assertIn("Created", out)
        self.assertTrue((self.tmp / "docs" / "contacts" / "jane-doe.md").exists())

    def test_cmd_crm_ls(self):
        write_file(self.tmp / "docs" / "contacts" / "jane-doe.md", """\
            ---
            name: "Jane Doe"
            org: "Acme Corp"
            category: "researcher"
            status: "active"
            tier: 1
            ---

            ## Notes
        """)
        code, out, _ = run_cmd(self.tmp, ["crm", "ls"])
        self.assertEqual(code, 0)
        self.assertIn("Jane Doe", out)
        self.assertIn("Acme Corp", out)

    def test_cmd_crm_show(self):
        write_file(self.tmp / "docs" / "contacts" / "jane-doe.md", """\
            ---
            name: "Jane Doe"
            org: "Acme Corp"
            role: "Scientist"
            category: "researcher"
            status: "active"
            tier: 1
            ---

            ## Notes

            Met at conference.
        """)
        code, out, _ = run_cmd(self.tmp, ["crm", "show", "jane-doe"])
        self.assertEqual(code, 0)
        self.assertIn("Jane Doe", out)
        self.assertIn("Scientist @ Acme Corp", out)
        self.assertIn("Met at conference", out)

    def test_cmd_crm_follow_ups(self):
        write_file(self.tmp / "docs" / "contacts" / "jane-doe.md", """\
            ---
            name: "Jane Doe"
            tier: 1
            status: "active"
            next_action: "Send demo link"
            ---

            # Notes
        """)
        code, out, _ = run_cmd(self.tmp, ["crm", "follow-ups"])
        self.assertEqual(code, 0)
        self.assertIn("Jane Doe", out)
        self.assertIn("Send demo link", out)


# ---------------------------------------------------------------------------
# Init (Agent Onboarding)
# ---------------------------------------------------------------------------

class TestInit(TestCase):
    def setUp(self):
        self.tmp = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "specgraph_test_init"
        if self.tmp.exists():
            shutil.rmtree(self.tmp)
        self.tmp.mkdir(parents=True)
        make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_cmd_init(self):
        write_file(self.tmp / "CLAUDE.md", "# Constitution\n")
        write_file(self.tmp / "docs" / "spec" / "AUTH.md", "# Auth\n")
        code, out, _ = run_cmd(self.tmp, ["init"])
        self.assertEqual(code, 0)
        self.assertIn("Project:", out)
        self.assertIn("Reading Order:", out)
        self.assertIn("CLAUDE.md", out)
        self.assertIn("1 specs", out)
        self.assertIn("Commands:", out)

    def test_cmd_init_with_tickets(self):
        d = self.tmp / ".tickets" / "impl" / "auth"
        d.mkdir(parents=True)
        write_file(d / "README.md", """\
            ---
            id: auth
            status: open
            ---

            # Auth
        """)
        write_file(d / "login.md", """\
            ---
            id: auth/login
            status: open
            ---

            # Login
        """)
        code, out, _ = run_cmd(self.tmp, ["init"])
        self.assertEqual(code, 0)
        self.assertIn("Tickets:", out)
        self.assertIn("1 open", out)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

class TestDashboard(TestCase):
    def setUp(self):
        self.tmp = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "specgraph_test_dash"
        if self.tmp.exists():
            shutil.rmtree(self.tmp)
        self.tmp.mkdir(parents=True)
        make_project(self.tmp)
        init_paths(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_cmd_dashboard(self):
        d = self.tmp / ".tickets" / "impl" / "auth"
        d.mkdir(parents=True)
        write_file(d / "README.md", "---\nid: auth\nstatus: open\n---\n# Auth\n")
        write_file(d / "login.md", "---\nid: auth/login\nstatus: closed\n---\n# Login\n")
        write_file(d / "signup.md", "---\nid: auth/signup\nstatus: open\n---\n# Signup\n")

        code, out, _ = run_cmd(self.tmp, ["dashboard"])
        self.assertEqual(code, 0)
        self.assertIn("DASHBOARD", out)
        self.assertIn("auth", out)


# ---------------------------------------------------------------------------
# Validators Plugin
# ---------------------------------------------------------------------------

class TestValidators(TestCase):
    def setUp(self):
        self.tmp = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "specgraph_test_validators"
        if self.tmp.exists():
            shutil.rmtree(self.tmp)
        self.tmp.mkdir(parents=True)
        make_project(self.tmp, extra_config='validators_file: specgraph_validators.py')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_load_validators(self):
        write_file(self.tmp / "specgraph_validators.py", """\
            from pathlib import Path

            def check_ops(root: Path, ref: str) -> bool:
                return ref == "geo:slope"

            VALIDATORS = {"ops": check_ops}
        """)
        config = specgraph.load_config(self.tmp)
        validators = specgraph.load_validators(self.tmp, config)
        self.assertIn("ops", validators)
        self.assertTrue(validators["ops"](self.tmp, "geo:slope"))
        self.assertFalse(validators["ops"](self.tmp, "geo:aspect"))

    def test_load_validators_missing_file(self):
        config = specgraph.load_config(self.tmp)
        validators = specgraph.load_validators(self.tmp, config)
        # Should still have built-in tickets validator
        self.assertIn("tickets", validators)

    def test_builtin_ticket_closed_validator(self):
        init_paths(self.tmp)
        d = self.tmp / ".tickets" / "impl" / "my-feature"
        d.mkdir(parents=True)
        write_file(d / "README.md", "---\nid: my-feature\nstatus: closed\n---\n# F\n")
        self.assertTrue(specgraph._check_ticket_closed("my-feature"))
        self.assertFalse(specgraph._check_ticket_closed("nonexistent"))


# ---------------------------------------------------------------------------
# Completeness
# ---------------------------------------------------------------------------

class TestCompleteness(TestCase):
    def setUp(self):
        self.tmp = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "specgraph_test_complete"
        if self.tmp.exists():
            shutil.rmtree(self.tmp)
        self.tmp.mkdir(parents=True)
        make_project(self.tmp)
        init_paths(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_cmd_completeness(self):
        write_file(self.tmp / "docs" / "spec" / "AUTH.md", """\
            ---
            spec: auth
            ---

            # Auth

            ## Login {#login}

            Login flow.

            ## Signup {#signup}

            Signup flow.
        """)
        # Create a ticket that references #login
        d = self.tmp / ".tickets" / "impl" / "auth-impl"
        d.mkdir(parents=True)
        write_file(d / "README.md", """\
            ---
            id: auth-impl
            status: open
            ---

            # Auth Implementation

            Covers [[spec:auth#login]].
        """)

        code, out, _ = run_cmd(self.tmp, ["completeness", "auth"])
        self.assertEqual(code, 0)
        self.assertIn("1/2 sections", out)
        self.assertIn("#login", out)
        self.assertIn("#signup", out)


# ---------------------------------------------------------------------------
# Prune
# ---------------------------------------------------------------------------

class TestPrune(TestCase):
    def setUp(self):
        self.tmp = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "specgraph_test_prune"
        if self.tmp.exists():
            shutil.rmtree(self.tmp)
        self.tmp.mkdir(parents=True)
        make_project(self.tmp)
        init_paths(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_prune_dry_run(self):
        d = self.tmp / ".tickets" / "impl" / "auth"
        d.mkdir(parents=True)
        write_file(d / "README.md", "---\nid: auth\nstatus: open\n---\n# Auth\n")
        write_file(d / "overview.md", "---\nid: auth/overview\nstatus: open\n---\n# Overview\n")
        write_file(d / "login.md", "---\nid: auth/login\nstatus: open\n---\n# Login\n")

        code, out, _ = run_cmd(self.tmp, ["prune", "--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn("overview", out)
        self.assertIn("Dry run", out)
        # File should still exist
        self.assertTrue((d / "overview.md").exists())

    def test_prune_deletes(self):
        d = self.tmp / ".tickets" / "impl" / "auth"
        d.mkdir(parents=True)
        write_file(d / "README.md", "---\nid: auth\nstatus: open\n---\n# Auth\n")
        write_file(d / "overview.md", "---\nid: auth/overview\nstatus: open\n---\n# Overview\n")

        code, out, _ = run_cmd(self.tmp, ["prune"])
        self.assertEqual(code, 0)
        self.assertFalse((d / "overview.md").exists())


# ---------------------------------------------------------------------------
# Drift Detection
# ---------------------------------------------------------------------------

def _git_init(tmp: Path):
    """Initialize a git repo and make an initial commit."""
    import subprocess
    subprocess.run(["git", "init"], cwd=str(tmp), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(tmp), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(tmp), capture_output=True, check=True)


def _git_add_commit(tmp: Path, msg: str, files: list[str] | None = None):
    """Stage files and commit."""
    import subprocess
    if files:
        for f in files:
            subprocess.run(["git", "add", f], cwd=str(tmp), capture_output=True, check=True)
    else:
        subprocess.run(["git", "add", "-A"], cwd=str(tmp), capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", msg, "--allow-empty-message"],
                   cwd=str(tmp), capture_output=True, check=True)


class TestDrift(TestCase):
    def setUp(self):
        self.tmp = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "specgraph_test_drift"
        if self.tmp.exists():
            shutil.rmtree(self.tmp)
        self.tmp.mkdir(parents=True)
        make_project(self.tmp)
        # Overwrite config with code_dirs (extra_config + dedent interaction is fragile)
        write_file(self.tmp / "specgraph.yaml", """\
            tickets_dir: .tickets
            specs_dir: docs/spec
            decisions_dir: docs/decisions
            use_cases_dir: docs/use_cases
            contacts_dir: docs/contacts
            benchmarks_dir: benchmarks
            roadmap_file: docs/ROADMAP.md
            template: .tickets/TEMPLATE.md
            code_dirs:
              - src:*.py
        """)
        _git_init(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_drift_no_specs(self):
        """No spec files → early exit."""
        # Remove the specs dir
        shutil.rmtree(self.tmp / "docs" / "spec")
        _git_add_commit(self.tmp, "init")
        code, out, _ = run_cmd(self.tmp, ["drift"])
        self.assertEqual(code, 1)
        self.assertIn("No specs", out)

    def test_drift_no_code_dirs(self):
        """No code_dirs configured → tells user to add them."""
        # Rewrite config without code_dirs
        write_file(self.tmp / "specgraph.yaml", """\
            tickets_dir: .tickets
            specs_dir: docs/spec
            decisions_dir: docs/decisions
            use_cases_dir: docs/use_cases
            contacts_dir: docs/contacts
            benchmarks_dir: benchmarks
            roadmap_file: docs/ROADMAP.md
            template: .tickets/TEMPLATE.md
        """)
        write_file(self.tmp / "docs" / "spec" / "AUTH.md", """\
            ---
            spec: auth
            ---

            # Auth
        """)
        _git_add_commit(self.tmp, "init")
        code, out, _ = run_cmd(self.tmp, ["drift"])
        self.assertEqual(code, 1)
        self.assertIn("No code_dirs", out)

    def test_drift_no_drift(self):
        """Spec updated after code → no drift."""
        import subprocess, time

        (self.tmp / "src").mkdir(parents=True, exist_ok=True)
        write_file(self.tmp / "src" / "auth.py", "# auth code\n")
        # Ticket linking code to spec
        d = self.tmp / ".tickets" / "impl" / "auth"
        d.mkdir(parents=True, exist_ok=True)
        write_file(d / "README.md", "---\nid: auth\nstatus: open\n---\n# Auth\n")
        write_file(d / "login.md", """\
            ---
            id: auth/login
            status: closed
            code:
              - "src/auth.py"
            links:
              - "docs/spec/AUTH.md"
            ---

            # Login
        """)
        _git_add_commit(self.tmp, "init: code and tickets")

        # Code change — use explicit past timestamp
        t_code = "2020-01-01T00:00:00"
        write_file(self.tmp / "src" / "auth.py", "# auth code v2\n")
        subprocess.run(["git", "add", "src/auth.py"], cwd=str(self.tmp), capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "code change",
             "--date", t_code],
            cwd=str(self.tmp), capture_output=True, check=True,
            env={**os.environ, "GIT_COMMITTER_DATE": t_code},
        )

        # Spec updated AFTER code — use later timestamp
        t_spec = "2020-01-02T00:00:00"
        write_file(self.tmp / "docs" / "spec" / "AUTH.md", """\
            ---
            spec: auth
            ---

            # Auth (updated)
        """)
        subprocess.run(["git", "add", "docs/spec/AUTH.md"], cwd=str(self.tmp), capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "update spec",
             "--date", t_spec],
            cwd=str(self.tmp), capture_output=True, check=True,
            env={**os.environ, "GIT_COMMITTER_DATE": t_spec},
        )

        code, out, _ = run_cmd(self.tmp, ["drift"])
        self.assertEqual(code, 0)
        self.assertIn("NO DRIFT", out)

    def test_drift_detected(self):
        """Code changed after spec → drift detected."""
        import subprocess

        (self.tmp / "src").mkdir(parents=True, exist_ok=True)
        write_file(self.tmp / "docs" / "spec" / "AUTH.md", """\
            ---
            spec: auth
            ---

            # Auth
        """)
        d = self.tmp / ".tickets" / "impl" / "auth"
        d.mkdir(parents=True, exist_ok=True)
        write_file(d / "README.md", "---\nid: auth\nstatus: open\n---\n# Auth\n")
        write_file(d / "login.md", """\
            ---
            id: auth/login
            status: closed
            code:
              - "src/auth.py"
            links:
              - "docs/spec/AUTH.md"
            ---

            # Login
        """)
        write_file(self.tmp / "src" / "auth.py", "# v1\n")
        _git_add_commit(self.tmp, "init")

        # Make code-only commits after the spec
        for i in range(4):
            write_file(self.tmp / "src" / "auth.py", f"# v{i+2}\n")
            subprocess.run(
                ["git", "add", "src/auth.py"],
                cwd=str(self.tmp), capture_output=True, check=True,
            )
            subprocess.run(
                ["git", "commit", "-m", f"code change {i+1}"],
                cwd=str(self.tmp), capture_output=True, check=True,
            )

        code, out, _ = run_cmd(self.tmp, ["drift"])
        self.assertIn("DRIFT DETECTED", out)
        self.assertIn("src", out)

    def test_drift_high_severity(self):
        """10+ code commits after spec → HIGH severity, exit 1."""
        import subprocess

        (self.tmp / "src").mkdir(parents=True, exist_ok=True)
        write_file(self.tmp / "docs" / "spec" / "AUTH.md", """\
            ---
            spec: auth
            ---

            # Auth
        """)
        d = self.tmp / ".tickets" / "impl" / "auth"
        d.mkdir(parents=True, exist_ok=True)
        write_file(d / "README.md", "---\nid: auth\nstatus: open\n---\n# Auth\n")
        write_file(d / "login.md", """\
            ---
            id: auth/login
            status: closed
            code:
              - "src/auth.py"
            links:
              - "docs/spec/AUTH.md"
            ---

            # Login
        """)
        write_file(self.tmp / "src" / "auth.py", "# v1\n")
        _git_add_commit(self.tmp, "init")

        for i in range(12):
            write_file(self.tmp / "src" / "auth.py", f"# v{i+2}\n")
            subprocess.run(
                ["git", "add", "src/auth.py"],
                cwd=str(self.tmp), capture_output=True, check=True,
            )
            subprocess.run(
                ["git", "commit", "-m", f"code change {i+1}"],
                cwd=str(self.tmp), capture_output=True, check=True,
            )

        code, out, _ = run_cmd(self.tmp, ["drift"])
        # 12 code commits + inclusive --since counts initial = 13 → HIGH
        self.assertEqual(code, 1)
        self.assertIn("DRIFT DETECTED", out)
        self.assertIn("HIGH", out)

    def test_drift_verbose(self):
        """--verbose flag shows remediation hints."""
        import subprocess

        (self.tmp / "src").mkdir(parents=True, exist_ok=True)
        write_file(self.tmp / "docs" / "spec" / "AUTH.md", "---\nspec: auth\n---\n# Auth\n")
        d = self.tmp / ".tickets" / "impl" / "auth"
        d.mkdir(parents=True, exist_ok=True)
        write_file(d / "README.md", "---\nid: auth\nstatus: open\n---\n# Auth\n")
        write_file(d / "login.md", "---\nid: auth/login\nstatus: closed\ncode:\n  - \"src/auth.py\"\nlinks:\n  - \"docs/spec/AUTH.md\"\n---\n# Login\n")
        write_file(self.tmp / "src" / "auth.py", "# v1\n")
        _git_add_commit(self.tmp, "init")

        for i in range(3):
            write_file(self.tmp / "src" / "auth.py", f"# v{i+2}\n")
            subprocess.run(["git", "add", "src/auth.py"], cwd=str(self.tmp), capture_output=True, check=True)
            subprocess.run(["git", "commit", "-m", f"change {i+1}"], cwd=str(self.tmp), capture_output=True, check=True)

        code, out, _ = run_cmd(self.tmp, ["drift", "--verbose"])
        self.assertIn("DRIFT DETECTED", out)
        self.assertIn("Review spec", out)
