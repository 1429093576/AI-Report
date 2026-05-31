"""Lightweight runner for reusable skill assets."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SkillSpec:
    """Declarative wiring for a reusable decision skill."""

    name: str
    references: tuple[str, ...] = ()
    validator_script: str | None = None
    context_key: str | None = None


@dataclass(frozen=True)
class SkillValidationResult:
    """Captured result from a skill validation script."""

    skill_name: str
    script_path: Path
    exit_code: int
    stdout: str = ""
    stderr: str = ""

    @property
    def passed(self) -> bool:
        return self.exit_code == 0

    def summary(self) -> str:
        output = "\n".join(part for part in (self.stderr, self.stdout) if part.strip()).strip()
        return output or f"{self.skill_name} validator exited with {self.exit_code}"


class SkillRunner:
    """Load skill context and run skill validators for pipeline steps."""

    def __init__(self, skills_root: Path | str = "skills") -> None:
        self.skills_root = Path(skills_root)

    def load_context(self, spec: SkillSpec) -> str:
        """Load a skill's human-readable policy assets for prompt grounding."""

        root = self.skills_root / spec.name
        sections = [("SKILL.md", root / "SKILL.md")]
        sections.extend((reference, root / reference) for reference in spec.references)

        parts: list[str] = [f"# Skill Context: {spec.name}"]
        for label, path in sections:
            if not path.exists():
                raise FileNotFoundError(f"skill asset not found: {path}")
            parts.extend([f"## {label}", path.read_text(encoding="utf-8").strip()])
        return "\n\n".join(parts).strip()

    def load_contexts(self, specs: list[SkillSpec] | tuple[SkillSpec, ...]) -> str:
        """Load and join prompt context for multiple skills."""

        return "\n\n".join(self.load_context(spec) for spec in specs)

    def insert_context(self, template: str, skill_context: str) -> str:
        """Insert skill context before the final input section when possible."""

        marker = "\n## 待处理输入"
        section = f"\n\n## Skill Context\n\n{skill_context}\n"
        if marker in template:
            return template.replace(marker, f"{section}{marker}", 1)
        return f"{template.rstrip()}{section}"

    def apply_prompt_context(
        self,
        template: str,
        specs: list[SkillSpec] | tuple[SkillSpec, ...],
    ) -> str:
        """Load skill assets and insert them into a prompt template."""

        return self.insert_context(template, self.load_contexts(specs))

    def validate(
        self,
        spec: SkillSpec,
        *args: Any,
        context: Any | None = None,
    ) -> SkillValidationResult:
        """Run a skill validation script's ``validate`` function in-process."""

        if not spec.validator_script:
            raise ValueError(f"{spec.name} does not define a validator script")

        script_path = self.skills_root / spec.name / spec.validator_script
        if not script_path.exists():
            raise FileNotFoundError(f"skill validator not found: {script_path}")

        module = _load_script_module(script_path)
        validate = getattr(module, "validate", None)
        if not callable(validate):
            raise AttributeError(f"{script_path} must expose a callable validate function")

        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                raw_exit_code = validate(*args)
            except SystemExit as exc:
                raw_exit_code = exc.code

        exit_code = _exit_code(raw_exit_code)
        result = SkillValidationResult(
            skill_name=spec.name,
            script_path=script_path,
            exit_code=exit_code,
            stdout=stdout.getvalue(),
            stderr=stderr.getvalue(),
        )
        if not result.passed:
            raise ValueError(f"{spec.name} skill validation failed: {result.summary()}")
        if context is not None and spec.context_key:
            context.set(spec.context_key, result)
        return result


def _load_script_module(path: Path) -> Any:
    resolved = path.resolve()
    module_name = "_skill_validator_" + hashlib.sha1(str(resolved).encode("utf-8")).hexdigest()
    if module_name in sys.modules:
        return sys.modules[module_name]

    spec = importlib.util.spec_from_file_location(module_name, resolved)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import skill validator: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _exit_code(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 1
