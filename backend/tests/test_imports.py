"""Каждый модуль приложения импортируется (20 из 101 модуля — инструменты,
воркер — не загружал ни один тест, и сломанный импорт в них нашёлся бы только на стенде)."""

import importlib
import pkgutil

import pytest

import app


def _modules() -> list[str]:
    return sorted(m.name for m in pkgutil.walk_packages(app.__path__, prefix="app."))


@pytest.mark.parametrize("name", _modules())
def test_module_imports(name: str) -> None:
    importlib.import_module(name)
