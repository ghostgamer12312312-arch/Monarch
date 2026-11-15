from concurrent.futures import ThreadPoolExecutor

import pytest

from monarchbot.marketplace import MarketplaceService
from monarchbot.service import GameError


def register(game, user_id):
    game[0].register(user_id, None, f"Hunter {user_id}")


def test_market_escrows_item_settles_fee_and_transfers_once(game):
    service, _, _ = game
    register(game, 101)
    register(game, 202)
    service.buy_item(101, "energy_shard", 2)
    market = MarketplaceService(service)
    listing = market.create_listing(101, "energy_shard", 1, 100)
    assert service.inventory(101)[0]["quantity"] == 1
    purchased = market.buy(202, listing["id"])
    assert purchased["fee"] == 5
    assert purchased["seller_receives"] == 95
    assert any(row["item_id"] == "energy_shard" and row["quantity"] == 1 for row in service.inventory(202))
    with pytest.raises(GameError, match="no longer available"):
        market.buy(202, listing["id"])


def test_market_cancel_returns_escrow_and_pagination_lists_newest(game):
    service, _, _ = game
    register(game, 101)
    service.buy_item(101, "energy_shard", 1)
    market = MarketplaceService(service)
    listing = market.create_listing(101, "energy_shard", 1, 30)
    market.cancel(101, listing["id"])
    assert service.inventory(101)[0]["quantity"] == 1


def test_market_expiry_refunds_items(game):
    service, _, epoch = game
    register(game, 101)
    service.buy_item(101, "energy_shard", 1)
    market = MarketplaceService(service)
    listing = market.create_listing(101, "energy_shard", 1, 30)
    epoch[0] += 8 * 24 * 60 * 60
    result = market.browse(101)
    assert result["total"] == 0
    assert service.inventory(101)[0]["quantity"] == 1
    with pytest.raises(GameError, match="no longer available"):
        market.buy(101, listing["id"])


def test_two_buyers_cannot_buy_same_listing(game):
    service, _, _ = game
    for user_id in (101, 202, 303):
        register(game, user_id)
    service.buy_item(101, "energy_shard", 1)
    market = MarketplaceService(service)
    listing = market.create_listing(101, "energy_shard", 1, 100)

    def attempt(user_id):
        try:
            return market.buy(user_id, listing["id"])
        except GameError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, (202, 303)))
    assert sum(isinstance(r, dict) for r in results) == 1


def test_banning_seller_cancels_listing_and_refunds_escrow(game):
    service, _, _ = game
    register(game, 101)
    register(game, 202)
    service.buy_item(101, "energy_shard", 1)
    market = MarketplaceService(service)
    listing = market.create_listing(101, "energy_shard", 1, 100)
    service.admin_ban(101, "rule violation", 999)
    with service.db.reader() as con:
        assert (
            con.execute(
                "SELECT quantity FROM inventory WHERE player_id=101 AND item_id='energy_shard'"
            ).fetchone()[0]
            == 1
        )
    with pytest.raises(GameError, match="no longer available"):
        market.buy(202, listing["id"])
