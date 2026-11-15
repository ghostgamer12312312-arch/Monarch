from __future__ import annotations

import logging

from .config import load_settings
from .database import Database
from .handlers import build_application
from .service import GameService


def main() -> None:
    settings = load_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    db = Database(settings.database_path)
    db.initialize()
    game = GameService(db)
    application = build_application(settings, game)
    logging.getLogger(__name__).info("Starting Monarch polling bot; database=%s", settings.database_path)
    try:
        application.run_polling(allowed_updates=None, drop_pending_updates=False)
    finally:
        db.close()


if __name__ == "__main__":
    main()
