import importlib
import importlib.util


def test_project_exposes_whyhow_api_package():
    assert importlib.util.find_spec("whyhow_api") is not None


def test_fastapi_app_imports_without_optional_observability_dependencies():
    module = importlib.import_module("whyhow_api.main")

    assert module.app.title == "WhyHow API"
