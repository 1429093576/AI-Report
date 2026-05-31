"""SkillRunner tests."""

from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from src.harness import PipelineContext, SkillRunner, SkillSpec


class SkillRunnerTests(unittest.TestCase):
    def test_loads_context_inserts_prompt_and_records_validation(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = root / "skills" / "demo_skill"
            (skill_root / "references").mkdir(parents=True)
            (skill_root / "scripts").mkdir()
            (skill_root / "SKILL.md").write_text("Demo skill purpose.", encoding="utf-8")
            (skill_root / "references" / "rules.md").write_text(
                "Demo rules.",
                encoding="utf-8",
            )
            (skill_root / "scripts" / "validate_demo.py").write_text(
                "\n".join(
                    [
                        "def validate(path):",
                        "    print(f'validated {path.name}')",
                        "    return 0",
                    ]
                ),
                encoding="utf-8",
            )

            runner = SkillRunner(root / "skills")
            spec = SkillSpec(
                name="demo_skill",
                references=("references/rules.md",),
                validator_script="scripts/validate_demo.py",
                context_key="demo_skill_validation",
            )
            context = PipelineContext(run_id="run-test", run_date=date(2026, 5, 20))
            payload_path = root / "payload.json"
            payload_path.write_text("[]", encoding="utf-8")

            skill_context = runner.load_context(spec)
            prompt = runner.apply_prompt_context("Intro\n## 待处理输入\n{}", [spec])
            result = runner.validate(spec, payload_path, context=context)

            self.assertIn("Skill Context: demo_skill", skill_context)
            self.assertIn("## references/rules.md", skill_context)
            self.assertLess(prompt.index("## Skill Context"), prompt.index("## 待处理输入"))
            self.assertTrue(result.passed)
            self.assertIn("validated payload.json", result.stdout)
            self.assertIs(context.get("demo_skill_validation"), result)

    def test_validation_fails_closed(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = root / "skills" / "demo_skill"
            (skill_root / "scripts").mkdir(parents=True)
            (skill_root / "SKILL.md").write_text("Demo skill purpose.", encoding="utf-8")
            (skill_root / "scripts" / "validate_demo.py").write_text(
                "\n".join(
                    [
                        "def validate(path):",
                        "    print('not valid')",
                        "    return 2",
                    ]
                ),
                encoding="utf-8",
            )

            runner = SkillRunner(root / "skills")
            spec = SkillSpec(
                name="demo_skill",
                validator_script="scripts/validate_demo.py",
            )

            with self.assertRaisesRegex(ValueError, "demo_skill skill validation failed"):
                runner.validate(spec, root / "payload.json")


if __name__ == "__main__":
    unittest.main()
