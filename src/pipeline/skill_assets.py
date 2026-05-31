"""Compatibility wrappers for first-stage skill asset integration."""

from __future__ import annotations

from typing import Any

from src.harness.skill_runner import SkillRunner, SkillSpec, SkillValidationResult


_DEFAULT_RUNNER = SkillRunner()


def load_skill_context(skill_name: str, references: list[str]) -> str:
    """Load a skill's human-readable policy assets for prompt grounding."""

    return _DEFAULT_RUNNER.load_context(SkillSpec(skill_name, tuple(references)))


def insert_skill_context(template: str, skill_context: str) -> str:
    """Insert skill context before the final input section when possible."""

    return _DEFAULT_RUNNER.insert_context(template, skill_context)


def run_skill_validator(
    skill_name: str,
    script_relative_path: str,
    *args: Any,
) -> SkillValidationResult:
    """Run a skill validation script's ``validate`` function in-process."""

    return _DEFAULT_RUNNER.validate(
        SkillSpec(skill_name, validator_script=script_relative_path),
        *args,
    )
