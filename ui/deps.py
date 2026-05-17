"""Shared singletons — DB and ScryfallClient — managed by the lifespan."""
import os
import sys

# Allow running as `python3 ui/app.py` from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database import Database, DB_PATH
from core.scryfall import ScryfallClient

_db_path = os.getenv("DB_PATH", DB_PATH)

db: Database = Database(_db_path)
scryfall: ScryfallClient = ScryfallClient()
