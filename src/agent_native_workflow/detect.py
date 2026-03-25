from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


def _debug(msg: str) -> None:
    if os.environ.get("DEBUG"):
        print(f"[detect:debug] {msg}", file=sys.stderr)


def _resolve_cmd(cmd: str) -> str | None:
    if shutil.which(cmd):
        return cmd
    if sys.prefix != sys.base_prefix:
        venv_path = Path(sys.prefix) / "bin" / cmd
        if venv_path.is_file():
            _debug(f"_resolve_cmd({cmd}) → {venv_path} (venv)")
            return str(venv_path)
    return None


def _cmd_exists(cmd: str) -> bool:
    exists = _resolve_cmd(cmd) is not None
    _debug(f"_cmd_exists({cmd}) → {exists}")
    return exists


def _has_makefile_target(target: str, project_root: Path | None = None) -> bool:
    makefile = (project_root or Path.cwd()) / "Makefile"
    if not makefile.is_file():
        return False
    try:
        content = makefile.read_text()
        found = bool(re.search(rf"^{re.escape(target)}:", content, re.MULTILINE))
        _debug(f"_has_makefile_target({target}) → {found}")
        return found
    except OSError:
        return False


def _has_npm_script(script: str, project_root: Path | None = None) -> bool:
    pkg_json = (project_root or Path.cwd()) / "package.json"
    if not pkg_json.is_file():
        return False
    try:
        data = json.loads(pkg_json.read_text())
        found = script in data.get("scripts", {})
        _debug(f"_has_npm_script({script}) → {found}")
        return found
    except (OSError, json.JSONDecodeError):
        return False


def _python_runner(project_root: Path | None = None) -> str:
    root = project_root or Path.cwd()
    if (root / "uv.lock").is_file() and _cmd_exists("uv"):
        return "uv run"
    if (root / "poetry.lock").is_file() and _cmd_exists("poetry"):
        return "poetry run"
    return ""


@dataclass
class ProjectConfig:
    """Detected project configuration."""

    project_type: str = "unknown"
    src_dirs: str = "."
    lint_cmd: str = ""
    test_cmd: str = ""
    security_cmd: str = ""
    instruction_files: list[str] = field(default_factory=list)
    design_docs: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    base_branch: str = "main"

    def print_config(self) -> str:
        lines = [
            "=== Detected Project Configuration ===",
            f"  PROJECT_TYPE:      {self.project_type}",
            f"  SRC_DIRS:          {self.src_dirs}",
            f"  LINT_CMD:          {self.lint_cmd or '<none — will skip>'}",
            f"  TEST_CMD:          {self.test_cmd or '<none — will skip>'}",
            f"  SECURITY_CMD:      {self.security_cmd or '<none — will skip>'}",
            f"  INSTRUCTION_FILES: {' '.join(self.instruction_files) or '<none>'}",
            f"  DESIGN_DOCS:       {' '.join(self.design_docs) or '<none>'}",
            f"  BASE_BRANCH:       {self.base_branch}",
            "=======================================",
        ]
        return "\n".join(lines)


def detect_project_type(project_root: Path | None = None) -> str:
    if env_val := os.environ.get("PROJECT_TYPE"):
        _debug(f"PROJECT_TYPE from env: {env_val}")
        return env_val

    root = project_root or Path.cwd()

    if any((root / f).is_file() for f in ("pyproject.toml", "setup.py", "setup.cfg")):
        result = "python"
    elif (root / "package.json").is_file():
        result = "node"
    elif (root / "Cargo.toml").is_file():
        result = "rust"
    elif (root / "go.mod").is_file():
        result = "go"
    elif (root / "pom.xml").is_file():
        result = "java-maven"
    elif any((root / f).is_file() for f in ("build.gradle", "build.gradle.kts")):
        result = "java-gradle"
    else:
        result = "unknown"

    _debug(f"detect_project_type → {result}")
    return result


def detect_src_dirs(project_root: Path | None = None) -> str:
    if env_val := os.environ.get("SRC_DIRS"):
        _debug(f"SRC_DIRS from env: {env_val}")
        return env_val

    root = project_root or Path.cwd()
    # Java standard layout: src/main/java — include as "src/"
    dirs = [f"{d}/" for d in ("src", "app", "lib", "pkg") if (root / d).is_dir()]
    result = " ".join(dirs) if dirs else "."
    _debug(f"detect_src_dirs → {result}")
    return result


def detect_lint_cmd(
    project_type: str | None = None, src_dirs: str | None = None, project_root: Path | None = None
) -> str:
    if env_val := os.environ.get("LINT_CMD"):
        _debug(f"LINT_CMD from env: {env_val}")
        return env_val

    root = project_root or Path.cwd()
    ptype = project_type or detect_project_type(root)
    sdirs = src_dirs or detect_src_dirs(root)

    if _has_makefile_target("lint", root):
        return "make lint"

    if ptype == "node" and _has_npm_script("lint", root):
        return "npm run lint"

    runner = _python_runner(root)

    if ptype == "python":
        resolved = _resolve_cmd("ruff")
        if resolved:
            return f"{runner} ruff check {sdirs}" if runner else f"{resolved} check {sdirs}"
        resolved = _resolve_cmd("flake8")
        if resolved:
            return f"{runner} flake8 {sdirs}" if runner else f"{resolved} {sdirs}"
        resolved = _resolve_cmd("pylint")
        if resolved:
            return f"{runner} pylint {sdirs}" if runner else f"{resolved} {sdirs}"
    elif ptype == "node":
        if _cmd_exists("eslint"):
            return f"npx eslint {sdirs}"
    elif ptype == "rust":
        return "cargo clippy -- -D warnings"
    elif ptype == "go":
        if _cmd_exists("golangci-lint"):
            return "golangci-lint run ./..."
        return "go vet ./..."
    elif ptype == "java-maven":
        return "mvn checkstyle:check -q"
    elif ptype == "java-gradle":
        gradlew = root / "gradlew"
        cmd = "./gradlew" if gradlew.is_file() else "gradle"
        return f"{cmd} checkstyleMain --quiet"

    _debug("detect_lint_cmd → (none)")
    return ""


def detect_test_cmd(project_type: str | None = None, project_root: Path | None = None) -> str:
    if env_val := os.environ.get("TEST_CMD"):
        _debug(f"TEST_CMD from env: {env_val}")
        return env_val

    root = project_root or Path.cwd()
    ptype = project_type or detect_project_type(root)

    if _has_makefile_target("test", root):
        return "make test"

    if ptype == "node" and _has_npm_script("test", root):
        return "npm test"

    runner = _python_runner(root)

    if ptype == "python":
        resolved = _resolve_cmd("pytest")
        if resolved:
            flags = "pytest --tb=short -q"
            return f"{runner} {flags}" if runner else f"{resolved} --tb=short -q"
        if (root / "tests").is_dir() or (root / "test").is_dir():
            prefix = f"{runner} " if runner else ""
            return f"{prefix}python -m unittest discover"
    elif ptype == "node":
        if _cmd_exists("jest"):
            return "npx jest --ci"
        if _cmd_exists("vitest"):
            return "npx vitest run"
    elif ptype == "rust":
        return "cargo test"
    elif ptype == "go":
        return "go test ./..."
    elif ptype == "java-maven":
        return "mvn test -q"
    elif ptype == "java-gradle":
        gradlew = root / "gradlew"
        cmd = "./gradlew" if gradlew.is_file() else "gradle"
        return f"{cmd} test --quiet"

    _debug("detect_test_cmd → (none)")
    return ""


def detect_security_cmd(
    project_type: str | None = None, src_dirs: str | None = None, project_root: Path | None = None
) -> str:
    if "SECURITY_CMD" in os.environ:
        env_val = os.environ["SECURITY_CMD"]
        _debug(f"SECURITY_CMD from env: {env_val!r}")
        return env_val

    root = project_root or Path.cwd()
    ptype = project_type or detect_project_type(root)
    sdirs = src_dirs or detect_src_dirs(root)

    semgrep = _resolve_cmd("semgrep")
    if semgrep:
        return f"{semgrep} scan --config auto --quiet {sdirs}"

    if ptype == "python":
        runner = _python_runner(root)
        resolved = _resolve_cmd("bandit")
        if resolved:
            return f"{runner} bandit -r {sdirs} -q" if runner else f"{resolved} -r {sdirs} -q"
    elif ptype == "node":
        return "npm audit --audit-level=high"
    elif ptype == "rust":
        if _cmd_exists("cargo-audit"):
            return "cargo audit"
    elif ptype == "go" and _cmd_exists("gosec"):
        return "gosec ./..."

    _debug("detect_security_cmd → (none)")
    return ""


def detect_instruction_files(project_root: Path | None = None) -> list[str]:
    if env_val := os.environ.get("INSTRUCTION_FILES"):
        return env_val.split()

    root = project_root or Path.cwd()
    files: list[str] = []
    seen: set[str] = set()

    for name in ("CLAUDE.md", "convention.md", "CONTRIBUTING.md"):
        path = root / name
        if path.is_file() and name not in seen:
            files.append(name)
            seen.add(name)

    rules_dir = root / ".claude" / "rules"
    if rules_dir.is_dir():
        for match in sorted(rules_dir.glob("*.md")):
            rel = str(match.relative_to(root))
            if rel not in seen:
                files.append(rel)
                seen.add(rel)

    _debug(f"detect_instruction_files → {files}")
    return files


def detect_design_docs(project_root: Path | None = None) -> list[str]:
    if env_val := os.environ.get("DESIGN_DOCS"):
        return env_val.split()

    root = project_root or Path.cwd()
    candidates = [
        "docs/design-doc.md",
        "docs/architecture.md",
        "docs/design.md",
        "ARCHITECTURE.md",
    ]
    files = [f for f in candidates if (root / f).is_file()]
    _debug(f"detect_design_docs → {files}")
    return files


def detect_changed_files(
    base_branch: str = "main", project_type: str | None = None, project_root: Path | None = None
) -> list[str]:
    if env_val := os.environ.get("CHANGED_FILES"):
        return env_val.split()

    root = project_root or Path.cwd()
    ptype = project_type or detect_project_type(root)

    def _git(*args: str) -> list[str]:
        try:
            result = subprocess.run(
                ["git", *args],
                capture_output=True,
                text=True,
                cwd=root,
                timeout=10,
            )
            return [line for line in result.stdout.strip().splitlines() if line][:200]
        except (subprocess.SubprocessError, FileNotFoundError):
            return []

    committed = _git("diff", "--name-only", f"{base_branch}..HEAD")
    staged = _git("diff", "--name-only", "--cached")
    unstaged = _git("diff", "--name-only", "HEAD")
    untracked = _git("ls-files", "--others", "--exclude-standard")

    all_files = sorted(set(committed + staged + unstaged + untracked))

    if not all_files:
        ext_map = {
            "python": ["*.py"],
            "node": ["*.ts", "*.tsx", "*.js", "*.jsx"],
            "rust": ["*.rs"],
            "go": ["*.go"],
            "java-maven": ["*.java"],
            "java-gradle": ["*.java"],
        }
        patterns = ext_map.get(ptype, ["*.py", "*.ts", "*.js", "*.rs", "*.go"])
        found: list[str] = []
        for pattern in patterns:
            found.extend(
                str(p.relative_to(root))
                for p in root.rglob(pattern)
                if not any(
                    part in p.parts for part in ("node_modules", ".venv", "target", "__pycache__")
                )
            )
        all_files = sorted(set(found))[:200]

    if not all_files:
        all_files = ["No changed files detected"]

    _debug(f"detect_changed_files → {len(all_files)} files")
    return all_files


def _file_hash(path: Path) -> str:
    try:
        return hashlib.md5(path.read_bytes(), usedforsecurity=False).hexdigest()
    except OSError:
        return ""


def snapshot_working_tree(project_root: Path | None = None) -> dict[str, str]:
    """Capture modified/staged/untracked files with their content hashes.

    Returns {porcelain_line: content_hash} so that files already in a modified
    state (from a prior iteration) are still detected as changed if their
    content differs from the snapshot taken before Agent A ran.
    """
    root = project_root or Path.cwd()
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            cwd=root,
            timeout=10,
        )
        snapshot: dict[str, str] = {}
        for line in result.stdout.splitlines():
            if not line:
                continue
            path_str = line[3:].split(" -> ")[-1].strip()
            snapshot[line] = _file_hash(root / path_str)
        return snapshot
    except (subprocess.SubprocessError, FileNotFoundError):
        return {}


def files_changed_since(before: dict[str, str], project_root: Path | None = None) -> list[str]:
    """Return files that changed between a snapshot and now.

    Detects both new entries in git status AND files whose content hash
    changed even though the git status code stayed the same (e.g. a file
    already modified in a prior iteration that the agent edited again).
    """
    after = snapshot_working_tree(project_root)
    paths: list[str] = []
    for entry, hash_after in sorted(after.items()):
        hash_before = before.get(entry)
        if hash_before is None or hash_before != hash_after:
            # porcelain format: "XY path" or "XY old -> new"
            parts = entry[3:].split(" -> ")
            paths.append(parts[-1].strip())
    return paths


def detect_all(project_root: Path | None = None, base_branch: str | None = None) -> ProjectConfig:
    root = project_root or Path.cwd()
    branch = base_branch or os.environ.get("BASE_BRANCH", "main")

    ptype = detect_project_type(root)
    sdirs = detect_src_dirs(root)

    return ProjectConfig(
        project_type=ptype,
        src_dirs=sdirs,
        lint_cmd=detect_lint_cmd(ptype, sdirs, root),
        test_cmd=detect_test_cmd(ptype, root),
        security_cmd=detect_security_cmd(ptype, sdirs, root),
        instruction_files=detect_instruction_files(root),
        design_docs=detect_design_docs(root),
        changed_files=detect_changed_files(branch, ptype, root),
        base_branch=branch,
    )
