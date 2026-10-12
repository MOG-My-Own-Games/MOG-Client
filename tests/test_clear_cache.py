import pytest

from mog_client.api import MogClient


class _Answers:
    def __init__(self, status, data):
        self.answer = (status, data)

    def delete_json(self, path, **kw):
        assert path == "/api/games/7/install"
        return self.answer


@pytest.mark.parametrize(
    "status, data, had_one",
    [
        (200, {"cleared": 1}, True),
        (200, {"cleared": 0}, False),  # the server says there was none
        (404, {"detail": "Not Found"}, False),  # an older server says it the other way
        (204, None, True),
    ],
)
def test_clearing_the_server_cache_says_whether_there_was_one(status, data, had_one):
    assert MogClient(_Answers(status, data)).clear_cache(7) is had_one


def test_a_cache_in_use_is_still_an_error():
    with pytest.raises(RuntimeError):
        MogClient(_Answers(409, {"detail": "Install is still running"})).clear_cache(7)
