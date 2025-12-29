# -*- coding: utf-8 -*-
"""
Telegram MCQ Bot (single correct) with:
- Main blocks 1..5 (Block 2 has sub-blocks 2.1..2.4)
- Random order of questions per attempt
- Random order of answers per question
- No truncated answers: options are shown in message; buttons are A/B/C...
- Shows the correct option (letter + full text) after each answer / timeout
- Timed mode: 60 seconds PER QUESTION (auto-fail and move on)

Setup:
1) Create bot via @BotFather, copy token
2) Install deps: pip3 install -r requirements.txt
3) Export token:
   export BOT_TOKEN="xxx"
4) Run: python3 bot.py
"""

import os
import json
import random
import asyncio
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
PER_QUESTION_SECONDS = 60
FINAL_N = 20

# ----- Data model -----

@dataclass
class Question:
    q: str
    options: List[str]
    correct_index: int
    explanation: str = ""

@dataclass
class BlockFile:
    file: str
    title: str
    questions: List[Question]

# ----- Block structure -----
# Main blocks (groups). Block 2 is a group with subblocks.
MAIN_BLOCKS = [
    {"key": "b1", "title": "1 блок — Аудит", "files": ["block1_audit.json"]},
    {"key": "b2", "title": "2 блок — Законодавство", "files": [
        "block2_1_constitution.json",
        "block2_2_civil_service.json",
        "block2_3_mku.json",
        "block2_4_corruption.json",
    ]},
    {"key": "b3", "title": "3 блок — Митна вартість", "files": ["block3_value.json"]},
    {"key": "b4", "title": "4 блок — Походження", "files": ["block4_origin.json"]},
    {"key": "b5", "title": "5 блок — Платежі", "files": ["block5_payments.json"]},
]

# Human subblock titles (shown inside Block 2 menu)
SUBBLOCK_LABELS = {
    "block2_1_constitution.json": "2.1 Конституція",
    "block2_2_civil_service.json": "2.2 Держслужба",
    "block2_3_mku.json": "2.3 МКУ",
    "block2_4_corruption.json": "2.4 Корупція",
}

# ----- Utilities -----

def load_json_block(path: str) -> BlockFile:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    title = raw.get("title") or os.path.splitext(os.path.basename(path))[0]
    questions: List[Question] = []
    for item in raw.get("questions", []):
        q = (item.get("q") or "").strip()
        opts = [str(x).strip() for x in (item.get("options") or [])]
        ci = int(item.get("correct_index", 0))
        exp = (item.get("explanation") or "").strip()
        if not q or len(opts) < 2:
            continue
        if ci < 0 or ci >= len(opts):
            ci = 0
        questions.append(Question(q=q, options=opts, correct_index=ci, explanation=exp))
    return BlockFile(file=os.path.basename(path), title=title, questions=questions)

def load_all_blocks() -> Dict[str, BlockFile]:
    blocks: Dict[str, BlockFile] = {}
    if not os.path.isdir(DATA_DIR):
        raise RuntimeError(f"Missing data dir: {DATA_DIR}")
    for fn in os.listdir(DATA_DIR):
        if not fn.lower().endswith(".json"):
            continue
        path = os.path.join(DATA_DIR, fn)
        blocks[fn] = load_json_block(path)
    return blocks

def session_cancel_timer(context: ContextTypes.DEFAULT_TYPE) -> None:
    job = context.user_data.get("timer_job")
    if job:
        try:
            job.schedule_removal()
        except Exception:
            pass
    context.user_data["timer_job"] = None

def fmt_options_with_letters(opts: List[str]) -> str:
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    lines = []
    for i, opt in enumerate(opts):
        lines.append(f"{letters[i]}. {opt}")
    return "\n".join(lines)

def build_answer_keyboard(n: int, qid: int) -> InlineKeyboardMarkup:
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    row = []
    for i in range(n):
        row.append(InlineKeyboardButton(letters[i], callback_data=f"ans|{qid}|{i}"))
    # break into rows of 6
    rows = [row[i:i+6] for i in range(0, len(row), 6)]
    rows.append([InlineKeyboardButton("⛔ Завершити тест", callback_data="quit")])
    return InlineKeyboardMarkup(rows)

def pick_random(questions: List[Question], n: int) -> List[Question]:
    if n >= len(questions):
        return random.sample(questions, len(questions))
    return random.sample(questions, n)

def merge_questions(files: List[str], blocks: Dict[str, BlockFile]) -> List[Question]:
    out: List[Question] = []
    for fn in files:
        bf = blocks.get(fn)
        if bf:
            out.extend(bf.questions)
    return out

# ----- UI screens -----

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session_cancel_timer(context)
    context.user_data.pop("session", None)

    kb = []
    for b in MAIN_BLOCKS:
        kb.append([InlineKeyboardButton(b["title"], callback_data=f"menu|{b['key']}")])
    kb.append([InlineKeyboardButton("🎓 Загальний фінальний тест (20 з кожного блоку)", callback_data="global_final")])
    await update.effective_chat.send_message(
        "Обери блок:",
        reply_markup=InlineKeyboardMarkup(kb),
    )

async def show_block_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, block_key: str) -> None:
    session_cancel_timer(context)
    context.user_data.pop("session", None)

    b = next((x for x in MAIN_BLOCKS if x["key"] == block_key), None)
    if not b:
        await show_main_menu(update, context)
        return

    # Block 2 has submenu
    if block_key == "b2":
        kb = []
        kb.append([InlineKeyboardButton("▶ Повний тест Блоку 2 (усі питання)", callback_data="start|b2|full")])
        kb.append([InlineKeyboardButton(f"🎯 Фінальний тест Блоку 2 ({FINAL_N} випадкових)", callback_data="start|b2|final")])
        kb.append([InlineKeyboardButton("— Підблоки —", callback_data="noop")])
        for fn in b["files"]:
            label = SUBBLOCK_LABELS.get(fn, blocks_cache.get(fn).title if blocks_cache.get(fn) else fn)
            kb.append([InlineKeyboardButton(label, callback_data=f"submenu|{fn}")])
        kb.append([InlineKeyboardButton("⬅ Назад", callback_data="back")])
        await update.callback_query.message.reply_text(
            "Блок 2 — Законодавство. Обери режим або підблок:",
            reply_markup=InlineKeyboardMarkup(kb),
        )
        return

    kb = [
        [InlineKeyboardButton("▶ Повний тест (усі питання)", callback_data=f"start|{block_key}|full")],
        [InlineKeyboardButton(f"🎯 Фінальний тест ({FINAL_N} випадкових)", callback_data=f"start|{block_key}|final")],
        [InlineKeyboardButton("⬅ Назад", callback_data="back")],
    ]
    await update.callback_query.message.reply_text(
        f"{b['title']}. Обери режим:",
        reply_markup=InlineKeyboardMarkup(kb),
    )

async def show_subblock_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, subfile: str) -> None:
    session_cancel_timer(context)
    context.user_data.pop("session", None)
    bf = blocks_cache.get(subfile)
    if not bf:
        await update.callback_query.message.reply_text("Не знайшов файл підблоку.")
        return
    label = SUBBLOCK_LABELS.get(subfile, bf.title)

    kb = [
        [InlineKeyboardButton("▶ Повний тест підблоку", callback_data=f"startfile|{subfile}|full")],
        [InlineKeyboardButton(f"🎯 Фінальний тест підблоку ({FINAL_N})", callback_data=f"startfile|{subfile}|final")],
        [InlineKeyboardButton("⬅ Назад до Блоку 2", callback_data="menu|b2")],
    ]
    await update.callback_query.message.reply_text(
        f"{label}. Обери режим:",
        reply_markup=InlineKeyboardMarkup(kb),
    )

# ----- Test engine -----

def build_test_questions(mode: str, block_key: Optional[str], subfile: Optional[str]) -> Tuple[str, List[Question]]:
    """Returns (title, questions)."""
    if subfile:
        bf = blocks_cache[subfile]
        base = bf.questions
        if mode == "final":
            return (f"{SUBBLOCK_LABELS.get(subfile, bf.title)} — фінальний ({FINAL_N})", pick_random(base, FINAL_N))
        return (f"{SUBBLOCK_LABELS.get(subfile, bf.title)} — повний", random.sample(base, len(base)))

    b = next((x for x in MAIN_BLOCKS if x["key"] == block_key), None)
    if not b:
        return ("", [])
    pool = merge_questions(b["files"], blocks_cache)
    if mode == "final":
        return (f"{b['title']} — фінальний ({FINAL_N})", pick_random(pool, FINAL_N))
    return (f"{b['title']} — повний", random.sample(pool, len(pool)))

def start_session(context: ContextTypes.DEFAULT_TYPE, title: str, questions: List[Question]) -> None:
    context.user_data["session"] = {
        "title": title,
        "questions": questions,
        "i": 0,
        "correct": 0,
        "qid": 0,  # increments per question shown
    }

async def send_question(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = context.user_data.get("session")
    if not session:
        return
    questions: List[Question] = session["questions"]
    i: int = session["i"]
    if i >= len(questions):
        await finish_session(update, context)
        return

    # cancel prior timer and set new
    session_cancel_timer(context)

    q = questions[i]

    # Shuffle answers and map back (robust: track correct by text)
    correct_text = q.options[q.correct_index]
    shuffled_opts = list(q.options)
    random.shuffle(shuffled_opts)
    correct_new_index = shuffled_opts.index(correct_text)


    # store per question mapping
    session["current"] = {
        "shuffled_opts": shuffled_opts,
        "correct_index": correct_new_index,
    }
    session["qid"] += 1
    qid = session["qid"]

    header = f"🧩 <b>{session['title']}</b>\nПитання {i+1}/{len(questions)}  ⏱ {PER_QUESTION_SECONDS}с"
    body = f"\n\n<b>{q.q}</b>\n\n{fmt_options_with_letters(shuffled_opts)}"
    msg = header + body

    # schedule timeout
    job = context.job_queue.run_once(
        timeout_question,
        when=PER_QUESTION_SECONDS,
        data={"chat_id": update.effective_chat.id, "user_id": update.effective_user.id, "qid": qid},
    )
    context.user_data["timer_job"] = job

    await update.effective_chat.send_message(
        msg,
        reply_markup=build_answer_keyboard(len(shuffled_opts), qid),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )

async def timeout_question(context: ContextTypes.DEFAULT_TYPE) -> None:
    data = context.job.data
    chat_id = data["chat_id"]
    user_id = data["user_id"]
    qid = data["qid"]

    # user_data is scoped per user, but job doesn't have it; use application.user_data
    udata = context.application.user_data.get(user_id)
    if not udata:
        return
    session = udata.get("session")
    if not session:
        return
    # only if still on same question
    if session.get("qid") != qid:
        return

    cur = session.get("current") or {}
    opts = cur.get("shuffled_opts") or []
    ci = int(cur.get("correct_index", 0))
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    correct_text = opts[ci] if 0 <= ci < len(opts) else ""
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"⏰ Час вичерпано.\n✅ Правильна відповідь: {letters[ci]}. {correct_text}",
        disable_web_page_preview=True,
    )
    # move on as wrong
    session["i"] += 1
    # schedule next question send
    await context.bot.send_message(chat_id=chat_id, text="➡️ Наступне питання…")
    # create a fake Update is hard; call helper that sends directly
    await send_question_direct(context, chat_id, user_id)

async def send_question_direct(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> None:
    # similar to send_question but without Update
    udata = context.application.user_data.get(user_id)
    if not udata:
        return
    session = udata.get("session")
    if not session:
        return
    questions: List[Question] = session["questions"]
    i: int = session["i"]
    if i >= len(questions):
        # finish
        letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        total = len(questions)
        correct = session["correct"]
        pct = round(100.0 * correct / total, 1) if total else 0.0
        await context.bot.send_message(chat_id=chat_id, text=f"🏁 Тест завершено!\n✅ {correct}/{total} ({pct}%)")
        udata.pop("session", None)
        return

    # cancel old job if any
    job = udata.get("timer_job")
    if job:
        try:
            job.schedule_removal()
        except Exception:
            pass
    udata["timer_job"] = None

    q = questions[i]
    order = list(range(len(q.options)))
    random.shuffle(order)
    shuffled_opts = [q.options[j] for j in order]
    correct_new_index = order.index(q.correct_index)

    session["current"] = {"shuffled_opts": shuffled_opts, "correct_index": correct_new_index}
    session["qid"] += 1
    qid = session["qid"]

    header = f"🧩 <b>{session['title']}</b>\nПитання {i+1}/{len(questions)}  ⏱ {PER_QUESTION_SECONDS}с"
    body = f"\n\n<b>{q.q}</b>\n\n{fmt_options_with_letters(shuffled_opts)}"
    msg = header + body

    job2 = context.job_queue.run_once(
        timeout_question,
        when=PER_QUESTION_SECONDS,
        data={"chat_id": chat_id, "user_id": user_id, "qid": qid},
    )
    udata["timer_job"] = job2

    await context.bot.send_message(
        chat_id=chat_id,
        text=msg,
        reply_markup=build_answer_keyboard(len(shuffled_opts), qid),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )

async def finish_session(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session_cancel_timer(context)
    session = context.user_data.get("session")
    if not session:
        return
    total = len(session["questions"])
    correct = session["correct"]
    pct = round(100.0 * correct / total, 1) if total else 0.0
    await update.effective_chat.send_message(f"🏁 Тест завершено!\n✅ {correct}/{total} ({pct}%)")
    context.user_data.pop("session", None)

# ----- Handlers -----

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_main_menu(update, context)

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data or ""

    if data == "noop":
        return
    if data == "back":
        await show_main_menu(update, context)
        return
    if data.startswith("menu|"):
        block_key = data.split("|", 1)[1]
        await show_block_menu(update, context, block_key)
        return
    if data.startswith("submenu|"):
        subfile = data.split("|", 1)[1]
        await show_subblock_menu(update, context, subfile)
        return
    if data == "quit":
        await query.message.reply_text("⛔ Тест зупинено.")
        context.user_data.pop("session", None)
        session_cancel_timer(context)
        await show_main_menu(update, context)
        return

    if data == "global_final":
        # 20 from each main block (1..5), then shuffle
        pools = []
        title_parts = []
        for b in MAIN_BLOCKS:
            pool = merge_questions(b["files"], blocks_cache)
            pools.extend(pick_random(pool, FINAL_N))
            title_parts.append(b["title"])
        random.shuffle(pools)
        title = f"🎓 Загальний фінальний тест ({FINAL_N} з кожного блоку)"
        start_session(context, title, pools)
        await query.message.reply_text("Починаємо фінальний тест…")
        await send_question(update, context)
        return

    if data.startswith("start|"):
        _, block_key, mode = data.split("|", 2)
        title, questions = build_test_questions(mode, block_key, None)
        if not questions:
            await query.message.reply_text("Немає питань у цьому блоці.")
            return
        start_session(context, title, questions)
        await query.message.reply_text("Починаємо тест…")
        await send_question(update, context)
        return

    if data.startswith("startfile|"):
        _, subfile, mode = data.split("|", 2)
        if subfile not in blocks_cache:
            await query.message.reply_text("Не знайшов підблок.")
            return
        title, questions = build_test_questions(mode, None, subfile)
        start_session(context, title, questions)
        await query.message.reply_text("Починаємо тест…")
        await send_question(update, context)
        return

    if data.startswith("ans|"):
        # ans|qid|index
        session = context.user_data.get("session")
        if not session:
            await query.message.reply_text("Сесія не активна. /start")
            return

        _, qid_str, idx_str = data.split("|", 2)
        qid = int(qid_str)
        idx = int(idx_str)
        # ignore stale answers
        if session.get("qid") != qid:
            await query.message.reply_text("Це питання вже не активне.")
            return

        session_cancel_timer(context)

        cur = session.get("current") or {}
        opts = cur.get("shuffled_opts") or []
        ci = int(cur.get("correct_index", 0))
        letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        correct_text = opts[ci] if 0 <= ci < len(opts) else ""

        if idx == ci:
            session["correct"] += 1
            await query.message.reply_text(f"✅ Правильно! ({letters[ci]}. {correct_text})")
        else:
            chosen = opts[idx] if 0 <= idx < len(opts) else ""
            await query.message.reply_text(
                f"❌ Неправильно.\nТвоя відповідь: {letters[idx]}. {chosen}\n✅ Правильна: {letters[ci]}. {correct_text}"
            )

        session["i"] += 1
        await send_question(update, context)
        return

    await query.message.reply_text("Невідома команда. /start")

# ----- Main -----

blocks_cache = {}

def main() -> None:
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise RuntimeError("Set BOT_TOKEN env var first")

    global blocks_cache
    blocks_cache = load_all_blocks()

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(on_callback))

    print("Bot is running…")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
