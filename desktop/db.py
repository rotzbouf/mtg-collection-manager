"""Shared Database and ScryfallClient singletons for the desktop app."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database import Database, DB_PATH
from core.scryfall import ScryfallClient

db = Database(os.getenv("DB_PATH", DB_PATH))
scryfall = ScryfallClient()
