from __future__ import annotations

import random
from datetime import UTC, datetime

import pytest

from monarchbot.database import Database
from monarchbot.service import GameService


class FixedRandom(random.Random):
    def choice(self, sequence):
        return sequence[0]

    def randint(self, a, b):
        return a

    def uniform(self, a, b):
        return a


@pytest.fixture
def game(tmp_path):
    db = Database(tmp_path / "monarch-test.sqlite3")
    db.initialize()
    clock = [datetime(2026, 1, 5, 12, tzinfo=UTC)]
    epoch = [1_767_614_400]
    service = GameService(db, rng=FixedRandom(1), clock=lambda: clock[0], epoch=lambda: epoch[0])
    yield service, clock, epoch
    db.close()
