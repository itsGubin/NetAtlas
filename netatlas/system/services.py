"""System-level services: facts about the running NetAtlas instance itself."""

from dataclasses import dataclass
from typing import Literal

from django.db import DatabaseError, connection

from netatlas import __version__

DatabaseState = Literal["up", "down"]


@dataclass(frozen=True)
class SystemStatus:
    version: str
    database: DatabaseState

    @property
    def is_healthy(self) -> bool:
        return self.database == "up"


def get_system_status() -> SystemStatus:
    return SystemStatus(version=__version__, database=_database_state())


def _database_state() -> DatabaseState:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except DatabaseError:
        return "down"
    return "up"
