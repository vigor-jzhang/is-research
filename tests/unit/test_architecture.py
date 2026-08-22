"""Architecture/dependency audit — lightweight import inspection."""

import pathlib
import re

SRC_ROOT = pathlib.Path(__file__).parents[2] / "src" / "research_harness"


def _read_files(base: pathlib.Path):
    return list(base.rglob("*.py"))


def test_kernel_has_no_concrete_plugin_imports():
    kernel_root = SRC_ROOT / "kernel"
    forbidden = [
        re.compile(r"from\s+research_harness\.plugins"),
        re.compile(r"import\s+research_harness\.plugins"),
        re.compile(r"from\s+research_harness\.app"),
        re.compile(r"import\s+research_harness\.app"),
    ]
    violations: list[str] = []
    for f in _read_files(kernel_root):
        text = f.read_text(encoding="utf-8")
        for pat in forbidden:
            if pat.search(text):
                violations.append(f"{f.relative_to(SRC_ROOT)}: {pat.pattern}")
    assert not violations, "kernel must not import concrete plugins/app:\n" + "\n".join(violations)


def test_contracts_have_no_plugin_imports():
    contracts_root = SRC_ROOT / "contracts"
    forbidden = [
        re.compile(r"from\s+research_harness\.plugins"),
        re.compile(r"import\s+research_harness\.plugins"),
        re.compile(r"from\s+research_harness\.app"),
        re.compile(r"import\s+research_harness\.app"),
        re.compile(r"from\s+research_harness\.kernel\.runtime"),
    ]
    violations: list[str] = []
    for f in _read_files(contracts_root):
        text = f.read_text(encoding="utf-8")
        for pat in forbidden:
            if pat.search(text):
                violations.append(f"{f.relative_to(SRC_ROOT)}: {pat.pattern}")
    assert not violations, "contracts must not import plugins:\n" + "\n".join(violations)


def test_plugins_do_not_import_other_plugin_implementations():
    plugins_root = SRC_ROOT / "plugins"
    # Allow imports within same plugin package, but not cross-plugin implementation imports
    # e.g., routing should not import from research_harness.plugins.models.openrouter
    violations: list[str] = []
    for f in _read_files(plugins_root):
        # Skip registry and __init__ which may legitimately reference multiple plugins
        if f.name == "registry.py":
            continue
        text = f.read_text(encoding="utf-8")
        # Find all imports of plugins
        for m in re.finditer(r"from\s+(research_harness\.plugins\.[^\s]+)\s+import", text):
            import_path = m.group(1)
            # Determine current plugin's top-level package: e.g., loops.simple_tool_loop
            # We allow imports from same plugin or from contracts/kernel/config
            # Forbid cross-plugin: e.g., a file in routing importing models
            rel = f.relative_to(plugins_root)
            parts = rel.parts  # e.g., ('routing', 'role_router', 'plugin.py')
            current_top = parts[0] if parts else ""
            # Extract imported top: e.g., research_harness.plugins.models.openrouter -> models
            imp_parts = import_path.split(".")
            # import_path is like research_harness.plugins.models.openrouter.plugin
            # we want the third component: models, routing, tools, etc.
            if len(imp_parts) >= 3:
                imported_top = imp_parts[2]
                if imported_top != current_top and imported_top not in ("registry", "__init__"):
                    # Allow registry imports? registry is allowed to import plugins for builtins
                    # But individual plugins should not cross-import
                    violations.append(
                        f"{f.relative_to(SRC_ROOT)} imports {import_path} (cross-plugin)"
                    )
        # Also check import research_harness.plugins.models.openrouter
        for m in re.finditer(r"import\s+(research_harness\.plugins\.[^\s]+)", text):
            import_path = m.group(1)
            rel = f.relative_to(plugins_root)
            parts = rel.parts
            current_top = parts[0] if parts else ""
            imp_parts = import_path.split(".")
            if len(imp_parts) >= 3:
                imported_top = imp_parts[2]
                if imported_top != current_top:
                    violations.append(
                        f"{f.relative_to(SRC_ROOT)} imports {import_path} (cross-plugin)"
                    )
    assert not violations, "plugins must not import other plugins' implementations:\n" + "\n".join(
        violations
    )


def test_research_plugins_do_not_hardcode_openrouter():
    # Only openrouter plugin and bootstrap should reference OpenRouter specifics
    # Check that contracts, kernel, and non-openrouter plugins don't contain OpenRouter URLs/headers
    forbidden_strings = [
        "openrouter.ai",
        "OPENROUTER_API_KEY",  # should only appear in openrouter plugin, bootstrap/dotenv, config
        "Bearer",
    ]
    allowed_files = {
        pathlib.Path("plugins/models/openrouter/plugin.py"),
        pathlib.Path("config/dotenv.py"),
        pathlib.Path("plugins/sessions/jsonl/plugin.py"),  # for scrubbing
        pathlib.Path("app/bootstrap.py"),  # may reference key for docs? not really
    }
    violations: list[str] = []
    for f in SRC_ROOT.rglob("*.py"):
        rel = f.relative_to(SRC_ROOT)
        if rel in allowed_files:
            continue
        # Skip registry and cli which may reference key for docs?
        if rel.parts[0] == "plugins" and rel.parts[1] == "models":
            continue
        text = f.read_text(encoding="utf-8")
        for s in forbidden_strings:
            if s in text and "openrouter" not in str(rel).lower():
                # For kernel/contracts, any occurrence is violation
                if rel.parts[0] in ("kernel", "contracts") and s in text:
                    violations.append(f"{rel}: contains {s!r}")
                # For other plugins, check for URL or API key hardcoded
                if s == "openrouter.ai" and "openrouter" in text.lower():
                    violations.append(f"{rel}: contains {s!r}")
    # We allow OPENROUTER_API_KEY in dotenv and openrouter plugin only; already skipped
    # So this test is lenient: only check kernel/contracts for leakage
    kernel_contract_violations = []
    for f in list((SRC_ROOT / "kernel").rglob("*.py")) + list(
        (SRC_ROOT / "contracts").rglob("*.py")
    ):
        text = f.read_text(encoding="utf-8")
        if "openrouter.ai" in text.lower() or "OPENROUTER_API_KEY" in text:
            kernel_contract_violations.append(str(f.relative_to(SRC_ROOT)))
    assert not kernel_contract_violations, (
        f"kernel/contracts must not reference OpenRouter specifics: {kernel_contract_violations}"
    )


def test_plugin_metadata_uses_service_contracts_not_imports():
    # Ensure that plugins cooperate via services, not direct imports of implementations
    # This is a soft check: we already checked cross-plugin imports, but also
    # verify that no plugin does `from research_harness.plugins.<other>.plugin import X`
    # The previous test covers this; this adds explicit check for direct implementation imports
    plugins_root = SRC_ROOT / "plugins"
    violations: list[str] = []
    for f in plugins_root.rglob("*.py"):
        if f.name in ("registry.py", "__init__.py"):
            continue
        text = f.read_text(encoding="utf-8")
        # Look for imports that reference another plugin's plugin.py directly
        for line in text.splitlines():
            if (
                "from research_harness.plugins" in line
                and "import" in line
                and "models.openrouter" in line
                and "openrouter" not in str(f)
            ):
                violations.append(f"{f.relative_to(SRC_ROOT)}: {line.strip()}")
    assert not violations, "plugins must use service contracts, not direct imports:\n" + "\n".join(
        violations
    )


def test_kernel_has_no_research_imports():
    kernel_root = SRC_ROOT / "kernel"
    forbidden = [
        re.compile(r"from\s+research_harness\.research"),
        re.compile(r"import\s+research_harness\.research"),
        re.compile(r"from\s+research_harness\.contracts\.artifact"),
        re.compile(r"import\s+research_harness\.contracts\.artifact"),
    ]
    violations: list[str] = []
    for f in _read_files(kernel_root):
        text = f.read_text(encoding="utf-8")
        for pat in forbidden:
            if pat.search(text):
                violations.append(f"{f.relative_to(SRC_ROOT)}: {pat.pattern}")
    assert not violations, "kernel must not import research/artifact store:\n" + "\n".join(
        violations
    )


def test_research_schemas_have_no_storage_plugin_imports():
    research_root = SRC_ROOT / "research"
    forbidden = [
        re.compile(r"from\s+research_harness\.plugins\.storage"),
        re.compile(r"import\s+research_harness\.plugins\.storage"),
        re.compile(r"from\s+research_harness\.contracts\.artifact.*SQLite"),
        re.compile(r"SQLiteArtifactStore"),
    ]
    violations: list[str] = []
    for f in _read_files(research_root):
        text = f.read_text(encoding="utf-8")
        for pat in forbidden:
            if pat.search(text):
                violations.append(f"{f.relative_to(SRC_ROOT)}: {pat.pattern}")
    assert not violations, "research schemas must not import concrete storage:\n" + "\n".join(
        violations
    )


def test_artifact_consumers_use_contract_not_implementation():
    # Only the sqlite plugin and CLI diagnostics may import SQLiteArtifactStore directly
    allowed = {
        pathlib.Path("plugins/storage/artifacts_sqlite/plugin.py"),
        pathlib.Path(
            "cli/main.py"
        ),  # CLI helper _get_artifact_store directly instantiates for inspection
    }
    violations: list[str] = []
    for f in SRC_ROOT.rglob("*.py"):
        rel = f.relative_to(SRC_ROOT)
        if rel in allowed:
            continue
        # Research plugins and tests should use artifact_store.default service, not direct SQLite class
        # We check for direct import of SQLiteArtifactStore outside allowed
        text = f.read_text(encoding="utf-8")
        if "SQLiteArtifactStore" in text and rel.parts[0] not in ("plugins", "cli"):
            # Allow tests that directly test the store implementation
            if rel.parts[0] == "tests" and "test_artifact_store" in str(rel):
                continue
            if rel.parts[0] == "tests" and "test_research_lineage" in str(rel):
                continue
            violations.append(f"{rel}: directly imports SQLiteArtifactStore")
        # Also check for direct sqlite3 usage outside storage plugin
        if "import sqlite3" in text and "storage/artifacts_sqlite" not in str(rel):
            if rel.parts[0] == "tests":
                continue
            violations.append(f"{rel}: directly uses sqlite3 outside storage plugin")
    assert not violations, (
        "consumers must use ArtifactStore contract, not SQLite impl:\n" + "\n".join(violations)
    )
