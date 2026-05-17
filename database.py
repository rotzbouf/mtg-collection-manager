# backward-compat shim — actual implementation is in core/database.py
from core.database import *  # noqa: F401,F403
from core.database import Database, DB_PATH  # noqa: F401
