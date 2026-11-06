"""English / Japanese text for the viewer and the rulebook.

Every key holds both languages, so a missing translation is impossible by construction.
Japanese is drawn with the bundled M PLUS Rounded 1c subset font: after adding Japanese text,
re-run ``python tools/subset_font.py`` (``tests/test_i18n.py`` fails on missing glyphs).
"""

from __future__ import annotations

from ..config import GameConfig

LANGS = ("en", "ja")

# key: (English, Japanese)
_TEXT: dict[str, tuple[str, str]] = {
    # Header / status
    "subtitle": ("ARENA  ·  find and fix the leaks", "アリーナ ・ 漏水を見つけて直そう"),
    "round": ("ROUND {r}/{max}", "ラウンド {r}/{max}"),
    "mode_single": ("SINGLE PLAYER", "シングルプレイ"),
    "mode_duel": ("1 VS 1", "1対1"),
    "mode_royale": ("BATTLE ROYALE {n}", "バトルロイヤル {n}"),
    "leaks": ("LEAKS found {f}  fixed {x} / {t}", "漏水  発見 {f}  修理 {x} / {t}"),
    "seed": ("seed {s}", "シード {s}"),
    # Buttons
    "play": ("PLAY", "スタート"),
    "pause": ("PAUSE", "一時停止"),
    "step": ("STEP", "1手進む"),
    "restart": ("RESTART", "リスタート"),
    "new_map": ("NEW MAP", "新しいマップ"),
    "simulate": ("SIMULATE {n}", "{n}回シミュレート"),
    "cancel_sim": ("CANCEL SIM", "シミュレート中止"),
    "pipes_ghost": ("PIPES: GHOST", "配管: 半透明"),
    "pipes_hidden": ("PIPES: HIDDEN", "配管: 非表示"),
    "marks_on": ("MARKS: ON", "マーク: オン"),
    "marks_off": ("MARKS: OFF", "マーク: オフ"),
    "rules": ("RULES", "ルール"),
    "rulebook_title": ("RULEBOOK", "ルールブック"),
    "close": ("CLOSE", "閉じる"),
    # Speed + scoreboard
    "speed": ("SPEED", "速度"),
    "rate": ("{n} moves/s", "{n} 手/秒"),
    "max": ("MAX", "最大"),
    "scoreboard": ("SCOREBOARD", "スコアボード"),
    "card_counts": ("pipes {p}  found {f}  fixed {x}", "配管 {p}  発見 {f}  修理 {x}"),
    "card_holes_live": ("holes {h} (-{pen} at end)  invalid {i}", "穴 {h} (終了時 -{pen})  無効 {i}"),
    "card_holes_final": ("holes {h} (-{pen})  invalid {i}", "穴 {h} (-{pen})  無効 {i}"),
    "error": ("error: {e}", "エラー: {e}"),
    # Simulation panel
    "sim_title": ("SIMULATION", "シミュレーション"),
    "sim_idle": ("Plays {n} headless games with\nthe current bots on fresh maps.",
                 "今のボットで新しいマップを\n{n}回、画面なしで対戦します。"),
    "sim_progress": ("{d}/{t} games  (seeds {s}+)", "{d}/{t} ゲーム  (シード {s}〜)"),
    "sim_head": ("{g} games  {sec}s  seed {s}", "{g}ゲーム  {sec}秒  シード {s}"),
    "sim_cancelled": ("(cancelled)", "(中止)"),
    "sim_avg": ("avg {a}", "平均 {a}"),
    "sim_wins": ("wins {w}%", "勝率 {w}%"),
    "sim_minmax": ("min {lo}  max {hi}  invalid {i}", "最小 {lo}  最大 {hi}  無効 {i}"),
    "sim_counts": ("pipes {p}  found {f}  fixed {x}  holes {h}", "配管 {p}  発見 {f}  修理 {x}  穴 {h}"),
    # Hints + toasts
    "hint_drop": ("Drag a PNG onto a bot card to set its sprite", "PNGをボットのカードにドロップで画像変更"),
    "hint_web_sprite": ("Sprites: commit bots/sprites/<ClassName>.png", "画像: bots/sprites/<クラス名>.png をコミット"),
    "hint_keys1": ("SPACE play  S step  R restart  N new  H rules", "スペース:再生  S:1手  R:リスタート  N:新マップ  H:ルール"),
    "hint_keys2": ("P pipes  D marks  M sim  L lang  UP/DN speed", "P:配管  D:マーク  M:シミュ  L:言語  ↑↓:速度"),
    "toast_png_only": ("Only .png files can be used as sprites", "画像は .png ファイルのみ使えます"),
    "toast_bad_image": ("Could not read that image", "画像を読み込めませんでした"),
    "toast_sprite_saved": ("Sprite saved for {name}", "{name} の画像を保存しました"),
    # Game over
    "game_over": ("GAME OVER", "ゲームオーバー"),
    "final_score": ("Final score: {s}", "最終スコア: {s}"),
    "winner": ("Winner: {names} ({s})", "勝者: {names} ({s})"),
    "tie": ("Tie: {names} ({s})", "引き分け: {names} ({s})"),
    "game_over_hint": (
        "RESTART replays this map  ·  NEW MAP rolls a new one",
        "リスタート: 同じマップ  ・  新しいマップ: 別のマップ",
    ),
}

STRINGS: dict[str, dict[str, str]] = {lang: {k: v[i] for k, v in _TEXT.items()} for i, lang in enumerate(LANGS)}


def tr(lang: str, key: str, **values: object) -> str:
    """Translated text for key, with {placeholders} filled in."""
    return STRINGS[lang][key].format(**values)


# --- Rulebook ----------------------------------------------------------------------------------
# Each section: (title, [(label or "", text), ...]). Placeholders come from GameConfig.
Section = tuple[str, list[tuple[str, str]]]

_RULES: dict[str, dict[str, list[Section]]] = {
    "en": {
        "left": [
            ("GOAL", [
                ("", "Find buried water leaks, fix them and leave no open pipe holes. "
                     "The highest score after {rounds} rounds wins."),
            ]),
            ("TURNS", [
                ("", "Bots act one at a time in a fixed order: exactly one action per turn."),
                ("", "One round = every bot acts once. A game always lasts {rounds} rounds."),
                ("", "Single player: your bot acts every turn."),
            ]),
            ("ACTIONS", [
                ("", "Each action targets the neighbouring square UP, DOWN, LEFT or RIGHT, "
                     "never the square the bot stands on."),
                ("MOVE", "Step onto an untouched, empty square. Holes and other bots block the way."),
                ("DETECT", "Learn privately whether a pipe is buried there. Works on any square inside the grid."),
                ("UNEARTH", "Dig the square open. It must be untouched and empty."),
                ("FIX", "Repair a broken pipe in an unearthed square."),
                ("REFILL", "Fill any hole back in, whoever dug it. A leaking pipe must be fixed first."),
            ]),
        ],
        "right": [
            ("LIMITS", [
                ("", "Bots can never leave the {w} x {h} grid."),
                ("", "One bot or object per square. Bots cannot stand on unearthed squares."),
                ("", "An invalid action (blocked, off the grid, wrong target, bot crash) "
                     "wastes the turn and counts as an invalid move."),
            ]),
            ("WHAT BOTS SEE", [
                ("", "The whole map: untouched or unearthed squares, pipes in unearthed squares, "
                     "which way they run and whether they are OK, broken or fixed."),
                ("", "Buried pipes stay hidden until detected or unearthed. Detect results are private."),
                ("", "Once a square has been unearthed, its pipe stays known to everyone, even after a refill."),
                ("", "Bots are not told who dug a hole: keep track of your own."),
            ]),
            ("SCORING", [
                ("+{pd}", "Detect a pipe square for the first time. Only once per square per bot, "
                          "and never for a square that was ever unearthed."),
                ("+{lf}", "Find a leak: unearth a broken pipe (once per leak)."),
                ("+{lx}", "Fix a leak."),
                ("-{pen}", "At game end, for every pipe hole you dug that is still open."),
                ("", "Ties: fewer invalid moves wins, then whoever reached their score first."),
            ]),
        ],
    },
    "ja": {
        "left": [
            ("目的", [
                ("", "地中の配管の漏水を見つけて修理し、穴を開けたままにしないこと。"
                     "{rounds}ラウンド後に最も得点の高いボットの勝ち。"),
            ]),
            ("ターン", [
                ("", "ボットは決まった順番で、1ターンに1回だけ行動する。"),
                ("", "全ボットが1回ずつ行動すると1ラウンド。ゲームは必ず{rounds}ラウンド続く。"),
                ("", "シングルプレイでは毎ターン自分のボットが行動する。"),
            ]),
            ("行動", [
                ("", "どの行動も上下左右いずれかの隣のマスが対象。自分が立っているマスには使えない。"),
                ("移動", "未掘削で空いているマスへ進む。穴と他のボットは通れない。"),
                ("探知", "そのマスに配管が埋まっているかを自分だけが知る。盤面内ならどのマスでも可。"),
                ("掘削", "マスを掘り起こす。未掘削で空いているマスのみ。"),
                ("修理", "掘り起こしたマスの壊れた配管を直す。"),
                ("埋め戻し", "誰が掘った穴でも埋め戻せる。漏水中の配管は先に修理が必要。"),
            ]),
        ],
        "right": [
            ("制限", [
                ("", "ボットは {w} × {h} の盤面の外には出られない。"),
                ("", "1マスにはボットか物体が1つだけ。掘り起こしたマスには立てない。"),
                ("", "無効な行動(通れない、盤面外、対象が違う、ボットのエラー)は"
                     "ターンを消費し、無効手として数えられる。"),
            ]),
            ("ボットに見えるもの", [
                ("", "盤面全体:未掘削か掘削済みか、掘削済みマスの配管、その向き、"
                     "正常・破損・修理済みの状態。"),
                ("", "埋まった配管は探知か掘削をするまで見えない。探知の結果は自分だけのもの。"),
                ("", "一度掘り起こされたマスの配管は、埋め戻した後も全員に知られたまま。"),
                ("", "誰が穴を掘ったかは教えてもらえない。自分の穴は自分で記録しよう。"),
            ]),
            ("得点", [
                ("+{pd}", "配管のマスを初めて探知。各ボット1マス1回だけで、"
                          "一度でも掘り起こされたマスは対象外。"),
                ("+{lf}", "漏水を発見:壊れた配管を掘り起こす(漏水ごとに1回)。"),
                ("+{lx}", "漏水を修理する。"),
                ("-{pen}", "ゲーム終了時、自分が掘って開いたままの配管の穴1つにつき。"),
                ("", "同点の場合:無効手が少ない方、次に先にその得点に達した方が上位。"),
            ]),
        ],
    },
}


def rulebook(lang: str, cfg: GameConfig) -> tuple[list[Section], list[Section]]:
    """(left column, right column) sections with the current rule values filled in."""
    values = {
        "rounds": cfg.max_rounds,
        "w": cfg.width,
        "h": cfg.height,
        "pd": cfg.points_pipe_detected,
        "lf": cfg.points_leak_found,
        "lx": cfg.points_leak_fixed,
        "pen": cfg.penalty_open_pipe_hole,
    }

    def fill(sections: list[Section]) -> list[Section]:
        return [
            (title, [(label.format(**values), text.format(**values)) for label, text in items])
            for title, items in sections
        ]

    book = _RULES[lang]
    return fill(book["left"]), fill(book["right"])


def all_japanese_text() -> str:
    """Every Japanese string the viewer can draw (used to subset the font)."""
    parts = [ja for _, ja in _TEXT.values()]
    for column in _RULES["ja"].values():
        for title, items in column:
            parts.append(title)
            parts.extend(label + text for label, text in items)
    return "".join(parts)
