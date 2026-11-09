"""BossBot: waterworks lock, stealing, chaining, and clean seeded rounds against every bot."""

import pytest

from bots.boss_bot import BossBot
from bots.government_bot import GovernmentBot
from bots.random_bot import RandomBot
from bots.tenchijin_bot import TenchijinBot
from leakbots import ActionType, BaseBot, Dir, Game, GameConfig, PipeState, PowerUp, Surface

H = Dir.LEFT.bit | Dir.RIGHT.bit
SMALL_MASKS = [[0] * 5, [0] * 5, [H] * 5, [0] * 5, [0] * 5]  # straight pipe along y=2
LEAK = (2, 2)
SMALL = GameConfig(width=5, height=5, max_moves=20, n_leaks=1, punk_spawn_move=0, extra_leak_moves=())


class IdleBot(BaseBot):
    def act(self, view):
        return None


def around_waterworks(g):
    return [p for p in g.connected_neighbors(g.waterworks_pos)] + [
        d.step(g.waterworks_pos) for d in Dir
        if g.in_bounds(d.step(g.waterworks_pos)) and d.step(g.waterworks_pos) not in g.connected_neighbors(g.waterworks_pos)
    ]


# --- Waterworks lock ---------------------------------------------------------------------------------
@pytest.mark.parametrize("seed", range(5))
def test_locks_every_square_around_the_waterworks_early(seed):
    g = Game([BossBot, IdleBot], GameConfig(), seed=seed)
    while g.move < 80:
        g.step()
    for pos in around_waterworks(g):
        cell = g.cell(pos)
        assert cell.surface is Surface.UNEARTHED or cell.occupant is not None, pos


def test_rivals_rarely_shop_but_boss_still_buys_its_satellite():
    # GovernmentBot sometimes reaches the shop before the lock is complete (it buys UT from move ~16)
    rival_buys = 0
    for seed in range(6):
        g = Game([BossBot, GovernmentBot], GameConfig(), seed=100 + seed).run()
        assert PowerUp.SATELLITE in g.states[0].upgrades
        rival_buys += bool(g.states[1].upgrades & {PowerUp.UT, PowerUp.SATELLITE})
    assert rival_buys <= 1


@pytest.mark.parametrize("seed", range(5))
def test_lock_pipe_holes_are_refilled_by_the_end(seed):
    g = Game([BossBot, IdleBot], GameConfig(), seed=seed).run()
    for pos in around_waterworks(g):
        cell = g.cell(pos)
        assert not (cell.surface is Surface.UNEARTHED and cell.has_pipe), pos  # -2 each otherwise
    assert g.states[0].open_holes <= 1  # at most a leak found too late to repair


def test_no_lock_in_single_player():
    g = Game([BossBot], GameConfig(), seed=4)
    while g.move < 120:
        g.step()
    holes = [p for p in around_waterworks(g) if g.cell(p).surface is Surface.UNEARTHED]
    assert not holes


# --- Stealing and chaining ---------------------------------------------------------------------------
def test_fixes_a_leak_an_opponent_prepared():
    g = Game([BossBot, IdleBot], SMALL, seed=0, masks=SMALL_MASKS, leaks=[LEAK], spawns=[(2, 1), (0, 4)])
    for pos in (LEAK, (1, 2), (3, 2)):  # the opponent dug everything and closed both valves
        c = g.cell(pos)
        c.surface, c.revealed, c.dug_by = Surface.UNEARTHED, True, 1
    for pos in ((1, 2), (3, 2)):
        c = g.cell(pos)
        c.valve, c.valve_open, c.valve_closer = True, False, 1
    event = g.step()
    assert event.action.type is ActionType.FIX and event.result.points == 40
    assert g.cell(LEAK).pipe_state is PipeState.FIXED


def test_chains_an_adjacent_opponent_as_a_punk():
    g = Game([BossBot, IdleBot], SMALL, seed=0, masks=SMALL_MASKS, leaks=[LEAK], spawns=[(0, 0), (1, 0)])
    g.states[0].upgrades.add(PowerUp.PUNK)
    event = g.step()
    assert event.action.type is ActionType.CHAIN and g.states[1].chained_left > 0


# --- Seeded rounds -----------------------------------------------------------------------------------
LINEUPS = {
    "solo": ([BossBot], 8),
    "vs random": ([BossBot, RandomBot], 8),
    "vs government": ([GovernmentBot, BossBot], 3),
    "vs tenchijin": ([BossBot, TenchijinBot], 2),
    "royale": ([TenchijinBot, BossBot, GovernmentBot], 2),
}


@pytest.fixture(scope="module")
def played():
    rounds = {}
    for name, (lineup, n) in LINEUPS.items():
        rounds[name] = [Game(lineup, GameConfig(), seed=500 + s).run() for s in range(n)]
    return rounds


@pytest.mark.parametrize("name", LINEUPS)
def test_rounds_run_clean(played, name):
    for g in played[name]:
        me = next(i for i, b in enumerate(g.bots) if isinstance(b, BossBot))
        st = g.states[me]
        assert st.last_error is None
        assert st.invalid <= 0.02 * st.actions
        assert st.closed_valves == 0
        assert st.open_holes <= 1  # only a leak found too late to repair


def test_beats_random_bot_every_round(played):
    assert all(g.winners() == [0] for g in played["vs random"])


def test_usually_wins_against_the_example_bots(played):
    # Measured over 40 rounds per matchup: ~90 % vs GovernmentBot, ~97 % vs TenchijinBot, ~87 % royale
    games = [g for name in ("vs government", "vs tenchijin", "royale") for g in played[name]]
    wins = sum(
        g.winners() == [next(i for i, b in enumerate(g.bots) if isinstance(b, BossBot))] for g in games
    )
    assert wins >= len(games) - 2


def test_solo_scores_like_a_strong_bot(played):
    scores = [g.states[0].score for g in played["solo"]]
    assert sum(scores) / len(scores) > 400
