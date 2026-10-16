import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "e2e: end-to-end test with Docker, a real server and real clients (MOG_E2E=1)")


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    """Each phase's report on the test item, so a fixture can tell whether the test itself failed."""
    report = yield
    setattr(item, f"rep_{report.when}", report)
    return report
