"""English / Japanese text for the viewer and the rulebook.

Every key holds both languages, so a missing translation is impossible by construction.
Japanese is drawn with the bundled M PLUS Rounded 1c subset font: after adding Japanese text,
re-run ``python tools/subset_font.py`` (``tests/test_i18n.py`` fails on missing glyphs).
Terms: a *move* (手) is one action by one bot; a *round* (ラウンド) is one whole game.
"""

from __future__ import annotations

from ..config import GameConfig

LANGS = ("en", "ja")

# key: (English, Japanese)
_TEXT: dict[str, tuple[str, str]] = {
    # Header / status
    "subtitle": ("ARENA  ·  find and fix the leaks", "アリーナ ・ 漏水を見つけて直そう"),
    "move": ("MOVE {m}/{max}", "{m}/{max} 手"),
    "mode_single": ("SINGLE PLAYER", "シングルプレイ"),
    "mode_duel": ("1 VS 1", "1対1"),
    "mode_royale": ("BATTLE ROYALE {n}", "バトルロイヤル {n}"),
    "mode_manual": ("MANUAL PLAY", "手動プレイ"),
    "leaks": ("LEAKS found {f}  fixed {x} / {t}", "漏水  発見 {f}  修理 {x} / {t}"),
    "seed": ("seed {s}", "シード {s}"),
    # Buttons
    "play": ("PLAY", "スタート"),
    "pause": ("PAUSE", "一時停止"),
    "step": ("STEP", "1手進む"),
    "restart": ("RESTART", "リスタート"),
    "new_map": ("NEW MAP", "新しいマップ"),
    "simulate": ("RUN {n} ROUNDS", "{n}ラウンド実行"),
    "cancel_sim": ("CANCEL SIM", "シミュレート中止"),
    "pipes_ghost": ("PIPES: GHOST", "配管: 半透明"),
    "pipes_hidden": ("PIPES: HIDDEN", "配管: 非表示"),
    "marks_on": ("MARKS: ON", "マーク: オン"),
    "marks_off": ("MARKS: OFF", "マーク: オフ"),
    "manual_on": ("MANUAL: ON", "手動: オン"),
    "manual_off": ("MANUAL: OFF", "手動: オフ"),
    "rules": ("RULES", "ルール"),
    "rulebook_title": ("RULEBOOK", "ルールブック"),
    "close": ("CLOSE", "閉じる"),
    # Setup overlay
    "setup": ("SETUP", "セットアップ"),
    "setup_title": ("GAME SETUP", "ゲーム設定"),
    "tab_single": ("SINGLE PLAYER", "シングルプレイ"),
    "tab_duel": ("1 VS 1", "1対1"),
    "tab_royale": ("BATTLE ROYALE", "バトルロイヤル"),
    "tab_manual": ("MANUAL", "手動"),
    "seat": ("BOT {n}", "ボット {n}"),
    "setup_seats": ("{n} seats  ·  {total} bots found in bots/", "{n}席  ・  bots/ のボット {total}体"),
    "add_seat": ("+ ADD BOT", "+ ボット追加"),
    "remove_seat": ("- REMOVE BOT", "- ボット削除"),
    "start_round": ("START ROUND", "ラウンド開始"),
    "setup_manual": ("You play one bot yourself with the keyboard.\n"
                     "W A S D move  ·  Q detect  ·  hold E / R / F + W A S D: dig up / fix / refill\n"
                     "1 buy UT  ·  2 buy satellite",
                     "キーボードで自分のボットを1体操作します。\n"
                     "W A S D 移動  ・  Q 探知  ・  E / R / F を押したまま W A S D: 掘削 / 修理 / 埋め戻し\n"
                     "1 UT購入  ・  2 衛星購入"),
    "setup_hint": ("Click < > to pick a bot  ·  Enter starts  ·  Esc closes",
                   "< > でボットを選択  ・  Enterで開始  ・  Escで閉じる"),
    "toast_bots_failed": ("Could not load {files}", "{files} を読み込めませんでした"),
    # Speed + scoreboard
    "speed": ("SPEED", "速度"),
    "rate": ("{n} moves/s", "{n} 手/秒"),
    "max": ("MAX", "最大"),
    "speed_manual": ("1 key = 1 move", "1キー = 1手"),
    "scoreboard": ("SCOREBOARD", "スコアボード"),
    "card_counts": ("pipes {p}  found {f}  fixed {x}", "配管 {p}  発見 {f}  修理 {x}"),
    "card_holes_live": ("holes {h} (-{pen} at end)  invalid {i}", "穴 {h} (終了時 -{pen})  無効 {i}"),
    "card_holes_final": ("holes {h} (-{pen})  invalid {i}", "穴 {h} (-{pen})  無効 {i}"),
    "error": ("error: {e}", "エラー: {e}"),
    "badge_ut": ("UT", "UT"),
    "badge_sat": ("SAT", "衛星"),
    "badge_punk": ("PUNK", "パンク"),
    "badge_chained": ("CHAINED {n}", "鉄球 {n}"),
    "toast_punk": ("A PUNK upgrade appeared on the map!", "パンクアイテムが出現!"),
    "risk_legend": ("SATELLITE RISK  A low ... E high", "衛星リスク  A 低 … E 高"),
    # Simulation panel
    "sim_title": ("SIMULATION", "シミュレーション"),
    "sim_idle": ("Plays {n} headless rounds with\nthe current bots on fresh maps.",
                 "今のボットで新しいマップを\n{n}ラウンド、画面なしで対戦します。"),
    "sim_progress": ("{d}/{t} rounds  (seeds {s}+)", "{d}/{t} ラウンド  (シード {s}〜)"),
    "sim_head": ("{g} rounds  {sec}s  seed {s}", "{g}ラウンド  {sec}秒  シード {s}"),
    "sim_cancelled": ("(cancelled)", "(中止)"),
    "sim_avg": ("avg {a}", "平均 {a}"),
    "sim_wins": ("wins {w}%", "勝率 {w}%"),
    "sim_minmax": ("min {lo}  max {hi}  invalid {i}", "最小 {lo}  最大 {hi}  無効 {i}"),
    "sim_counts": ("pipes {p}  found {f}  fixed {x}  holes {h}", "配管 {p}  発見 {f}  修理 {x}  穴 {h}"),
    # Manual mode: key help box (key caps are the same on English and Japanese keyboards)
    "you": ("You", "あなた"),
    "help_title": ("MANUAL CONTROLS", "手動操作"),
    "keys_move": ("W A S D", "W A S D"),
    "help_move": ("move up / left / down / right", "上・左・下・右へ移動"),
    "keys_detect": ("Q", "Q"),
    "help_detect": ("detect all four sides", "上下左右の4マスを探知"),
    "keys_unearth": ("E + WASD", "E + WASD"),
    "help_unearth": ("unearth that side", "その方向を掘削"),
    "keys_fix": ("R + WASD", "R + WASD"),
    "help_fix": ("fix that side", "その方向を修理"),
    "keys_refill": ("F + WASD", "F + WASD"),
    "help_refill": ("refill that side", "その方向を埋め戻し"),
    "keys_buy_ut": ("1", "1"),
    "help_buy_ut": ("buy UT ({c} points)", "UTを購入 ({c}点)"),
    "keys_buy_sat": ("2", "2"),
    "help_buy_sat": ("buy satellite ({c} points)", "衛星を購入 ({c}点)"),
    "help_note": ("Hold E, R or F, then tap a direction.", "E・R・Fを押したまま方向キーを押す。"),
    # Hints + toasts
    "hint_drop": ("Drag a PNG onto a bot card to set its sprite", "PNGをボットのカードにドロップで画像変更"),
    "hint_web_sprite": ("Sprites: commit bots/sprites/<ClassName>.png", "画像: bots/sprites/<クラス名>.png をコミット"),
    "hint_keys1": ("SPACE play  S step  R restart  N new  H rules", "スペース:再生  S:1手  R:リスタート  N:新マップ  H:ルール"),
    "hint_keys2": ("P pipes  D marks  M sim  L lang  UP/DN speed", "P:配管  D:マーク  M:シミュ  L:言語  ↑↓:速度"),
    "hint_manual1": ("N new map  P pipes  H rules  L lang", "N:新マップ  P:配管  H:ルール  L:言語"),
    "hint_manual2": ("Click MANUAL again to hand over to the bots", "もう一度「手動」でボットの対戦に戻る"),
    "toast_invalid": ("Invalid: {e}", "無効: {e}"),
    "toast_png_only": ("Only .png files can be used as sprites", "画像は .png ファイルのみ使えます"),
    "toast_bad_image": ("Could not read that image", "画像を読み込めませんでした"),
    "toast_sprite_saved": ("Sprite saved for {name}", "{name} の画像を保存しました"),
    # End of round
    "game_over": ("ROUND OVER", "ラウンド終了"),
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
                ("", "Find buried water leaks, fix them and leave no open pipe holes. A round is one "
                     "game: the highest score after {moves} moves wins."),
            ]),
            ("MOVES", [
                ("", "Bots take turns in a fixed order. Each move is exactly one action. Every bot makes "
                     "{moves} moves per round; in single player your bot makes every move."),
                ("", "The square a bot stands on is detected automatically and for free: at the start "
                     "and after every step."),
            ]),
            ("ACTIONS", [
                ("", "Actions aim at a neighbouring square UP, DOWN, LEFT or RIGHT, never at the "
                     "bot's own square. DETECT and BUY need no direction."),
                ("MOVE", "Step onto an untouched, empty square. Holes and other bots block the way."),
                ("DETECT", "Scan the four neighbouring squares (those inside the grid) at once and learn "
                           "privately which hold a pipe."),
                ("UNEARTH", "Dig the square open. It must be untouched and empty."),
                ("FIX", "Repair a broken pipe in an unearthed square."),
                ("REFILL", "Fill any hole back in, whoever dug it. A leaking pipe must be fixed first."),
                ("BUY", "Buy a power-up with points. Uses the move and needs enough points; "
                        "each power-up once per round."),
                ("CHAIN", "Punk only, once per round: ball and chain on an adjacent opponent."),
            ]),
            ("LIMITS", [
                ("", "Bots can never leave the {w} x {h} grid."),
                ("", "One bot or object per square. Bots cannot stand on unearthed squares."),
                ("", "An invalid action (blocked, off the grid, wrong target, not enough points, bot "
                     "crash) wastes the move and counts as an invalid move."),
            ]),
        ],
        "right": [
            ("POWER-UPS", [
                ("UT", "{ut} points. Every detect and auto-detect from now on also shows whether the scanned squares "
                       "leak (scan a square again to update an old result). No points for leaks seen "
                       "this way: unearth and fix them to score."),
                ("SAT", "{sat} points. The Tenchijin satellite shows you the whole pipe network with "
                              "a risk grade A to E per pipe square (E = most likely leaking). Squares it "
                              "shows give no detect points."),
                ("", "Grades are a hint, not proof: most leaks are graded E or D, but an intact pipe "
                     "can be graded D, and a leak can be graded B."),
                ("PUNK", "Free pickup at move {punk} on a random square: step on it for +{punk_pts} and a "
                         "mohawk, plus one ball and chain (chained bots need 2 moves per step for {chain} moves)."),
            ]),
            ("WHAT BOTS SEE", [
                ("", "Everywhere on the map: which squares are untouched, dug up or filled back in. A "
                     "dug-up square shows its pipe, which way it runs and whether it is OK, leaking "
                     "or fixed."),
                ("", "Buried pipes stay hidden until detected, unearthed or shown by the satellite. "
                     "Detect results are private."),
                ("", "Once a square has been dug up, its pipe stays known to everyone, even after a refill."),
                ("", "Bots are not told who dug a hole: keep track of your own. Power-up purchases are public."),
            ]),
            ("SCORING", [
                ("+{pd}", "Discover a pipe square for the first time (detect or auto-detect). Once per "
                          "square per bot, and never for a square that was ever dug up."),
                ("+{lf}", "Find a leak: unearth a broken pipe (once per leak)."),
                ("+{lx}", "Fix a leak."),
                ("-{pen}", "At the end of the round, for every pipe hole you dug that is still open."),
                ("", "Power-ups cost their price in points. Ties: fewer invalid moves wins, then "
                     "whoever reached their score first."),
            ]),
        ],
    },
    "ja": {
        "left": [
            ("目的", [
                ("", "地中の配管の漏水を見つけて修理し、穴を開けたままにしないこと。1ラウンド = 1ゲーム。"
                     "{moves}手の後に最も得点の高いボットの勝ち。"),
            ]),
            ("手番", [
                ("", "ボットは決まった順番で交代に行動する。1手 = 1つの行動。各ボットは1ラウンドで"
                     "{moves}手ずつ行動する。シングルプレイでは毎手自分のボットが行動する。"),
                ("", "ボットが立っているマスは自動で無料探知される(開始時と、移動するたび)。"),
            ]),
            ("行動", [
                ("", "行動は上下左右いずれかの隣のマスが対象で、自分のマスには使えない。"
                     "探知と購入は方向の指定が不要。"),
                ("移動", "未掘削で空いているマスへ進む。穴と他のボットは通れない。"),
                ("探知", "上下左右の4マス(盤面内のみ)を同時にスキャンし、配管があるかを自分だけが知る。"),
                ("掘削", "マスを掘り起こす。未掘削で空いているマスのみ。"),
                ("修理", "掘り起こしたマスの壊れた配管を直す。"),
                ("埋め戻し", "誰が掘った穴でも埋め戻せる。漏水中の配管は先に修理が必要。"),
                ("購入", "得点でパワーアップを買う。1手を使い、得点が足りる場合のみ。"
                         "各パワーアップは1ラウンドに1回。"),
                ("鉄球", "パンクのみ、1ラウンドに1回:隣の相手に鉄球付きの鎖をつける。"),
            ]),
            ("制限", [
                ("", "ボットは {w} × {h} の盤面の外には出られない。"),
                ("", "1マスにはボットか物体が1つだけ。掘り起こしたマスには立てない。"),
                ("", "無効な行動(通れない、盤面外、対象が違う、得点不足、ボットのエラー)は"
                     "1手を消費し、無効手として数えられる。"),
            ]),
        ],
        "right": [
            ("パワーアップ", [
                ("UT", "{ut}点。以後の探知と自動探知で、スキャンしたマスの漏水の有無も分かる(古い結果は"
                       "もう一度探知すると更新)。この方法で見つけた漏水に得点はなし:"
                       "掘り起こして修理すると得点。"),
                ("衛星", "{sat}点。天地人衛星で配管網全体と、配管マスごとのリスク等級A〜E"
                         "(Eが最も漏水の可能性が高い)が見える。衛星で見えたマスは探知の得点なし。"),
                ("", "等級はヒントであり確実ではない:漏水の多くはEかDだが、正常な配管がDのことも、"
                     "漏水がBのこともある。"),
                ("パンク", "{punk}手目にランダムなマスに現れる無料アイテム。踏むと+{punk_pts}点と赤いモヒカン。"
                           "対戦では鉄球付きの鎖も1回使える:つけられたボットは次の{chain}手の間、"
                           "1歩に2手かかる。"),
            ]),
            ("ボットに見えるもの", [
                ("", "盤面のどこでも、未掘削・掘削済み・埋め戻し済みのマスが分かる。掘削済みのマスでは"
                     "配管、その向き、正常・漏水・修理済みの状態が見える。"),
                ("", "埋まった配管は探知・掘削・衛星まで見えない。探知の結果は自分だけのもの。"),
                ("", "一度掘り起こされたマスの配管は、埋め戻した後も全員に知られたまま。"),
                ("", "誰が穴を掘ったかは教えてもらえない。自分の穴は自分で記録しよう。"
                     "パワーアップの購入は全員に公開される。"),
            ]),
            ("得点", [
                ("+{pd}", "配管マスを初めて発見(探知または自動探知)。各ボット1マス1回だけで、"
                          "一度でも掘り起こされたマスは対象外。"),
                ("+{lf}", "漏水を発見:壊れた配管を掘り起こす(漏水ごとに1回)。"),
                ("+{lx}", "漏水を修理する。"),
                ("-{pen}", "ラウンド終了時、自分が掘って開いたままの配管の穴1つにつき。"),
                ("", "パワーアップは価格分の得点を消費する。同点の場合:無効手が少ない方、"
                     "次に先にその得点に達した方が上位。"),
            ]),
        ],
    },
}


def rulebook(lang: str, cfg: GameConfig) -> tuple[list[Section], list[Section]]:
    """(left column, right column) sections with the current rule values filled in."""
    values = {
        "moves": cfg.max_moves,
        "w": cfg.width,
        "h": cfg.height,
        "pd": cfg.points_pipe_detected,
        "lf": cfg.points_leak_found,
        "lx": cfg.points_leak_fixed,
        "pen": cfg.penalty_open_pipe_hole,
        "ut": cfg.cost_ut,
        "sat": cfg.cost_satellite,
        "punk": cfg.punk_spawn_move,
        "punk_pts": cfg.points_punk,
        "chain": cfg.chain_duration,
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
