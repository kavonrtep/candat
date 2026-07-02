"""Tests for the help screen."""

import pytest

from candat.app import CandatApp
from candat.help import HelpScreen

pytestmark = pytest.mark.asyncio


async def chord(pilot, *keys):
    for key in keys:
        await pilot.press(key)
        await pilot.pause()
    await pilot.pause()


async def test_f1_opens_and_closes_help():
    app = CandatApp()
    async with app.run_test() as pilot:
        await pilot.press("f1")
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)
        await pilot.press("q")
        await pilot.pause()
        assert not isinstance(app.screen, HelpScreen)


async def test_cx_question_mark_opens_help():
    app = CandatApp()
    async with app.run_test() as pilot:
        await chord(pilot, "ctrl+x", "question_mark")
        assert isinstance(app.screen, HelpScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, HelpScreen)
