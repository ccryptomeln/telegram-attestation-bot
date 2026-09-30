# -*- coding: utf-8 -*-

"""
Telegram MCQ Bot

Логіка:
- Основні блоки 1..5
- Блок 2 має підблоки 2.1..2.4
- Повний тест блоку = усі питання
- Фінальний тест блоку = 20 випадкових питань
- Повний тест підблоку = усі питання
- Фінальний тест підблоку = 20 випадкових питань
- Загальний фінальний тест = по 20 питань з кожного блоку 1..5
  (до 100 питань загалом)
- Випадковий порядок питань
- Випадковий порядок відповідей
- 60 секунд на кожне питання
- Після відповіді питання НЕ зникає
- Кнопки відповіді блокуються
- Результат надсилається окремим повідомленням
- Після відповіді автоматично надсилається наступне питання
- Підтримка Render Web Service
- python-telegram-bot 21.11.1
"""

import os
import json
import random
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)


# ============================================================
# SETTINGS
# ============================================================

DATA_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "data",
)

PER_QUESTION_SECONDS = 60
FINAL_N = 20

BOT_TOKEN = os.environ.get("BOT_TOKEN")

# Кеш завантажених JSON-файлів
blocks_cache: Dict[str, "BlockFile"] = {}


# ============================================================
# DATA MODELS
# ============================================================

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


# ============================================================
# BLOCK STRUCTURE
# ============================================================

MAIN_BLOCKS = [
    {
        "key": "b1",
        "title": "1 блок — Аудит",
        "files": [
            "block1_audit.json"
        ],
    },
    {
        "key": "b2",
        "title": "2 блок — Законодавство",
        "files": [
            "block2_1_constitution.json",
            "block2_2_civil_service.json",
            "block2_3_mku.json",
            "block2_4_corruption.json",
        ],
    },
    {
        "key": "b3",
        "title": "3 блок — Митна вартість",
        "files": [
            "block3_value.json"
        ],
    },
    {
        "key": "b4",
        "title": "4 блок — Походження",
        "files": [
            "block4_origin.json"
        ],
    },
    {
        "key": "b5",
        "title": "5 блок — Платежі",
        "files": [
            "block5_payments.json"
        ],
    },
]


SUBBLOCK_LABELS = {
    "block2_1_constitution.json":
        "2.1 Конституція",

    "block2_2_civil_service.json":
        "2.2 Держслужба",

    "block2_3_mku.json":
        "2.3 МКУ",

    "block2_4_corruption.json":
        "2.4 Корупція",
}


# ============================================================
# DATA LOADING
# ============================================================

def load_json_block(path: str) -> BlockFile:

    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    title = (
        raw.get("title")
        or os.path.splitext(
            os.path.basename(path)
        )[0]
    )

    questions: List[Question] = []

    for item in raw.get("questions", []):

        q = (
            item.get("q")
            or ""
        ).strip()

        opts = [
            str(x).strip()
            for x in (
                item.get("options")
                or []
            )
        ]

        try:
            ci = int(
                item.get(
                    "correct_index",
                    0,
                )
            )
        except (
            TypeError,
            ValueError,
        ):
            ci = 0

        exp = (
            item.get("explanation")
            or ""
        ).strip()

        if not q:
            continue

        if len(opts) < 2:
            continue

        if ci < 0 or ci >= len(opts):
            ci = 0

        questions.append(
            Question(
                q=q,
                options=opts,
                correct_index=ci,
                explanation=exp,
            )
        )

    return BlockFile(
        file=os.path.basename(path),
        title=title,
        questions=questions,
    )


def load_all_blocks() -> Dict[str, BlockFile]:

    blocks: Dict[str, BlockFile] = {}

    if not os.path.isdir(DATA_DIR):

        raise RuntimeError(
            f"Missing data dir: {DATA_DIR}"
        )

    for filename in os.listdir(DATA_DIR):

        if not filename.lower().endswith(".json"):
            continue

        path = os.path.join(
            DATA_DIR,
            filename,
        )

        try:

            block = load_json_block(path)

            blocks[filename] = block

            print(
                f"[INFO] Loaded "
                f"{len(block.questions)} "
                f"questions from "
                f"{filename}",
                flush=True,
            )

        except Exception as e:

            print(
                f"[ERROR] Could not load "
                f"{filename}: {e}",
                flush=True,
            )

    return blocks


# ============================================================
# TIMER
# ============================================================

def session_cancel_timer(
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    job = context.user_data.get(
        "timer_job"
    )

    if job:

        try:
            job.schedule_removal()
        except Exception:
            pass

    context.user_data["timer_job"] = None


# ============================================================
# HELPERS
# ============================================================

def fmt_options_with_letters(
    opts: List[str],
) -> str:

    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

    lines = []

    for i, opt in enumerate(opts):

        lines.append(
            f"{letters[i]}. {opt}"
        )

    return "\n".join(lines)


def pick_random(
    questions: List[Question],
    n: int,
) -> List[Question]:

    if not questions:
        return []

    if n >= len(questions):

        return random.sample(
            questions,
            len(questions),
        )

    return random.sample(
        questions,
        n,
    )


def merge_questions(
    files: List[str],
    blocks: Dict[str, BlockFile],
) -> List[Question]:

    result: List[Question] = []

    for filename in files:

        block = blocks.get(filename)

        if block:
            result.extend(
                block.questions
            )

    return result


# ============================================================
# ANSWER KEYBOARD
# ============================================================

def build_answer_keyboard(
    n: int,
    qid: int,
) -> InlineKeyboardMarkup:

    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

    row = []

    for i in range(n):

        row.append(
            InlineKeyboardButton(
                letters[i],
                callback_data=(
                    f"ans|{qid}|{i}"
                ),
            )
        )

    rows = [
        row[i:i + 6]
        for i in range(
            0,
            len(row),
            6,
        )
    ]

    rows.append([
        InlineKeyboardButton(
            "⛔ Завершити тест",
            callback_data="quit",
        )
    ])

    return InlineKeyboardMarkup(rows)


# ============================================================
# MAIN MENU
# ============================================================

async def show_main_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    session_cancel_timer(context)

    context.user_data.pop(
        "session",
        None,
    )

    keyboard = []

    for block in MAIN_BLOCKS:

        keyboard.append([
            InlineKeyboardButton(
                block["title"],
                callback_data=(
                    f"menu|{block['key']}"
                ),
            )
        ])

    keyboard.append([
        InlineKeyboardButton(
            "🎓 Загальний фінальний тест "
            "(20 з кожного блоку)",
            callback_data="global_final",
        )
    ])

    await update.effective_chat.send_message(
        "📚 <b>Оберіть блок:</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
    )


# ============================================================
# BLOCK MENU
# ============================================================

async def show_block_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    block_key: str,
) -> None:

    session_cancel_timer(context)

    context.user_data.pop(
        "session",
        None,
    )

    block = next(
        (
            x
            for x in MAIN_BLOCKS
            if x["key"] == block_key
        ),
        None,
    )

    if not block:

        await show_main_menu(
            update,
            context,
        )

        return

    # --------------------------------------------------------
    # BLOCK 2
    # --------------------------------------------------------

    if block_key == "b2":

        keyboard = []

        keyboard.append([
            InlineKeyboardButton(
                "▶ Повний тест Блоку 2 "
                "(усі питання)",
                callback_data=(
                    "start|b2|full"
                ),
            )
        ])

        keyboard.append([
            InlineKeyboardButton(
                f"🎯 Фінальний тест Блоку 2 "
                f"({FINAL_N} випадкових)",
                callback_data=(
                    "start|b2|final"
                ),
            )
        ])

        keyboard.append([
            InlineKeyboardButton(
                "— Підблоки —",
                callback_data="noop",
            )
        ])

        for filename in block["files"]:

            label = SUBBLOCK_LABELS.get(
                filename,
                blocks_cache.get(
                    filename
                ).title
                if blocks_cache.get(filename)
                else filename,
            )

            keyboard.append([
                InlineKeyboardButton(
                    label,
                    callback_data=(
                        f"submenu|{filename}"
                    ),
                )
            ])

        keyboard.append([
            InlineKeyboardButton(
                "⬅ Назад",
                callback_data="back",
            )
        ])

        await update.callback_query.message.reply_text(
            "📚 <b>Блок 2 — Законодавство</b>\n\n"
            "Оберіть режим або підблок:",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                keyboard
            ),
        )

        return

    # --------------------------------------------------------
    # OTHER BLOCKS
    # --------------------------------------------------------

    keyboard = [
        [
            InlineKeyboardButton(
                "▶ Повний тест "
                "(усі питання)",
                callback_data=(
                    f"start|{block_key}|full"
                ),
            )
        ],
        [
            InlineKeyboardButton(
                f"🎯 Фінальний тест "
                f"({FINAL_N} випадкових)",
                callback_data=(
                    f"start|{block_key}|final"
                ),
            )
        ],
        [
            InlineKeyboardButton(
                "⬅ Назад",
                callback_data="back",
            )
        ],
    ]

    await update.callback_query.message.reply_text(
        f"<b>{block['title']}</b>\n\n"
        "Оберіть режим:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
    )


# ============================================================
# SUBBLOCK MENU
# ============================================================

async def show_subblock_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    subfile: str,
) -> None:

    session_cancel_timer(context)

    context.user_data.pop(
        "session",
        None,
    )

    block = blocks_cache.get(
        subfile
    )

    if not block:

        await update.callback_query.message.reply_text(
            "❌ Не знайшов файл підблоку."
        )

        return

    label = SUBBLOCK_LABELS.get(
        subfile,
        block.title,
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "▶ Повний тест підблоку",
                callback_data=(
                    f"startfile|{subfile}|full"
                ),
            )
        ],
        [
            InlineKeyboardButton(
                f"🎯 Фінальний тест підблоку "
                f"({FINAL_N})",
                callback_data=(
                    f"startfile|{subfile}|final"
                ),
            )
        ],
        [
            InlineKeyboardButton(
                "⬅ Назад до Блоку 2",
                callback_data="menu|b2",
            )
        ],
    ]

    await update.callback_query.message.reply_text(
        f"<b>{label}</b>\n\n"
        "Оберіть режим:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
    )


# ============================================================
# BUILD TEST
# ============================================================

def build_test_questions(
    mode: str,
    block_key: Optional[str],
    subfile: Optional[str],
) -> Tuple[str, List[Question]]:

    # --------------------------------------------------------
    # SUBBLOCK
    # --------------------------------------------------------

    if subfile:

        block = blocks_cache.get(
            subfile
        )

        if not block:
            return "", []

        pool = block.questions

        if mode == "final":

            questions = pick_random(
                pool,
                FINAL_N,
            )

            title = (
                f"{SUBBLOCK_LABELS.get(subfile, block.title)} "
                f"— фінальний ({len(questions)})"
            )

            return title, questions

        questions = random.sample(
            pool,
            len(pool),
        )

        title = (
            f"{SUBBLOCK_LABELS.get(subfile, block.title)} "
            f"— повний"
        )

        return title, questions

    # --------------------------------------------------------
    # MAIN BLOCK
    # --------------------------------------------------------

    block = next(
        (
            x
            for x in MAIN_BLOCKS
            if x["key"] == block_key
        ),
        None,
    )

    if not block:
        return "", []

    pool = merge_questions(
        block["files"],
        blocks_cache,
    )

    if mode == "final":

        questions = pick_random(
            pool,
            FINAL_N,
        )

        title = (
            f"{block['title']} "
            f"— фінальний ({len(questions)})"
        )

        return title, questions

    questions = random.sample(
        pool,
        len(pool),
    )

    title = (
        f"{block['title']} — повний"
    )

    return title, questions


# ============================================================
# START SESSION
# ============================================================

def start_session(
    context: ContextTypes.DEFAULT_TYPE,
    title: str,
    questions: List[Question],
) -> None:

    context.user_data["session"] = {

        "title": title,

        "questions": questions,

        "i": 0,

        "correct": 0,

        "qid": 0,

        "current": None,

        "answered": False,

        "question_message_id": None,

    }


# ============================================================
# SEND QUESTION
# ============================================================

async def send_question(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    session = context.user_data.get(
        "session"
    )

    if not session:
        return

    questions: List[
        Question
    ] = session["questions"]

    i: int = session["i"]

    if i >= len(questions):

        await finish_session(
            update,
            context,
        )

        return

    # Cancel previous timer
    session_cancel_timer(
        context
    )

    question = questions[i]

    # --------------------------------------------------------
    # RANDOMIZE ANSWERS
    # --------------------------------------------------------

    order = list(
        range(
            len(question.options)
        )
    )

    random.shuffle(order)

    shuffled_options = [
        question.options[index]
        for index in order
    ]

    correct_new_index = order.index(
        question.correct_index
    )

    # --------------------------------------------------------
    # STORE CURRENT QUESTION
    # --------------------------------------------------------

    session["current"] = {

        "shuffled_opts":
            shuffled_options,

        "correct_index":
            correct_new_index,

    }

    session["answered"] = False

    session["qid"] += 1

    qid = session["qid"]

    # --------------------------------------------------------
    # MESSAGE
    # --------------------------------------------------------

    header = (
        f"🧩 <b>{session['title']}</b>\n"
        f"Питання {i + 1}/"
        f"{len(questions)}"
        f"  ⏱ "
        f"{PER_QUESTION_SECONDS}с"
    )

    body = (
        f"\n\n"
        f"<b>{question.q}</b>"
        f"\n\n"
        f"{fmt_options_with_letters(shuffled_options)}"
    )

    message_text = (
        header
        + body
    )

    # --------------------------------------------------------
    # TIMER
    # --------------------------------------------------------

    job = context.job_queue.run_once(

        timeout_question,

        when=PER_QUESTION_SECONDS,

        data={
            "chat_id":
                update.effective_chat.id,

            "user_id":
                update.effective_user.id,

            "qid":
                qid,
        },
    )

    context.user_data["timer_job"] = job

    # --------------------------------------------------------
    # SEND NEW MESSAGE
    # --------------------------------------------------------

    message = await update.effective_chat.send_message(

        message_text,

        reply_markup=build_answer_keyboard(
            len(shuffled_options),
            qid,
        ),

        parse_mode=ParseMode.HTML,

        disable_web_page_preview=True,
    )

    session[
        "question_message_id"
    ] = message.message_id


# ============================================================
# SEND QUESTION DIRECTLY
# Used by timeout handler
# ============================================================

async def send_question_direct(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    user_id: int,
) -> None:

    user_data = (
        context.application
        .user_data
        .get(user_id)
    )

    if not user_data:
        return

    session = user_data.get(
        "session"
    )

    if not session:
        return

    questions: List[
        Question
    ] = session["questions"]

    i: int = session["i"]

    if i >= len(questions):

        await finish_session_direct(
            context,
            chat_id,
            user_id,
        )

        return

    # Cancel old timer
    old_job = user_data.get(
        "timer_job"
    )

    if old_job:

        try:
            old_job.schedule_removal()
        except Exception:
            pass

    user_data["timer_job"] = None

    question = questions[i]

    # Randomize answers
    order = list(
        range(
            len(question.options)
        )
    )

    random.shuffle(order)

    shuffled_options = [
        question.options[index]
        for index in order
    ]

    correct_new_index = order.index(
        question.correct_index
    )

    session["current"] = {

        "shuffled_opts":
            shuffled_options,

        "correct_index":
            correct_new_index,
    }

    session["answered"] = False

    session["qid"] += 1

    qid = session["qid"]

    header = (
        f"🧩 <b>{session['title']}</b>\n"
        f"Питання {i + 1}/"
        f"{len(questions)}"
        f"  ⏱ "
        f"{PER_QUESTION_SECONDS}с"
    )

    body = (
        f"\n\n"
        f"<b>{question.q}</b>"
        f"\n\n"
        f"{fmt_options_with_letters(shuffled_options)}"
    )

    message_text = (
        header
        + body
    )

    # New timer
    job = context.job_queue.run_once(

        timeout_question,

        when=PER_QUESTION_SECONDS,

        data={
            "chat_id": chat_id,
            "user_id": user_id,
            "qid": qid,
        },
    )

    user_data["timer_job"] = job

    message = await context.bot.send_message(

        chat_id=chat_id,

        text=message_text,

        reply_markup=build_answer_keyboard(
            len(shuffled_options),
            qid,
        ),

        parse_mode=ParseMode.HTML,

        disable_web_page_preview=True,
    )

    session[
        "question_message_id"
    ] = message.message_id


# ============================================================
# TIMEOUT
# ============================================================

async def timeout_question(
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    data = context.job.data

    chat_id = data["chat_id"]

    user_id = data["user_id"]

    qid = data["qid"]

    user_data = (
        context.application
        .user_data
        .get(user_id)
    )

    if not user_data:
        return

    session = user_data.get(
        "session"
    )

    if not session:
        return

    # Ignore old timer
    if session.get("qid") != qid:
        return

    # Already answered
    if session.get("answered"):
        return

    session["answered"] = True

    # Cancel timer reference
    user_data["timer_job"] = None

    current = (
        session.get("current")
        or {}
    )

    options = (
        current.get(
            "shuffled_opts"
        )
        or []
    )

    correct_index = int(
        current.get(
            "correct_index",
            0,
        )
    )

    letters = (
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    )

    if (
        0 <= correct_index
        < len(options)
    ):

        correct_text = (
            options[correct_index]
        )

        correct_answer = (
            f"{letters[correct_index]}. "
            f"{correct_text}"
        )

    else:

        correct_answer = (
            "невідома"
        )

    # --------------------------------------------------------
    # REMOVE BUTTONS ONLY
    # QUESTION ITSELF REMAINS
    # --------------------------------------------------------

    question_message_id = session.get(
        "question_message_id"
    )

    if question_message_id:

        try:

            await context.bot.edit_message_reply_markup(

                chat_id=chat_id,

                message_id=question_message_id,

                reply_markup=None,
            )

        except Exception as e:

            print(
                "[WARNING] Could not "
                "remove timeout "
                f"keyboard: {e}",
                flush=True,
            )

    # --------------------------------------------------------
    # RESULT
    # --------------------------------------------------------

    result_text = (
        "⏰ <b>Час вичерпано!</b>\n\n"
        "❌ Правильна відповідь:\n"
        f"<b>{correct_answer}</b>"
    )

    # Explanation
    questions = session["questions"]

    current_index = session["i"]

    if (
        0 <= current_index
        < len(questions)
    ):

        explanation = (
            questions[
                current_index
            ].explanation
        )

        if explanation:

            result_text += (
                "\n\n"
                "💡 <b>Пояснення:</b>\n"
                f"{explanation}"
            )

    await context.bot.send_message(

        chat_id=chat_id,

        text=result_text,

        parse_mode=ParseMode.HTML,

        disable_web_page_preview=True,
    )

    # Move to next question
    session["i"] += 1

    await context.bot.send_message(

        chat_id=chat_id,

        text="➡️ <b>Наступне питання…</b>",

        parse_mode=ParseMode.HTML,
    )

    await send_question_direct(

        context,

        chat_id,

        user_id,
    )


# ============================================================
# FINISH DIRECT
# ============================================================

async def finish_session_direct(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    user_id: int,
) -> None:

    user_data = (
        context.application
        .user_data
        .get(user_id)
    )

    if not user_data:
        return

    session = user_data.get(
        "session"
    )

    if not session:
        return

    session_cancel_timer(
        context
    )

    questions = session[
        "questions"
    ]

    total = len(questions)

    correct = session[
        "correct"
    ]

    percentage = (
        round(
            100.0
            * correct
            / total,
            1,
        )
        if total
        else 0.0
    )

    await context.bot.send_message(

        chat_id=chat_id,

        text=(
            "🏁 <b>Тест завершено!</b>\n\n"
            f"📚 {session['title']}\n\n"
            f"✅ Правильних: "
            f"<b>{correct}/{total}</b>\n"
            f"📊 Результат: "
            f"<b>{percentage}%</b>"
        ),

        parse_mode=ParseMode.HTML,
    )

    user_data.pop(
        "session",
        None,
    )


# ============================================================
# FINISH SESSION
# ============================================================

async def finish_session(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    session_cancel_timer(
        context
    )

    session = context.user_data.get(
        "session"
    )

    if not session:
        return

    total = len(
        session["questions"]
    )

    correct = session[
        "correct"
    ]

    percentage = (
        round(
            100.0
            * correct
            / total,
            1,
        )
        if total
        else 0.0
    )

    await update.effective_chat.send_message(

        "🏁 <b>Тест завершено!</b>\n\n"
        f"📚 {session['title']}\n\n"
        f"✅ Правильних: "
        f"<b>{correct}/{total}</b>\n"
        f"📊 Результат: "
        f"<b>{percentage}%</b>",

        parse_mode=ParseMode.HTML,
    )

    context.user_data.pop(
        "session",
        None,
    )


# ============================================================
# START COMMAND
# ============================================================

async def cmd_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    await show_main_menu(
        update,
        context,
    )


# ============================================================
# CALLBACK HANDLER
# ============================================================

async def on_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    query = update.callback_query

    await query.answer()

    data = query.data or ""

    # --------------------------------------------------------
    # NOOP
    # --------------------------------------------------------

    if data == "noop":
        return

    # --------------------------------------------------------
    # BACK
    # --------------------------------------------------------

    if data == "back":

        await show_main_menu(
            update,
            context,
        )

        return

    # --------------------------------------------------------
    # MAIN BLOCK MENU
    # --------------------------------------------------------

    if data.startswith("menu|"):

        block_key = data.split(
            "|",
            1,
        )[1]

        await show_block_menu(
            update,
            context,
            block_key,
        )

        return

    # --------------------------------------------------------
    # SUBBLOCK MENU
    # --------------------------------------------------------

    if data.startswith("submenu|"):

        subfile = data.split(
            "|",
            1,
        )[1]

        await show_subblock_menu(
            update,
            context,
            subfile,
        )

        return

    # --------------------------------------------------------
    # QUIT
    # --------------------------------------------------------

    if data == "quit":

        session_cancel_timer(
            context
        )

        context.user_data.pop(
            "session",
            None,
        )

        await query.message.reply_text(
            "⛔ <b>Тест зупинено.</b>",
            parse_mode=ParseMode.HTML,
        )

        await show_main_menu(
            update,
            context,
        )

        return

    # --------------------------------------------------------
    # GLOBAL FINAL TEST
    #
    # 20 RANDOM QUESTIONS FROM EACH
    # MAIN BLOCK 1..5
    #
    # TOTAL = UP TO 100
    # --------------------------------------------------------

    if data == "global_final":

        session_cancel_timer(
            context
        )

        context.user_data.pop(
            "session",
            None,
        )

        pools = []

        for block in MAIN_BLOCKS:

            pool = merge_questions(
                block["files"],
                blocks_cache,
            )

            selected = pick_random(
                pool,
                FINAL_N,
            )

            pools.extend(
                selected
            )

        random.shuffle(
            pools
        )

        title = (
            "🎓 Загальний фінальний тест "
            f"({FINAL_N} з кожного блоку)"
        )

        if not pools:

            await query.message.reply_text(
                "❌ Немає питань для "
                "фінального тесту."
            )

            return

        start_session(
            context,
            title,
            pools,
        )

        await query.message.reply_text(
            "🎓 <b>Починаємо загальний "
            "фінальний тест!</b>\n\n"
            f"Буде до <b>{len(pools)}</b> "
            "питань.",
            parse_mode=ParseMode.HTML,
        )

        await send_question(
            update,
            context,
        )

        return

    # --------------------------------------------------------
    # START MAIN BLOCK TEST
    # --------------------------------------------------------

    if data.startswith("start|"):

        _, block_key, mode = (
            data.split(
                "|",
                2,
            )
        )

        title, questions = (
            build_test_questions(
                mode,
                block_key,
                None,
            )
        )

        if not questions:

            await query.message.reply_text(
                "❌ Немає питань у цьому блоці."
            )

            return

        start_session(
            context,
            title,
            questions,
        )

        await query.message.reply_text(
            "▶ <b>Починаємо тест…</b>",
            parse_mode=ParseMode.HTML,
        )

        await send_question(
            update,
            context,
        )

        return

    # --------------------------------------------------------
    # START SUBBLOCK TEST
    # --------------------------------------------------------

    if data.startswith("startfile|"):

        _, subfile, mode = (
            data.split(
                "|",
                2,
            )
        )

        if subfile not in blocks_cache:

            await query.message.reply_text(
                "❌ Не знайшов підблок."
            )

            return

        title, questions = (
            build_test_questions(
                mode,
                None,
                subfile,
            )
        )

        if not questions:

            await query.message.reply_text(
                "❌ У підблоці немає питань."
            )

            return

        start_session(
            context,
            title,
            questions,
        )

        await query.message.reply_text(
            "▶ <b>Починаємо тест…</b>",
            parse_mode=ParseMode.HTML,
        )

        await send_question(
            update,
            context,
        )

        return

    # --------------------------------------------------------
    # ANSWER
    # --------------------------------------------------------

    if data.startswith("ans|"):

        session = (
            context.user_data.get(
                "session"
            )
        )

        if not session:

            await query.message.reply_text(
                "❌ Сесія не активна.\n"
                "Натисни /start"
            )

            return

        try:

            _, qid_str, idx_str = (
                data.split(
                    "|",
                    2,
                )
            )

            qid = int(qid_str)

            selected_index = int(
                idx_str
            )

        except Exception:

            return

        # ----------------------------------------------------
        # OLD QUESTION
        # ----------------------------------------------------

        if session.get("qid") != qid:

            return

        # ----------------------------------------------------
        # ALREADY ANSWERED
        # ----------------------------------------------------

        if session.get("answered"):

            return

        session["answered"] = True

        # Stop timer
        session_cancel_timer(
            context
        )

        current = (
            session.get("current")
            or {}
        )

        options = (
            current.get(
                "shuffled_opts"
            )
            or []
        )

        correct_index = int(
            current.get(
                "correct_index",
                0,
            )
        )

        letters = (
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        )

        if not (
            0 <= selected_index
            < len(options)
        ):

            return

        # ----------------------------------------------------
        # REMOVE BUTTONS ONLY
        #
        # QUESTION MESSAGE REMAINS
        # ----------------------------------------------------

        question_message_id = (
            session.get(
                "question_message_id"
            )
        )

        if question_message_id:

            try:

                await query.edit_message_reply_markup(
                    reply_markup=None
                )

            except Exception as e:

                print(
                    "[WARNING] Could not "
                    "remove answer "
                    f"keyboard: {e}",
                    flush=True,
                )

        # ----------------------------------------------------
        # CORRECT ANSWER
        # ----------------------------------------------------

        correct_text = options[
            correct_index
        ]

        if (
            selected_index
            == correct_index
        ):

            session["correct"] += 1

            result_text = (
                "✅ <b>Правильно!</b>\n\n"
                f"Правильна відповідь:\n"
                f"<b>"
                f"{letters[correct_index]}"
                f". {correct_text}"
                f"</b>"
            )

        # ----------------------------------------------------
        # WRONG ANSWER
        # ----------------------------------------------------

        else:

            selected_text = options[
                selected_index
            ]

            result_text = (
                "❌ <b>Неправильно!</b>\n\n"
                "Твоя відповідь:\n"
                f"<b>"
                f"{letters[selected_index]}"
                f". {selected_text}"
                f"</b>\n\n"
                "✅ Правильна відповідь:\n"
                f"<b>"
                f"{letters[correct_index]}"
                f". {correct_text}"
                f"</b>"
            )

        # ----------------------------------------------------
        # EXPLANATION
        # ----------------------------------------------------

        questions = session[
            "questions"
        ]

        current_index = session[
            "i"
        ]

        if (
            0 <= current_index
            < len(questions)
        ):

            explanation = (
                questions[
                    current_index
                ].explanation
            )

            if explanation:

                result_text += (
                    "\n\n"
                    "💡 <b>Пояснення:</b>\n"
                    f"{explanation}"
                )

        # ----------------------------------------------------
        # RESULT AS SEPARATE MESSAGE
        # ----------------------------------------------------

        await query.message.reply_text(

            result_text,

            parse_mode=ParseMode.HTML,

            disable_web_page_preview=True,
        )

        # ----------------------------------------------------
        # NEXT QUESTION
        # ----------------------------------------------------

        session["i"] += 1

        await query.message.reply_text(
            "➡️ <b>Наступне питання…</b>",
            parse_mode=ParseMode.HTML,
        )

        await send_question(
            update,
            context,
        )

        return

    # --------------------------------------------------------
    # UNKNOWN
    # --------------------------------------------------------

    await query.message.reply_text(
        "❓ Невідома команда.\n"
        "Натисни /start"
    )


# ============================================================
# MAIN
# ============================================================

def main() -> None:

    global blocks_cache

    print(
        "=" * 60,
        flush=True,
    )

    print(
        "Starting Telegram MCQ Bot...",
        flush=True,
    )

    print(
        "=" * 60,
        flush=True,
    )

    # --------------------------------------------------------
    # ENV CHECK
    # --------------------------------------------------------

    print(
        "=== ENV CHECK START ===",
        flush=True,
    )

    print(
        "HAS BOT_TOKEN =",
        "BOT_TOKEN" in os.environ,
        flush=True,
    )

    print(
        "=== ENV CHECK END ===",
        flush=True,
    )

    if not BOT_TOKEN:

        raise RuntimeError(
            "Set BOT_TOKEN environment variable first"
        )

    # --------------------------------------------------------
    # DATA
    # --------------------------------------------------------

    print(
        f"[INFO] DATA_DIR: {DATA_DIR}",
        flush=True,
    )

    if not os.path.isdir(DATA_DIR):

        raise RuntimeError(
            f"Missing data directory: "
            f"{DATA_DIR}"
        )

    blocks_cache = (
        load_all_blocks()
    )

    print(
        f"[INFO] Loaded "
        f"{len(blocks_cache)} "
        f"JSON files.",
        flush=True,
    )

    # --------------------------------------------------------
    # APPLICATION
    # --------------------------------------------------------

    application = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            cmd_start,
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            on_callback
        )
    )

    print(
        "[OK] Telegram application created.",
        flush=True,
    )

    print(
        "[INFO] Starting polling...",
        flush=True,
    )

    application.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
