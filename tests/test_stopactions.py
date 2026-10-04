from mog_client import trace
from mog_client.stopactions import StopActions


def test_an_action_for_an_install_that_is_not_running_runs_now_and_leaves_nothing_behind():
    actions, ran = StopActions(), []
    assert actions.register(7, lambda: ran.append("discard"), running=False) is False
    assert ran == ["discard"]
    assert actions.take(7) is None  # nothing pending to fire at the end of the next install


def test_an_action_for_a_running_install_waits_for_it_and_fires_once():
    actions, ran = StopActions(), []
    assert actions.register(7, lambda: ran.append("discard"), running=True) is True
    assert ran == []
    action = actions.take(7)
    action()
    assert ran == ["discard"] and actions.take(7) is None


def test_a_new_install_does_not_inherit_what_was_asked_of_an_earlier_one():
    actions = StopActions()
    actions.register(7, lambda: None, running=True)
    actions.forget(7)
    assert actions.take(7) is None


def test_the_trace_keeps_the_earlier_downloads(tmp_path):
    trace.start("first")
    trace.event("a")
    trace.start("second")
    trace.event("b")
    text = trace.trace_path().read_text()
    assert text.index("first") < text.index("second") and "] a" in text and "] b" in text
