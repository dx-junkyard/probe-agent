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

from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from typing import Any, Callable, ContextManager, Iterator, Optional

Timer = Callable[[str], ContextManager[Any]]

# The ask request's scope, visible only while a caller binds it (Issue #469:
# screen providers; Issue #470: the discussion-bundle and adapter resolvers,
# which also call ``build_overview``).  ContextVar, not module state: nothing
# is shared across requests, threads, or Systems.
ACTIVE_SCOPE: ContextVar[Any] = ContextVar("assistant_ask_scope", default=None)


@contextmanager
def bind_scope(scope: Any) -> Iterator[None]:
    token = ACTIVE_SCOPE.set(scope)
    try:
        yield
    finally:
        ACTIVE_SCOPE.reset(token)


def active_system_state_provider(system_id: int) -> Optional[Callable[[], Any]]:
    """The bound scope's ``system_state`` provider for ``system_id``, or None.

    A scope of another System is never used (no cross-System sharing).
    """
    scope = ACTIVE_SCOPE.get()
    if scope is None or getattr(scope, "system_id", None) != system_id:
        return None
    return scope.system_state


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
