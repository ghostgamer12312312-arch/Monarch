from __future__ import annotations

import logging
import threading

from .config import load_settings
from .database import Database
from .handlers import build_application
from .service import GameService
from .social import SocialService


def _weekly_reset_loop(social: SocialService, stop: threading.Event) -> None:
    while not stop.wait(3600):
        try:
            count = social.reset_weekly_raids()
            if count:
                logging.getLogger(__name__).info("Reset %s guild raid(s) for the new UTC week", count)
        except Exception:
            logging.getLogger(__name__).exception("Weekly guild raid reset failed")


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
    stop = threading.Event()
    reset_thread = threading.Thread(target=_weekly_reset_loop, args=(SocialService(game), stop), daemon=True)
    reset_thread.start()
    logging.getLogger(__name__).info("Starting Monarch polling bot; database=%s", settings.database_path)
    try:
        application.run_polling(allowed_updates=None, drop_pending_updates=False)
    finally:
        stop.set()
        db.close()


if __name__ == "__main__":
    main()
