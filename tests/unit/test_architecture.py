"""静态依赖边界：坚守内核五模块纯净度，禁止内核反向依赖外核、Web 框架或厂商客户端。"""

import ast
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
PACKAGE = SRC / "agent_service"


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package = ".".join(path.relative_to(SRC).with_suffix("").parts[:-1])
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                module = importlib.util.resolve_name("." * node.level + module, package)
            modules.add(module)
            modules.update(f"{module}.{alias.name}" for alias in node.names)
    return modules


def test_kernel_has_five_explicit_responsibilities():
    modules = {
        path.name
        for path in (PACKAGE / "kernel").iterdir()
        if path.is_dir() and path.name != "__pycache__"
    }
    assert modules == {"runtime", "context", "model", "dispatch", "strategy"}


def test_kernel_never_imports_outer_implementations_or_configuration():
    forbidden = (
        "agent_service.adapters",
        "agent_service.config",
        "agent_service.bootstrap",
        "agent_service.main",
        "agent_service.web_main",
        "fastapi",
        "starlette",
        "uvicorn",
        "openai",
    )
    violations = []
    for path in (PACKAGE / "kernel").rglob("*.py"):
        for module in imported_modules(path):
            if any(
                module == prefix or module.startswith(prefix + ".")
                for prefix in forbidden
            ):
                violations.append(f"{path.relative_to(ROOT)}: {module}")
    assert violations == []


def test_legacy_flat_package_directories_remain_removed():
    removed = {"core", "client", "tools", "ui", "safety", "api", "db", "services"}
    for name in removed:
        assert not (PACKAGE / name).exists()
    for path in PACKAGE.rglob("*.py"):
        for module in imported_modules(path):
            segments = module.split(".")
            assert not (
                len(segments) > 1
                and segments[0] == "agent_service"
                and segments[1] in removed
            ), f"{path}: {module}"
