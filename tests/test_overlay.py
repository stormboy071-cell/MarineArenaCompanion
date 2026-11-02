from __future__ import annotations

from marine_arena_companion.overlay import OverlaySession


def test_overlay_match_timer_start_pause_resume_and_reset() -> None:
    now = [100.0]
    overlay = OverlaySession(clock=lambda: now[0])

    assert overlay.status() == {
        "running": False, "elapsed_seconds": 0, "dismissed_reminder_ids": [],
    }
    overlay.start()
    now[0] += 12.8
    assert overlay.status()["elapsed_seconds"] == 12

    overlay.pause()
    now[0] += 30
    assert overlay.status()["elapsed_seconds"] == 12

    overlay.start()
    now[0] += 3.3
    overlay.dismiss(4)
    assert overlay.status() == {
        "running": True, "elapsed_seconds": 16, "dismissed_reminder_ids": [4],
    }

    overlay.reset()
    assert overlay.status() == {
        "running": False, "elapsed_seconds": 0, "dismissed_reminder_ids": [],
    }
