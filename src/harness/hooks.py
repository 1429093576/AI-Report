"""Hook registration and execution for Harness-managed workflows."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from importlib import import_module
from types import ModuleType
from typing import Any

from .context import PipelineContext


Hook = Callable[..., PipelineContext | None]


class HookRegistry:
    """Register and run named hook chains."""

    def __init__(self) -> None:
        self._hooks: dict[str, list[Hook]] = defaultdict(list)

    def register(self, name: str, hook: Hook) -> None:
        """Register a callable hook under a named lifecycle event."""

        hook_name = name.strip()
        if not hook_name:
            raise ValueError("hook name must not be empty")
        if not callable(hook):
            raise TypeError("hook must be callable")

        self._hooks[hook_name].append(hook)

    def register_module(self, name: str, module_path: str) -> None:
        """Register a hook module that exposes a ``run`` callable."""

        module = import_module(module_path)
        self.register(name, self._get_module_hook(module, module_path))

    def run(
        self,
        name: str,
        context: PipelineContext,
        **kwargs: Any,
    ) -> PipelineContext:
        """Run all hooks registered for ``name`` in registration order."""

        current_context = context
        for hook in self._hooks.get(name, []):
            result = hook(current_context, **kwargs)
            if result is not None:
                if not isinstance(result, PipelineContext):
                    raise TypeError("hook must return PipelineContext or None")
                current_context = result

        return current_context

    def run_error_hooks(
        self,
        context: PipelineContext,
        error: Exception,
    ) -> PipelineContext:
        """Run hooks registered for the ``on_error`` lifecycle event."""

        return self.run("on_error", context, error=error)

    def has_hooks(self, name: str) -> bool:
        """Return whether hooks are registered for ``name``."""

        return bool(self._hooks.get(name))

    def names(self) -> list[str]:
        """Return registered hook names in sorted order."""

        return sorted(self._hooks.keys())

    @staticmethod
    def _get_module_hook(module: ModuleType, module_path: str) -> Hook:
        hook = getattr(module, "run", None)
        if not callable(hook):
            raise AttributeError(f"hook module {module_path!r} must define run()")
        return hook
