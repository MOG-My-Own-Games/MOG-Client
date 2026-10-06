import wave

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402

from mog_client.gui import sounds  # noqa: E402


class FakeEffect:
    def __init__(self, path, volume):
        self.path, self.volume, self.plays = path, volume, 0

    def play(self):
        self.plays += 1

    def stop(self):
        self.stopped = True

    def deleteLater(self):  # noqa: N802 - Qt's name
        self.deleted = True


def player(enabled=True, factory=None, clock=None):
    made: list[FakeEffect] = []

    def default_factory(path, volume):
        made.append(FakeEffect(path, volume))
        return made[-1]

    clock = clock or [0.0]
    p = sounds.SoundPlayer(lambda: enabled, factory or default_factory, lambda: clock[0])
    return p, made, clock


def test_a_sound_plays_when_the_setting_is_on_and_not_when_it_is_off():
    p, made, _ = player(enabled=True)
    p.play(sounds.PLAY)
    assert made[0].plays == 1 and made[0].path.name == "play.wav"
    off, made_off, _ = player(enabled=False)
    off.play(sounds.PLAY)
    assert made_off == []  # not even loaded


def test_a_held_direction_does_not_become_a_buzz():
    p, made, clock = player()
    for _ in range(5):  # five repeats within a few milliseconds
        p.play(sounds.NAVIGATE)
        clock[0] += 0.01
    assert made[0].plays == 1
    clock[0] += 0.1
    p.play(sounds.NAVIGATE)
    assert made[0].plays == 2


def test_each_sound_has_its_own_debounce_and_volume():
    p, made, _ = player()
    p.play(sounds.NAVIGATE)
    p.play(sounds.PLAY)  # not held back by the navigation sound just played
    assert {m.path.name: m.volume for m in made} == {"navigate.wav": sounds.VOLUME[sounds.NAVIGATE], "play.wav": sounds.VOLUME[sounds.PLAY]}


def test_a_machine_that_cannot_play_sound_stays_quiet():
    def no_audio(path, volume):
        raise RuntimeError("no multimedia backend")

    p, _, _ = player(factory=no_audio)
    p.play(sounds.NAVIGATE)
    p.play(sounds.NAVIGATE)  # nothing raised, and it does not retry the load every time

    class Broken(FakeEffect):
        def play(self):
            raise OSError("no device")

    broken, _, _ = player(factory=lambda path, volume: Broken(path, volume))
    broken.play(sounds.PLAY)


def test_an_unknown_sound_is_ignored():
    p, made, _ = player()
    p.play("fanfare")
    assert made == []


def test_only_a_real_keypress_moving_through_the_interface_makes_the_navigation_sound():
    assert sounds.is_navigation(Qt.Key_Down, spontaneous=True, typing=False)
    assert sounds.is_navigation(Qt.Key_Tab, spontaneous=True, typing=False)
    assert not sounds.is_navigation(Qt.Key_Down, spontaneous=False, typing=False)  # posted for the pad, which plays its own
    assert not sounds.is_navigation(Qt.Key_Left, spontaneous=True, typing=True)  # moving a cursor in a text field
    assert not sounds.is_navigation(Qt.Key_A, spontaneous=True, typing=False)


@pytest.mark.parametrize("name", [sounds.NAVIGATE, sounds.PLAY])
def test_the_sound_files_are_small_valid_wav_files_with_their_licence(name):
    path = sounds.SOUNDS / f"{name}.wav"
    with wave.open(str(path)) as w:
        assert w.getnframes() / w.getframerate() < 1.0
    assert path.stat().st_size < 100_000
    assert "CC0" in (sounds.SOUNDS / "LICENSE.txt").read_text()


def test_sounds_are_on_by_default(tmp_path):
    from mog_client.config import Settings

    assert Settings().sounds is True


def test_closing_stops_what_is_playing_and_plays_nothing_more():
    p, made, clock = player()
    p.play(sounds.NAVIGATE)
    p.close()
    assert made[0].stopped and made[0].deleted
    clock[0] += 1
    p.play(sounds.NAVIGATE)
    p.play(sounds.PLAY)
    assert len(made) == 1 and made[0].plays == 1  # nothing new, and nothing loaded again


def test_closing_a_player_that_never_played_or_cannot_is_harmless():
    p, _, _ = player()
    p.close()
    broken, _, _ = player(factory=lambda path, volume: object())  # an effect without stop()
    broken.play(sounds.PLAY)
    broken.close()
