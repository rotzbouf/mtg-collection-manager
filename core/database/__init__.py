"""MTG Collection Manager database — public API.

Internal structure:
  _common.py     — shared constants (DB_PATH, _SCHEMA, _SORT_MAP, _row_to_dict)
  _schema.py     — _SchemaMixin  (initialize, _migrate, close, commit)
  _cards.py      — _CardsMixin   (add, remove, update, resync, bulk reads)
  _search.py     — _SearchMixin  (count_search, search, count_cards, advanced_search, list_cards)
  _prices.py     — _PricesMixin  (record_prices, update_card_prices, get_price_history, stats, …)
  _containers.py — _ContainersMixin (CRUD + overcount queries)
  _backup.py     — _BackupMixin  (backup_bytes, inspect_backup, restore_from_bytes)
  _legality.py   — _LegalityMixin (format_bans, overrides, rebuild_format_bans)
"""
import asyncio

from ._common import DB_PATH
from ._schema import _SchemaMixin
from ._cards import _CardsMixin
from ._search import _SearchMixin
from ._prices import _PricesMixin
from ._containers import _ContainersMixin
from ._backup import _BackupMixin
from ._legality import _LegalityMixin


class Database(
    _SchemaMixin,
    _CardsMixin,
    _SearchMixin,
    _PricesMixin,
    _ContainersMixin,
    _BackupMixin,
    _LegalityMixin,
):
    def __init__(self, path: str = DB_PATH):
        self.path = path
        self._db = None
        self._write_lock = asyncio.Lock()
        self._restore_lock = asyncio.Lock()


__all__ = ["Database", "DB_PATH"]
