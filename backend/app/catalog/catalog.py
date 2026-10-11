"""Справочник в памяти процесса: читается на каждом запросе, меняется раз в загрузку."""

import logging
from pathlib import Path

from app.catalog.loaders import load_catalog_csv, load_catalog_json
from app.catalog.model import CatalogError, Position

log = logging.getLogger(__name__)

_LOADERS = {".json": load_catalog_json, ".csv": load_catalog_csv}


class Catalog:
    def __init__(self, positions: list[Position], version: str) -> None:
        self.version = version
        self._by_code: dict[str, Position] = {}
        # Категория портала (id каталога у СТЕ) → позиция: двойники и похожие СТЕ знают только его.
        self._by_portal: dict[int, Position] = {}
        self._by_name: dict[str, list[Position]] = {}
        for p in positions:
            if p.code in self._by_code:
                first = self._by_code[p.code].source
                raise CatalogError(f"код {p.code} встречается дважды ({first}, {p.source})")
            self._by_code[p.code] = p
            if p.portal_id is not None:
                self._by_portal.setdefault(p.portal_id, p)
            self._by_name.setdefault(p.name, []).append(p)

    @classmethod
    def load(cls, paths: list[Path]) -> "Catalog":
        if not paths:
            raise CatalogError("не задан ни один файл справочника (CATALOG_PATHS)")
        positions: list[Position] = []
        for path in paths:
            if not path.is_file():
                raise CatalogError(f"файл справочника не найден: {path} (CATALOG_PATHS)")
            loader = _LOADERS.get(path.suffix.lower())
            if loader is None:
                raise CatalogError(f"{path}: неизвестный формат справочника, нужен .json или .csv")
            loaded = loader(path)
            log.info("справочник %s: %d позиций", path, len(loaded))
            positions.extend(loaded)
        if not positions:
            raise CatalogError(f"в файлах справочника нет позиций: {', '.join(map(str, paths))}")
        return cls(positions, version="+".join(p.name for p in paths))

    def get(self, code: str) -> Position | None:
        return self._by_code.get(code)

    def by_portal(self, portal_id: int | None) -> Position | None:
        return self._by_portal.get(portal_id) if portal_id is not None else None

    def same_name(self, name: str) -> list[Position]:
        """Позиции с ровно таким наименованием (КТРУ: одно наименование, разные значения)."""
        return list(self._by_name.get(name, []))

    def all(self) -> list[Position]:
        return list(self._by_code.values())

    def __len__(self) -> int:
        return len(self._by_code)
