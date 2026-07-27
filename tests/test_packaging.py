import importlib.util


def test_project_exposes_whyhow_api_package():
    assert importlib.util.find_spec("whyhow_api") is not None
