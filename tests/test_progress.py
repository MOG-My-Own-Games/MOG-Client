from mog_client.progress import RateMeter, format_eta


def test_speed_and_eta_over_the_window():
    meter = RateMeter()
    meter.add(0.0, 0)
    assert meter.speed() is None  # one sample tells nothing
    meter.add(10.0, 10_000_000)
    assert meter.speed() == 1_000_000
    assert meter.eta(10_000_000, 40_000_000) == 30


def test_old_samples_age_out():
    meter = RateMeter(window=10)
    meter.add(0, 0)
    meter.add(5, 5_000_000)  # fast
    meter.add(30, 5_100_000)  # then slow
    meter.add(40, 5_200_000)
    assert meter.speed() < 20_000


def test_a_restarted_file_does_not_give_a_negative_speed():
    meter = RateMeter()
    meter.add(0, 5_000_000)
    meter.add(5, 100)
    meter.add(10, 1_000_100)
    assert meter.speed() == 200_000


def test_format_eta():
    assert [format_eta(s) for s in (5, 59.6, 61, 3599, 3661)] == ["5s", "1m 00s", "1m 01s", "59m 59s", "1h 01m"]
