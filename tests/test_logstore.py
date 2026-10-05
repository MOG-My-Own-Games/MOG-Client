import logging
import threading

from mog_client import api, logstore


def fresh(capacity=5):
    return logstore.Store(capacity)


def test_records_carry_a_level_a_time_and_the_text():
    store = fresh()
    logger = logging.getLogger("test.logstore.levels")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    logger.addHandler(store)

    logger.debug("d")
    logger.info("i")
    logger.warning("w")
    logger.error("e")
    logger.critical("c")

    assert [(r.level, r.text) for r in store.records()] == [
        ("info", "d"),
        ("info", "i"),
        ("warning", "w"),
        ("error", "e"),
        ("error", "c"),
    ]
    assert all(r.time > 0 for r in store.records())


def test_only_the_newest_records_are_kept():
    store = fresh(3)
    logger = logging.getLogger("test.logstore.ring")
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.addHandler(store)
    for n in range(5):
        logger.info(str(n))
    assert [r.text for r in store.records()] == ["2", "3", "4"]
    store.clear()
    assert store.records() == []


def test_subscribers_hear_new_records_until_they_unsubscribe_and_cannot_break_logging():
    store = fresh()
    logger = logging.getLogger("test.logstore.subs")
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.addHandler(store)
    heard = []
    stop = store.subscribe(lambda r: heard.append(r.text))
    store.subscribe(lambda r: 1 / 0)

    logger.info("one")
    stop()
    logger.info("two")

    assert heard == ["one"] and [r.text for r in store.records()] == ["one", "two"]


def test_many_threads_can_log_at_once():
    store = fresh(1000)
    logger = logging.getLogger("test.logstore.threads")
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.addHandler(store)

    def work(n):
        for i in range(50):
            logger.info(f"{n}-{i}")

    threads = [threading.Thread(target=work, args=(n,)) for n in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(store.records()) == 400


def test_the_transfer_helpers_feed_the_store(capsys):
    logstore.store.clear()
    api.log("downloading")
    api.warn("hash mismatch")
    out = capsys.readouterr()
    assert out.out == "downloading\n" and "WARN: hash mismatch" in out.err
    assert [(r.level, r.text) for r in logstore.store.records()] == [("info", "downloading"), ("warning", "hash mismatch")]
    logstore.store.clear()


def test_the_log_can_also_go_to_a_file_that_stays_small(tmp_path):
    path = tmp_path / "logs" / "mog-client.log"
    logstore.write_to_file(path)
    logstore.write_to_file(path)  # twice adds no second handler
    logstore.error("it broke")
    for handler in logstore.logger.handlers:
        handler.flush()
    assert "ERROR it broke" in path.read_text()
    assert sum(isinstance(h, logging.handlers.RotatingFileHandler) for h in logstore.logger.handlers) == 1
    for handler in list(logstore.logger.handlers):
        if isinstance(handler, logging.handlers.RotatingFileHandler):
            logstore.logger.removeHandler(handler)
            handler.close()
    logstore.store.clear()
