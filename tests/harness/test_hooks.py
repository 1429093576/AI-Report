"""HookRegistry tests."""

from __future__ import annotations

import unittest

from src.harness import HookRegistry, PipelineContext


class HookRegistryTests(unittest.TestCase):
    def test_register_rejects_empty_name(self) -> None:
        registry = HookRegistry()

        with self.assertRaises(ValueError):
            registry.register(" ", lambda context: context)

    def test_register_rejects_non_callable_hook(self) -> None:
        registry = HookRegistry()

        with self.assertRaises(TypeError):
            registry.register("pre_process", "not-callable")  # type: ignore[arg-type]

    def test_run_executes_hooks_in_registration_order(self) -> None:
        registry = HookRegistry()
        context = PipelineContext(run_id="run-test")

        def first(current: PipelineContext) -> None:
            current.set("order", ["first"])

        def second(current: PipelineContext) -> PipelineContext:
            current.get("order").append("second")
            return current

        registry.register("pre_process", first)
        registry.register("pre_process", second)

        result = registry.run("pre_process", context)

        self.assertIs(result, context)
        self.assertEqual(result.get("order"), ["first", "second"])

    def test_run_allows_context_replacement(self) -> None:
        registry = HookRegistry()
        original = PipelineContext(run_id="run-original")
        replacement = PipelineContext(run_id="run-replacement")

        registry.register("pre_process", lambda context: replacement)

        result = registry.run("pre_process", original)

        self.assertIs(result, replacement)

    def test_run_rejects_invalid_return_value(self) -> None:
        registry = HookRegistry()
        context = PipelineContext(run_id="run-test")

        registry.register("pre_process", lambda current: "bad")  # type: ignore[return-value]

        with self.assertRaises(TypeError):
            registry.run("pre_process", context)

    def test_run_unknown_hook_name_returns_context(self) -> None:
        registry = HookRegistry()
        context = PipelineContext(run_id="run-test")

        result = registry.run("post_validate", context)

        self.assertIs(result, context)

    def test_run_error_hooks_passes_error_argument(self) -> None:
        registry = HookRegistry()
        context = PipelineContext(run_id="run-test")
        error = RuntimeError("boom")

        def on_error(current: PipelineContext, error: Exception) -> None:
            current.set("error_type", error.__class__.__name__)
            current.set("error_message", str(error))

        registry.register("on_error", on_error)

        result = registry.run_error_hooks(context, error)

        self.assertIs(result, context)
        self.assertEqual(result.get("error_type"), "RuntimeError")
        self.assertEqual(result.get("error_message"), "boom")

    def test_register_module_uses_run_callable(self) -> None:
        registry = HookRegistry()
        context = PipelineContext(run_id="run-test")

        registry.register_module("pre_process", "hooks.pre_process")

        result = registry.run("pre_process", context)

        self.assertIs(result, context)
        self.assertTrue(registry.has_hooks("pre_process"))
        self.assertEqual(registry.names(), ["pre_process"])

    def test_register_module_requires_run_callable(self) -> None:
        registry = HookRegistry()

        with self.assertRaises(AttributeError):
            registry.register_module("broken", "src.harness.context")


if __name__ == "__main__":
    unittest.main()
