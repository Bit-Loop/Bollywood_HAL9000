from __future__ import annotations

from hal9000.clicks import SpeakerClickAggregator


def test_double_click_is_delayed_until_triple_window_expires(qtbot) -> None:
    clicks = SpeakerClickAggregator(interval_ms=45)
    doubles: list[bool] = []
    triples: list[bool] = []
    clicks.doubleClick.connect(lambda: doubles.append(True))
    clicks.tripleClick.connect(lambda: triples.append(True))

    clicks.registerClick()
    clicks.registerClick()
    assert doubles == []
    qtbot.waitUntil(lambda: len(doubles) == 1, timeout=500)
    assert triples == []


def test_triple_click_cancels_pending_double(qtbot) -> None:
    clicks = SpeakerClickAggregator(interval_ms=55)
    doubles: list[bool] = []
    triples: list[bool] = []
    clicks.doubleClick.connect(lambda: doubles.append(True))
    clicks.tripleClick.connect(lambda: triples.append(True))

    for _ in range(3):
        clicks.registerClick()

    qtbot.waitUntil(lambda: len(triples) == 1, timeout=300)
    qtbot.wait(90)
    assert doubles == []
    assert triples == [True]
