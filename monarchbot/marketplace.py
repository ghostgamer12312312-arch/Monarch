from __future__ import annotations

from .catalog import ITEMS, MARKET_FEE_PERCENT, MARKET_MAX_PRICE, MARKET_TTL_SECONDS
from .service import GameError, GameService


class MarketplaceService:
    def __init__(self, game: GameService):
        self.game = game
        self.db = game.db

    @staticmethod
    def _credit_item(con, player_id: int, item_id: str, quantity: int) -> None:
        con.execute(
            "INSERT INTO inventory(player_id,item_id,quantity) VALUES(?,?,?) ON CONFLICT(player_id,item_id) DO UPDATE SET quantity=quantity+excluded.quantity",
            (player_id, item_id, quantity),
        )

    def _expire(self, con, now: int) -> int:
        rows = con.execute(
            "SELECT id,seller_id,item_id,quantity FROM market_listings WHERE status='active' AND expires_at<=?",
            (now,),
        ).fetchall()
        for row in rows:
            self._credit_item(con, int(row["seller_id"]), row["item_id"], int(row["quantity"]))
            con.execute(
                "UPDATE market_listings SET status='expired' WHERE id=? AND status='active'", (row["id"],)
            )
        return len(rows)

    def _expire_commit(self, now: int) -> int:
        with self.db.transaction() as con:
            return self._expire(con, now)

    def create_listing(self, seller_id: int, item_id: str, quantity: int, price: int) -> dict:
        if item_id not in ITEMS:
            raise GameError("That item cannot be listed. Check /shop.")
        if not 1 <= quantity <= 99:
            raise GameError("Listing quantity must be from 1 to 99.")
        if not 1 <= price <= MARKET_MAX_PRICE:
            raise GameError(f"Price must be from 1 to {MARKET_MAX_PRICE:,} coins.")
        now = int(self.game.epoch())
        self._expire_commit(now)
        with self.db.transaction() as con:
            self.game._require_player(con, seller_id)
            inv = con.execute(
                "SELECT quantity FROM inventory WHERE player_id=? AND item_id=?", (seller_id, item_id)
            ).fetchone()
            if inv is None or int(inv["quantity"]) < quantity:
                raise GameError("You do not have enough of that item.")
            con.execute(
                "UPDATE inventory SET quantity=quantity-? WHERE player_id=? AND item_id=?",
                (quantity, seller_id, item_id),
            )
            cur = con.execute(
                "INSERT INTO market_listings(seller_id,item_id,quantity,price,expires_at) VALUES(?,?,?,?,?)",
                (seller_id, item_id, quantity, price, now + MARKET_TTL_SECONDS),
            )
            return {
                "id": int(cur.lastrowid),
                "item": ITEMS[item_id]["name"],
                "quantity": quantity,
                "price": price,
            }

    def browse(self, player_id: int, offset: int = 0) -> dict:
        if not 0 <= offset <= 100_000:
            raise GameError("Invalid market page.")
        now = int(self.game.epoch())
        self._expire_commit(now)
        with self.db.reader() as con:
            self.game._require_player(con, player_id)
            rows = con.execute(
                """SELECT m.id,m.seller_id,p.display_name AS seller_name,m.item_id,m.quantity,m.price,m.expires_at
                   FROM market_listings m JOIN players p ON p.telegram_id=m.seller_id
                   WHERE m.status='active' ORDER BY m.created_at DESC,m.id DESC LIMIT 10 OFFSET ?""",
                (offset,),
            ).fetchall()
            total = con.execute("SELECT COUNT(*) FROM market_listings WHERE status='active'").fetchone()[0]
            return {
                "items": [{**dict(row), "item_name": ITEMS[row["item_id"]]["name"]} for row in rows],
                "offset": offset,
                "total": total,
                "next_offset": offset + 10 if offset + 10 < total else None,
            }

    def buy(self, buyer_id: int, listing_id: int) -> dict:
        if listing_id <= 0:
            raise GameError("Listing ID must be a positive number.")
        now = int(self.game.epoch())
        self._expire_commit(now)
        with self.db.transaction() as con:
            buyer = self.game._require_player(con, buyer_id)
            listing = con.execute(
                "SELECT m.*,p.display_name AS seller_name FROM market_listings m JOIN players p ON p.telegram_id=m.seller_id WHERE m.id=?",
                (listing_id,),
            ).fetchone()
            if listing is None or listing["status"] != "active":
                raise GameError("That listing is no longer available.")
            seller_id = int(listing["seller_id"])
            if seller_id == buyer_id:
                raise GameError("You cannot buy your own listing.")
            if con.execute("SELECT 1 FROM banned_players WHERE telegram_id=?", (seller_id,)).fetchone():
                raise GameError("That seller is restricted; their listing cannot be purchased.")
            if int(buyer["coins"]) < int(listing["price"]):
                raise GameError(f"You need {int(listing['price']):,} coins for that listing.")
            price = int(listing["price"])
            fee = price * MARKET_FEE_PERCENT // 100
            payout = price - fee
            con.execute("UPDATE players SET coins=coins-? WHERE telegram_id=?", (price, buyer_id))
            con.execute("UPDATE players SET coins=coins+? WHERE telegram_id=?", (payout, seller_id))
            self._credit_item(con, buyer_id, listing["item_id"], int(listing["quantity"]))
            changed = con.execute(
                "UPDATE market_listings SET status='sold',buyer_id=?,sold_at=CURRENT_TIMESTAMP WHERE id=? AND status='active'",
                (buyer_id, listing_id),
            )
            if changed.rowcount != 1:
                raise GameError("That listing was purchased by another hunter.")
            return {
                "item": ITEMS[listing["item_id"]]["name"],
                "quantity": int(listing["quantity"]),
                "price": price,
                "fee": fee,
                "seller_receives": payout,
                "seller": listing["seller_name"],
            }

    def cancel(self, seller_id: int, listing_id: int) -> dict:
        now = int(self.game.epoch())
        self._expire_commit(now)
        with self.db.transaction() as con:
            self.game._require_player(con, seller_id)
            row = con.execute(
                "SELECT seller_id,item_id,quantity,status FROM market_listings WHERE id=?", (listing_id,)
            ).fetchone()
            if row is None or int(row["seller_id"]) != seller_id:
                raise GameError("That is not your listing.")
            if row["status"] != "active":
                raise GameError("That listing is no longer active.")
            con.execute(
                "UPDATE market_listings SET status='cancelled' WHERE id=? AND status='active'", (listing_id,)
            )
            self._credit_item(con, seller_id, row["item_id"], int(row["quantity"]))
            return {"item": ITEMS[row["item_id"]]["name"], "quantity": int(row["quantity"])}
