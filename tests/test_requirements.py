from pathlib import Path


def test_requirements_are_portable():
    requirements = Path("requirements.txt").read_text()

    assert "file:///" not in requirements
    assert "linux_x86_64" not in requirements
    assert "/root/" not in requirements
