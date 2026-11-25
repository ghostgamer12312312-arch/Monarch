import pytest

from monarchbot import handlers
from monarchbot.catalog import GACHA_CHARACTERS, SKILLS, WORLD_BOSSES
from monarchbot.service import GameError


def register(service, user_id=101):
    return service.register(user_id, f"hunter{user_id}", f"Hunter {user_id}")


def fund(service, user_id=101, coins=0, essence=0):
    with service.db.transaction() as con:
        con.execute(
            "UPDATE players SET coins=coins+?,essence=essence+? WHERE telegram_id=?",
            (coins, essence, user_id),
        )


def test_skill_shop_and_owned_skill_in_battle(game):
    service, _, _ = game
    register(service)
    fund(service, coins=1000)
    assert service.skills(101)[0]["owned"] is True
    learned = service.learn_skill(101, "ember_slash")
    assert learned["name"] == SKILLS["ember_slash"]["name"]
    with pytest.raises(GameError):
        service.learn_skill(101, "ember_slash")
    battle = service.start_battle(101, "gate")
    after = service.battle_action(101, battle["id"], battle["turn"], "skill:ember_slash")
    assert after["enemy_hp"] < battle["enemy_hp"]
    assert any("Ember Slash" in line for line in after["result"]["log"])


def test_unowned_skill_is_rejected(game):
    service, _, _ = game
    register(service)
    battle = service.start_battle(101, "gate")
    with pytest.raises(GameError):
        service.battle_action(101, battle["id"], battle["turn"], "skill:shadow_domain")


def test_gacha_pull_collects_characters_and_grants_bonus(game):
    service, _, _ = game
    register(service)
    fund(service, essence=1000)
    result = service.gacha_pull(101, 10)
    assert len(result["results"]) == 10
    assert all(pull["id"] in GACHA_CHARACTERS for pull in result["results"])
    collection = service.gacha_collection(101)
    assert collection["owned"] >= 1
    assert collection["bonus_hp"] > 0 and collection["bonus_atk"] > 0
    assert collection["pity"] <= 10


def test_bank_deposit_and_withdraw(game):
    service, _, _ = game
    register(service)
    fund(service, coins=5000)
    deposit = service.bank_deposit(101, 1000)
    assert deposit["bank"] == 1000 and deposit["coins"] == 4100
    withdraw = service.bank_withdraw(101, 100)
    assert withdraw["bank"] == 900 and withdraw["coins"] == 4200
    with pytest.raises(GameError):
        service.bank_deposit(101, 50)


def test_guild_treasury_upgrade_and_world_boss(game):
    service, _, _ = game
    register(service)
    fund(service, coins=3000)
    service.create_guild(101, "Shadow Court")
    service.donate_guild(101, 500)
    treasury = service.guild_treasury(101)
    assert treasury["coins"] == 500 and treasury["role"] == "leader"
    upgraded = service.guild_upgrade(101)
    assert upgraded["level"] == 2
    boss = service.raid_status()["boss"]
    assert boss["name"] in {entry["name"] for entry in WORLD_BOSSES}
    assert boss["art"].startswith("boss_")
    strike = service.raid_attack(101)
    assert strike["damage"] > 0 and strike["boss"]


def test_battle_markup_offers_skill_picker_not_guard():
    markup = handlers._battle_markup({"id": "abc", "turn": 1, "result": {}})
    flat = [button for row in markup.inline_keyboard for button in row]
    labels = {button.text for button in flat}
    callbacks = {button.callback_data for button in flat}
    assert "🛡️ Guard" not in labels
    assert "🌀 Skills" in labels
    assert "b:abc:1:skills" in callbacks


class _FakeQuery:
    def __init__(self, data):
        self.data = data
        self.from_user = __import__("types").SimpleNamespace(id=101)
        self.message = None
        self.answers = []
        self.edits = []

    async def answer(self, text=None, **kwargs):
        self.answers.append({"text": text, **kwargs})

    async def edit_message_text(self, text, **kwargs):
        self.edits.append(text)

    async def edit_message_caption(self, caption=None, **kwargs):
        self.edits.append(caption or "")


class _FakeUpdate:
    def __init__(self, data):
        self.update_id = 1
        self.effective_user = _FakeQuery(data).from_user
        self.callback_query = _FakeQuery(data)
        self.effective_message = None


def _features_context(service):
    from types import SimpleNamespace

    return SimpleNamespace(
        application=SimpleNamespace(
            bot_data={
                "game": service,
                "settings": SimpleNamespace(admin_ids=frozenset()),
            }
        )
    )


@pytest.mark.asyncio
async def test_gacha_callback_pull_and_collection(game):
    service, _, _ = game
    register(service)
    fund(service, essence=500)
    update = _FakeUpdate("ga:pull:1")
    await handlers.features_callback(update, _features_context(service))
    assert update.callback_query.edits and "SUMMON" in update.callback_query.edits[0]
    update = _FakeUpdate("ga:collection")
    await handlers.features_callback(update, _features_context(service))
    assert "COLLECTION" in update.callback_query.edits[0]


@pytest.mark.asyncio
async def test_bank_callback_deposit(game):
    service, _, _ = game
    register(service)
    fund(service, coins=1000)
    update = _FakeUpdate("bk:dep:100")
    await handlers.features_callback(update, _features_context(service))
    assert "MONARCH BANK" in update.callback_query.edits[0]
    assert service.bank_status(101)["bank"] == 100


@pytest.mark.asyncio
async def test_shop_and_skill_callbacks(game):
    service, _, _ = game
    register(service)
    fund(service, coins=2000)
    update = _FakeUpdate("s:energy_shard:1")
    await handlers.features_callback(update, _features_context(service))
    assert "PURCHASE COMPLETE" in update.callback_query.edits[0]
    update = _FakeUpdate("sk:ember_slash")
    await handlers.features_callback(update, _features_context(service))
    assert "SKILL LEARNED" in update.callback_query.edits[0]
    assert any(row["id"] == "ember_slash" and row["owned"] for row in service.skills(101))


@pytest.mark.asyncio
async def test_world_boss_callback_attack_and_rank(game):
    service, _, _ = game
    register(service)
    update = _FakeUpdate("rb:attack")
    await handlers.features_callback(update, _features_context(service))
    assert "STRIKE DELIVERED" in update.callback_query.edits[0]
    update = _FakeUpdate("rb:rank")
    await handlers.features_callback(update, _features_context(service))
    assert "DAMAGE RANK" in update.callback_query.edits[0]
