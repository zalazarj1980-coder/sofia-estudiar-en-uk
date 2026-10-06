from pathlib import Path


REQUIREMENTS_PATH = Path(__file__).parents[1] / "requirements.txt"


def test_sqlalchemy_asyncio_extra_is_installed_for_async_engine():
    requirements = REQUIREMENTS_PATH.read_text(encoding="utf-8").splitlines()

    assert any(line.lower().startswith("sqlalchemy[asyncio]") for line in requirements)
    assert not any(line.lower().startswith("sqlalchemy>=") for line in requirements)
