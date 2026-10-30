"""The opt-in stall watchdog logs the event-loop stack during a freeze."""

import time

import pytest

from candat import watchdog
from helpers import open_app, wait_for


def test_threshold_from_env(monkeypatch):
    monkeypatch.delenv("CANDAT_WATCHDOG", raising=False)
    assert watchdog.threshold_from_env() is None
    monkeypatch.setenv("CANDAT_WATCHDOG", "1")
    assert watchdog.threshold_from_env() == watchdog.DEFAULT_THRESHOLD
    monkeypatch.setenv("CANDAT_WATCHDOG", "0.2")
    assert watchdog.threshold_from_env() == 0.2
    monkeypatch.setenv("CANDAT_WATCHDOG", "0")
    assert watchdog.threshold_from_env() is None


def blocking_handler_for_test():
    time.sleep(0.6)  # a deliberate UI-thread stall


@pytest.mark.asyncio
async def test_watchdog_records_a_stall(monkeypatch):
    monkeypatch.setenv("CANDAT_WATCHDOG", "0.2")
    async with open_app() as (app, pilot):
        assert app._watchdog is not None
        blocking_handler_for_test()

        def logged() -> bool:
            path = watchdog.log_path()
            return path.exists() and "blocking_handler_for_test" in path.read_text()

        # Other stalls (a slow startup on a loaded machine) may be logged
        # too; what matters is that this one is, with its stack.
        assert await wait_for(pilot, logged)
        assert "UI blocked" in watchdog.log_path().read_text()
        assert app._watchdog.stalls >= 1
