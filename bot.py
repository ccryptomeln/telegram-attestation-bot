# -*- coding: utf-8 -*-

"""
Telegram MCQ Bot

Features:
- Main blocks 1..5
- Block 2 has sub-blocks 2.1..2.4
- Random questions
- Random answer order
- Questions remain in chat after answering
- Answer result is sent as a separate message
- 60 seconds per question
- Correct answer shown after answer / timeout
- Final test: 20 questions
- Compatible with python-telegram-bot 21.11.1
- Compatible with Render Web Service
"""

import os
import json
import random
from threading import Thread
from http.server import BaseHTTPRequestHandler, HTTPServer
from dataclasses import dataclass
from typing import List, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

PER_QUESTION_SECONDS = 60
FINAL_N = 20

BOT_TOKEN = os.environ.get("BOT_TOKEN")
PORT = int(os.environ.get("PORT", "10000"))


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
        "files": ["block1_audit.json"],
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
        "files": ["block3_value.json"],
    },
    {
        "key": "b4",
        "title": "4 блок — Походження",
        "files": ["block4_origin.json"],
    },
    {
        "key": "b5",
        "title": "5 блок — Платежі",
        "files": ["block5_payments.json"],
    },
]


SUBBLOCK_LABELS = {
    "block2_1_constitution.json": "2.1 Конституція",
    "block2_2_civil_service.json": "2.2 Держслужба",
    "block2_3_mku.json": "2.3 МКУ",
    "block2_4_corruption.json": "2.4 Корупція",
}


# ============================================================
# USER SESSIONS
# ============================================================

user_sessions = {}


# ============================================================
# JSON
# ============================================================

def load_json_block(path: str) -> BlockFile:

    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    title = raw.get("title") or os.path.splitext(
        os.path.basename(path)
    )[0]

    questions = []

    for item in raw.get("questions", []):

        q = (item.get("q") or "").strip()

        opts = [
            str(x).strip()
            for x in (item.get("options") or [])
        ]

        try:
            ci = int(item.get("correct_index", 0))
        except (TypeError, ValueError):
            ci = 0

        exp = (item.get("explanation") or "").strip()

        if not q or len(opts) < 2:
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


def load_questions(files: List[str]) -> List[Question]:

    all_questions = []

    for filename in files:

        path = os.path.join(DATA_DIR, filename)

        if not os.path.exists(path):

            print(
                f"[WARNING] File not found: {path}",
                flush=True,
            )

            continue

        try:

            block = load_json_block(path)

            print(
                f"[INFO] Loaded {len(block.questions)} "
                f"questions from {filename}",
                flush=True,
            )

            all_questions.extend(block.questions)

        except Exception as e:

            print(
                f"[ERROR] Could not load {filename}: {e}",
                flush=True,
            )

    return all_questions


# ============================================================
# RENDER HEALTH SERVER
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):

        self.send_response(200)

        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8",
        )

        self.end_headers()

        self.wfile.write(
            b"Telegram bot is running"
        )

    def log_message(self, format, *args):
        return


def start_health_server():

    server = HTTPServer(
        ("0.0.0.0", PORT),
        HealthHandler,
    )

    print(
        f"[INFO] Health server listening on port {PORT}",
        flush=True,
    )

    server.serve_forever()


# ============================================================
# KEYBOARDS
# ============================================================

def main_menu_keyboard():

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "1. Аудит",
                callback_data="block:b1",
            )
        ],
        [
            InlineKeyboardButton(
                "2. Законодавство",
                callback_data="block:b2",
            )
        ],
        [
            InlineKeyboardButton(
                "3. Митна вартість",
                callback_data="block:b3",
            )
        ],
        [
            InlineKeyboardButton(
                "4. Походження",
                callback_data="block:b4",
            )
        ],
        [
            InlineKeyboardButton(
                "5. Платежі",
                callback_data="block:b5",
            )
        ],
    ])


def subblock_menu_keyboard():

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "2.1 Конституція",
                callback_data="subblock:block2_1_constitution.json",
            )
        ],
        [
            InlineKeyboardButton(
                "2.2 Держслужба",
                callback_data="subblock:block2_2_civil_service.json",
            )
        ],
        [
            InlineKeyboardButton(
                "2.3 МКУ",
                callback_data="subblock:block2_3_mku.json",
            )
        ],
        [
            InlineKeyboardButton(
                "2.4 Корупція",
                callback_data="subblock:block2_4_corruption.json",
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="menu:main",
            )
        ],
    ])


def answer_keyboard(question_number: int, options_count: int):

    letters = ["A", "B", "C", "D", "E", "F"]

    buttons = []

    for i in range(options_count):

        buttons.append(
            InlineKeyboardButton(
                letters[i],
                callback_data=f"answer:{question_number}:{i}",
            )
        )

    rows = []

    for i in range(0, len(buttons), 2):
        rows.append(buttons[i:i + 2])

    return InlineKeyboardMarkup(rows)


def after_question_keyboard():

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "➡️ Наступне питання",
                callback_data="next_question",
            )
        ],
        [
            InlineKeyboardButton(
                "🏠 Головне меню",
                callback_data="menu:main",
            )
        ],
    ])


def result_keyboard():

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🔄 Пройти ще раз",
                callback_data="restart",
            )
        ],
        [
            InlineKeyboardButton(
                "🏠 Головне меню",
                callback_data="menu:main",
            )
        ],
    ])


# ============================================================
# HELPERS
# ============================================================

def letter(index: int) -> str:

    letters = ["A", "B", "C", "D", "E", "F"]

    if 0 <= index < len(letters):
        return letters[index]

    return str(index + 1)


def escape_html(text: str) -> str:

    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


# ============================================================
# QUESTION PREPARATION
# ============================================================

def prepare_question(question: Question):

    options_with_correct = []

    for index, text in enumerate(question.options):

        options_with_correct.append(
            (
                text,
                index == question.correct_index,
            )
        )

    random.shuffle(options_with_correct)

    options = [
        item[0]
        for item in options_with_correct
    ]

    correct_index = next(
        i
        for i, item in enumerate(options_with_correct)
        if item[1]
    )

    return Question(
        q=question.q,
        options=options,
        correct_index=correct_index,
        explanation=question.explanation,
    )


# ============================================================
# SESSION
# ============================================================

def get_session(user_id: int):

    return user_sessions.get(user_id)


def cancel_timer(session):

    job = session.get("timer_job")

    if job:

        try:
            job.schedule_removal()
        except Exception:
            pass

        session["timer_job"] = None


def clear_session(user_id: int):

    session = user_sessions.pop(user_id, None)

    if session:
        cancel_timer(session)


# ============================================================
# START TEST
# ============================================================

async def start_test(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    files: List[str],
    block_title: str,
):

    user = update.effective_user

    if not user:
        return

    user_id = user.id

    questions = load_questions(files)

    if not questions:

        text = (
            "❌ <b>Не знайдено питань.</b>\n\n"
            f"Блок: {escape_html(block_title)}\n\n"
            "Перевір папку <code>data/</>."
        )

        if update.callback_query:

            await update.callback_query.edit_message_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=main_menu_keyboard(),
            )

        return

    random.shuffle(questions)

    if len(questions) > FINAL_N:
        questions = questions[:FINAL_N]

    prepared_questions = [
        prepare_question(q)
        for q in questions
    ]

    clear_session(user_id)

    user_sessions[user_id] = {
        "questions": prepared_questions,
        "current": 0,
        "score": 0,
        "answered": 0,
        "block_title": block_title,
        "chat_id": update.effective_chat.id,
        "timer_job": None,
        "waiting": True,
        "result_message_id": None,
    }

    await send_current_question(
        update,
        context,
        user_id,
    )


# ============================================================
# SEND QUESTION
# ============================================================

async def send_current_question(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
):

    session = get_session(user_id)

    if not session:
        return

    cancel_timer(session)

    current = session["current"]
    questions = session["questions"]

    if current >= len(questions):

        await finish_test(
            update,
            context,
            user_id,
        )

        return

    question = questions[current]

    question_number = current + 1
    total = len(questions)

    text_lines = [
        f"📚 <b>{escape_html(session['block_title'])}</b>",
        "",
        f"❓ <b>Питання {question_number}/{total}</b>",
        "",
        escape_html(question.q),
        "",
    ]

    for i, option in enumerate(question.options):

        text_lines.append(
            f"<b>{letter(i)})</b> "
            f"{escape_html(option)}"
        )

    text_lines.extend([
        "",
        f"⏱ <b>{PER_QUESTION_SECONDS} секунд</b>",
    ])

    text = "\n".join(text_lines)

    keyboard = answer_keyboard(
        current,
        len(question.options),
    )

    # IMPORTANT:
    # We SEND a NEW question instead of editing the old one.
    message = await update.effective_chat.send_message(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )

    session["message_id"] = message.message_id
    session["waiting"] = True

    job = context.job_queue.run_once(
        question_timeout,
        when=PER_QUESTION_SECONDS,
        data={
            "user_id": user_id,
            "question_number": current,
        },
    )

    session["timer_job"] = job


# ============================================================
# ANSWER
# ============================================================

async def answer_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    user = update.effective_user

    if not user:
        return

    user_id = user.id

    session = get_session(user_id)

    if not session:
        return

    if not session.get("waiting"):
        return

    try:

        _, question_number_str, answer_index_str = (
            query.data.split(":")
        )

        question_number = int(question_number_str)
        answer_index = int(answer_index_str)

    except Exception:
        return

    current = session["current"]

    if question_number != current:
        return

    question = session["questions"][current]

    cancel_timer(session)

    session["waiting"] = False
    session["answered"] += 1

    is_correct = (
        answer_index == question.correct_index
    )

    if is_correct:
        session["score"] += 1

    # IMPORTANT:
    # Do NOT edit/delete the question.
    # Just disable its buttons by editing only the markup.
    try:

        await query.edit_message_reply_markup(
            reply_markup=None
        )

    except Exception:
        pass

    # Send separate result message
    await show_answer_result(
        update,
        context,
        user_id,
        is_correct,
        selected_index=answer_index,
    )


# ============================================================
# ANSWER RESULT
# ============================================================

async def show_answer_result(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    is_correct: bool,
    selected_index: Optional[int] = None,
):

    session = get_session(user_id)

    if not session:
        return

    current = session["current"]

    question = session["questions"][current]

    correct_index = question.correct_index

    correct_text = question.options[
        correct_index
    ]

    if is_correct:
        header = "✅ <b>Правильно!</b>"
    else:
        header = "❌ <b>Неправильно!</b>"

    text_lines = [
        header,
        "",
        f"Правильна відповідь:",
        f"<b>{letter(correct_index)})</b> "
        f"{escape_html(correct_text)}",
    ]

    if (
        selected_index is not None
        and selected_index != correct_index
    ):

        selected_text = question.options[
            selected_index
        ]

        text_lines.extend([
            "",
            "Твоя відповідь:",
            f"<b>{letter(selected_index)})</b> "
            f"{escape_html(selected_text)}",
        ])

    if question.explanation:

        text_lines.extend([
            "",
            "💡 <b>Пояснення:</b>",
            escape_html(question.explanation),
        ])

    text_lines.extend([
        "",
        f"📊 Результат: "
        f"<b>{session['score']}/{session['answered']}</b>",
    ])

    # Separate message!
    message = await update.effective_chat.send_message(
        "\n".join(text_lines),
        parse_mode=ParseMode.HTML,
        reply_markup=after_question_keyboard(),
    )

    session["result_message_id"] = message.message_id


# ============================================================
# TIMEOUT
# ============================================================

async def question_timeout(
    context: ContextTypes.DEFAULT_TYPE,
):

    data = context.job.data

    user_id = data["user_id"]
    question_number = data["question_number"]

    session = get_session(user_id)

    if not session:
        return

    if not session.get("waiting"):
        return

    if session["current"] != question_number:
        return

    session["waiting"] = False
    session["answered"] += 1

    question = session["questions"][question_number]

    correct_text = question.options[
        question.correct_index
    ]

    text_lines = [
        "⏰ <b>Час вийшов!</b>",
        "",
        "❌ Правильна відповідь:",
        f"<b>{letter(question.correct_index)})</b> "
        f"{escape_html(correct_text)}",
    ]

    if question.explanation:

        text_lines.extend([
            "",
            "💡 <b>Пояснення:</b>",
            escape_html(question.explanation),
        ])

    text_lines.extend([
        "",
        f"📊 Результат: "
        f"<b>{session['score']}/{session['answered']}</b>",
    ])

    # Remove buttons from the question,
    # but KEEP the question itself.
    try:

        await context.bot.edit_message_reply_markup(
            chat_id=session["chat_id"],
            message_id=session["message_id"],
            reply_markup=None,
        )

    except Exception:
        pass

    # Send timeout as a separate message.
    message = await context.bot.send_message(
        chat_id=session["chat_id"],
        text="\n".join(text_lines),
        parse_mode=ParseMode.HTML,
        reply_markup=after_question_keyboard(),
    )

    session["result_message_id"] = message.message_id


# ============================================================
# NEXT QUESTION
# ============================================================

async def next_question_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    user = update.effective_user

    if not user:
        return

    user_id = user.id

    session = get_session(user_id)

    if not session:
        return

    # Remove buttons from result message only.
    try:

        await query.edit_message_reply_markup(
            reply_markup=None
        )

    except Exception:
        pass

    session["current"] += 1

    await send_current_question(
        update,
        context,
        user_id,
    )


# ============================================================
# FINISH
# ============================================================

async def finish_test(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
):

    session = get_session(user_id)

    if not session:
        return

    cancel_timer(session)

    total = len(session["questions"])
    score = session["score"]

    percent = 0

    if total:
        percent = round(
            score / total * 100
        )

    if percent == 100:
        emoji = "🏆"
    elif percent >= 80:
        emoji = "🎉"
    elif percent >= 60:
        emoji = "👍"
    elif percent >= 50:
        emoji = "📚"
    else:
        emoji = "🔄"

    text = (
        f"{emoji} <b>Тест завершено!</b>\n\n"
        f"📚 {escape_html(session['block_title'])}\n\n"
        f"Правильних відповідей: "
        f"<b>{score} з {total}</b>\n"
        f"Результат: <b>{percent}%</b>\n\n"
        "Можеш пройти тест ще раз."
    )

    clear_session(user_id)

    await update.effective_chat.send_message(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=result_keyboard(),
    )


# ============================================================
# START
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    user = update.effective_user

    if user:
        clear_session(user.id)

    text = (
        "👋 <b>Вітаю!</b>\n\n"
        "Це бот для проходження тестів.\n\n"
        f"⏱ На кожне питання — "
        f"<b>{PER_QUESTION_SECONDS} секунд</b>.\n"
        f"📝 У тесті — до "
        f"<b>{FINAL_N} питань</b>.\n"
        "🔀 Питання та варіанти відповідей "
        "перемішуються.\n\n"
        "<b>Оберіть блок:</b>"
    )

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_keyboard(),
    )


# ============================================================
# MAIN MENU
# ============================================================

async def main_menu_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    user = update.effective_user

    if user:
        clear_session(user.id)

    text = (
        "📚 <b>Головне меню</b>\n\n"
        "Оберіть блок для проходження:"
    )

    await query.edit_message_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_keyboard(),
    )


# ============================================================
# BLOCK
# ============================================================

async def block_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    key = query.data.split(":", 1)[1]

    block = next(
        (
            b
            for b in MAIN_BLOCKS
            if b["key"] == key
        ),
        None,
    )

    if not block:
        return

    if key == "b2":

        text = (
            "📚 <b>2 блок — Законодавство</b>\n\n"
            "Оберіть підблок:"
        )

        await query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=subblock_menu_keyboard(),
        )

        return

    await start_test(
        update,
        context,
        block["files"],
        block["title"],
    )


# ============================================================
# SUBBLOCK
# ============================================================

async def subblock_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    filename = query.data.split(":", 1)[1]

    label = SUBBLOCK_LABELS.get(
        filename,
        filename,
    )

    await start_test(
        update,
        context,
        [filename],
        label,
    )


# ============================================================
# RESTART
# ============================================================

async def restart_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    user = update.effective_user

    if user:
        clear_session(user.id)

    await query.edit_message_text(
        "📚 <b>Оберіть блок для нового тесту:</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_keyboard(),
    )


# ============================================================
# ERROR
# ============================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):

    print(
        "[ERROR] Telegram application error:",
        repr(context.error),
        flush=True,
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 60, flush=True)
    print("Starting Telegram MCQ Bot...", flush=True)
    print("=" * 60, flush=True)

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN is not configured."
        )

    print(
        "[OK] BOT_TOKEN found.",
        flush=True,
    )

    print(
        f"[INFO] BASE_DIR: {BASE_DIR}",
        flush=True,
    )

    print(
        f"[INFO] DATA_DIR: {DATA_DIR}",
        flush=True,
    )

    if not os.path.isdir(DATA_DIR):

        print(
            f"[WARNING] Data directory does not exist: "
            f"{DATA_DIR}",
            flush=True,
        )

    else:

        print(
            "[OK] Data directory found.",
            flush=True,
        )

        try:

            files = os.listdir(DATA_DIR)

            print(
                "[INFO] Data files:",
                flush=True,
            )

            for filename in files:

                print(
                    f"  - {filename}",
                    flush=True,
                )

        except Exception as e:

            print(
                f"[WARNING] Cannot list data directory: {e}",
                flush=True,
            )

    health_thread = Thread(
        target=start_health_server,
        daemon=True,
    )

    health_thread.start()

    print(
        "[INFO] Creating Telegram application...",
        flush=True,
    )

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start_command,
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            answer_callback,
            pattern=r"^answer:",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            next_question_callback,
            pattern=r"^next_question$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            main_menu_callback,
            pattern=r"^menu:main$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            block_callback,
            pattern=r"^block:",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            subblock_callback,
            pattern=r"^subblock:",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            restart_callback,
            pattern=r"^restart$",
        )
    )

    application.add_error_handler(
        error_handler
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

