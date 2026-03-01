from __future__ import annotations

from hotkey import GlobalHotkeyAdapter
from models import SessionMode


def test_primary_hotkey_routes_to_raw_mode() -> None:
    events: list[tuple[str, SessionMode]] = []
    adapter = GlobalHotkeyAdapter(
        primary_hotkey_name="Key.alt_l",
        secondary_hotkey_name="Key.alt_r",
    )
    adapter._on_press = lambda mode: events.append(("press", mode))
    adapter._on_release = lambda mode: events.append(("release", mode))

    adapter._handle_key_state("option_l", True)
    adapter._handle_key_state("option_l", False)

    assert events == [
        ("press", SessionMode.RAW),
        ("release", SessionMode.RAW),
    ]


def test_secondary_hotkey_routes_to_polish_mode() -> None:
    events: list[tuple[str, SessionMode]] = []
    adapter = GlobalHotkeyAdapter(
        primary_hotkey_name="Key.alt_l",
        secondary_hotkey_name="Key.alt_r",
    )
    adapter._on_press = lambda mode: events.append(("press", mode))
    adapter._on_release = lambda mode: events.append(("release", mode))

    adapter._handle_key_state("option_r", True)
    adapter._handle_key_state("option_r", False)

    assert events == [
        ("press", SessionMode.POLISH),
        ("release", SessionMode.POLISH),
    ]


def test_hotkey_release_requires_same_pressed_key() -> None:
    events: list[tuple[str, SessionMode]] = []
    adapter = GlobalHotkeyAdapter(
        primary_hotkey_name="Key.alt_l",
        secondary_hotkey_name="Key.alt_r",
    )
    adapter._on_press = lambda mode: events.append(("press", mode))
    adapter._on_release = lambda mode: events.append(("release", mode))

    adapter._handle_key_state("option_l", True)
    adapter._handle_key_state("option_r", False)  # ignored
    adapter._handle_key_state("option_l", False)

    assert events == [
        ("press", SessionMode.RAW),
        ("release", SessionMode.RAW),
    ]


def test_hotkey_ignores_second_press_until_active_key_released() -> None:
    events: list[tuple[str, SessionMode]] = []
    adapter = GlobalHotkeyAdapter(
        primary_hotkey_name="Key.alt_l",
        secondary_hotkey_name="Key.alt_r",
    )
    adapter._on_press = lambda mode: events.append(("press", mode))
    adapter._on_release = lambda mode: events.append(("release", mode))

    adapter._handle_key_state("option_l", True)
    adapter._handle_key_state("option_r", True)  # ignored while option active
    adapter._handle_key_state("option_r", False)  # ignored
    adapter._handle_key_state("option_l", False)

    assert events == [
        ("press", SessionMode.RAW),
        ("release", SessionMode.RAW),
    ]
