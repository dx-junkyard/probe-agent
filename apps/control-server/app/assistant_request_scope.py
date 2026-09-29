"""Per-request memo for one ``POST /assistant/ask`` (Issue #469).

The ask path used to compute the System diagnostics report up to three times
(the route, ``build_system_state``, and -- on the Overview screen --
``build_overview`` -> ``build_system_state`` again).  This scope computes each
at most once *within one request*.

Deliberately NOT a cache: no module-level state, no TTL, nothing shared across
requests or Systems.  A new ``AskRequestScope`` is created per ask.  Neither
computation is invoked while a ``get_conn()`` connection is held: both open
their own connections.
"""
from __future__ import annotations

from contextlib import nullcontext
from typing import Any, Callable, ContextManager, Optional

Timer = Callable[[str], ContextManager[Any]]


class AskRequestScope:
    def __init__(self, system_id: int, timer: Optional[Timer] = None) -> None:
        self.system_id = system_id
        self._timer: Timer = timer if timer is not None else (lambda _stage: nullcontext())
        self._diagnostics: Any = None
        self._state: Any = None
        self._has_diagnostics = False
        self._has_state = False
        self.diagnostics_runs = 0
        self.system_state_runs = 0

    def diagnostics(self) -> Any:
        if not self._has_diagnostics:
            from . import system_diagnostics

            with self._timer("diagnostics"):
                self._diagnostics = system_diagnostics.run_system_diagnostics(self.system_id)
            self.diagnostics_runs += 1
            self._has_diagnostics = True
        return self._diagnostics

    def system_state(self) -> Any:
        if not self._has_state:
            from . import system_state

            report = self.diagnostics()
            with self._timer("system_state"):
                self._state = system_state.build_system_state(
                    self.system_id, diagnostics_report=report
                )
            self.system_state_runs += 1
            self._has_state = True
        return self._state
