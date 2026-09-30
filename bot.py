import os
import json
import random
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, List, Optional, Tuple

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

blocks_cache: Dict[str, "BlockFile"] = {}


# ============================================================
# RENDER HEALTH SERVER
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        if self.path == "/health":
            body = b'{"status":"ok"}'
            content_type = "application/json"
        else:
            body = b"Telegram MCQ Bot is running"
            content_type = "text/plain; charset=utf-8"

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


def start_health_server():
    port = int(os.environ.get("PORT", "10000"))

    server = ThreadingHTTPServer(
        ("0.0.0.0", port),
        HealthHandler,
    )

    thread = threading.Thread(
        target=server.serve_forever,
        daemon=True,
    )

    thread.start()

    print(f"[OK] Health server listening on port {port}")


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
# BLOCKS
# ============================================================

MAIN_BLOCKS = {
    "1": {
        "title": "1. Аудит",
        "files": [
            "block1_audit.json"
        ],
    },

    "2": {
        "title": "2. Законодавство",
        "files": [
            "block2_1_constitution.json",
            "block2_2_civil_service.json",
            "block2_3_mku.json",
            "block2_4_corruption.json",
        ],
    },

    "3": {
        "title": "3. Митна вартість",
        "files": [
            "block3_value.json"
        ],
    },

    "4": {
        "title": "4. Походження",
        "files": [
            "block4_origin.json"
        ],
    },

    "5": {
        "title": "5. Платежі",
        "files": [
            "block5_payments.json"
        ],
    },
}


SUBBLOCK_LABELS = {
    "block2_1_constitution.json":
        "2.1 Конституція України",

    "block2_2_civil_service.json":
        "2.2 Державна служба",

    "block2_3_mku.json":
        "2.3 Митний кодекс України",

    "block2_4_corruption.json":
        "2.4 Запобігання корупції",
}


# ============================================================
# LOAD QUESTIONS
# ============================================================

def load_block_file(filename: str) -> BlockFile:

    path = os.path.join(DATA_DIR, filename)

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict):
        raw_questions = data.get("questions", [])
        title = data.get(
            "title",
            SUBBLOCK_LABELS.get(filename, filename)
        )

    elif isinstance(data, list):
        raw_questions = data
        title = SUBBLOCK_LABELS.get(
            filename,
            filename
        )

    else:
        raise ValueError(
            f"Invalid JSON format: {filename}"
        )

    questions = []

    for number, item in enumerate(
        raw_questions,
        start=1
    ):

        if not isinstance(item, dict):
            raise ValueError(
                f"{filename}: question #{number} is not an object"
            )

        question_text = str(
            item.get("q", "")
        ).strip()

        options = item.get("options", [])

        correct_index = item.get(
            "correct_index"
        )

        if not question_text:
            raise ValueError(
                f"{filename}: question #{number} has empty q"
            )

        if not isinstance(options, list):
            raise ValueError(
                f"{filename}: question #{number} has invalid options"
            )

        if len(options) < 2:
            raise ValueError(
                f"{filename}: question #{number} needs at least 2 options"
            )

        options = [
            str(option)
            for option in options
        ]

        if not isinstance(
            correct_index,
            int
        ):
            raise ValueError(
                f"{filename}: question #{number} has invalid correct_index"
            )

        if not (
            0 <= correct_index < len(options)
        ):
            raise ValueError(
                f"{filename}: question #{number} correct_index out of range"
            )

        explanation = str(
            item.get("explanation", "")
        ).strip()

        questions.append(
            Question(
                q=question_text,
                options=options,
                correct_index=correct_index,
                explanation=explanation,
            )
        )

    return BlockFile(
        file=filename,
        title=title,
        questions=questions,
    )


def load_all_blocks():

    global blocks_cache

    blocks_cache = {}

    if not os.path.isdir(DATA_DIR):
        raise FileNotFoundError(
            f"Data directory not found: {DATA_DIR}"
        )

    for filename in sorted(
        os.listdir(DATA_DIR)
    ):

        if not filename.lower().endswith(".json"):
            continue

        block = load_block_file(filename)

        blocks_cache[filename] = block

        print(
            f"[OK] Loaded {filename}: "
            f"{len(block.questions)} questions"
        )


# ============================================================
# SESSION HELPERS
# ============================================================

def get_sessions(context):
    if "sessions" not in context.application.bot_data:
        context.application.bot_data["sessions"] = {}

    return context.application.bot_data["sessions"]


def get_session(context, chat_id):

    return get_sessions(context).get(chat_id)


def session_cancel_timer(session):

    job = session.get("timer_job")

    if job:

        try:
            job.schedule_removal()
        except Exception:
            pass

    session["timer_job"] = None


# ============================================================
# QUESTION HELPERS
# ============================================================

LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def format_options(options):

    return "\n".join(
        f"{LETTERS[i]}. {option}"
        for i, option in enumerate(options)
    )


def random_questions(
    questions,
    count=None,
):

    result = list(questions)

    random.shuffle(result)

    if count is not None:
        return result[:count]

    return result


def merge_questions(files):

    result = []

    for filename in files:

        block = blocks_cache.get(filename)

        if block:
            result.extend(
                block.questions
            )

    return result


def shuffle_answers(question):

    pairs = list(
        enumerate(question.options)
    )

    random.shuffle(pairs)

    options = [
        text
        for _, text in pairs
    ]

    correct_index = next(
        new_index
        for new_index, (
            old_index,
            _
        ) in enumerate(pairs)
        if old_index == question.correct_index
    )

    return options, correct_index


# ============================================================
# KEYBOARDS
# ============================================================

def answer_keyboard(
    qid,
    options_count,
):

    buttons = []

    for i in range(options_count):

        buttons.append(
            InlineKeyboardButton(
                LETTERS[i],
                callback_data=(
                    f"ans|{qid}|{i}"
                ),
            )
        )

    rows = []

    for i in range(
        0,
        len(buttons),
        6,
    ):
        rows.append(
            buttons[i:i + 6]
        )

    rows.append(
        [
            InlineKeyboardButton(
                "⛔ Завершити тест",
                callback_data="quit",
            )
        ]
    )

    return InlineKeyboardMarkup(rows)


def main_menu_keyboard():

    rows = []

    for key, block in MAIN_BLOCKS.items():

        rows.append(
            [
                InlineKeyboardButton(
                    block["title"],
                    callback_data=f"main|{key}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                "🎓 Загальний фінальний тест (20 з кожного блоку)",
                callback_data="global_final",
            )
        ]
    )

    return InlineKeyboardMarkup(rows)


def block2_keyboard():

    rows = [
        [
            InlineKeyboardButton(
                "📚 Весь блок 2",
                callback_data="full|2",
            )
        ],
        [
            InlineKeyboardButton(
                f"🎓 Фінальний тест блоку 2 ({FINAL_N})",
                callback_data="final|2",
            )
        ],
    ]

    for filename in MAIN_BLOCKS["2"]["files"]:

        rows.append(
            [
                InlineKeyboardButton(
                    SUBBLOCK_LABELS.get(
                        filename,
                        filename
                    ),
                    callback_data=f"sub|{filename}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="back_main",
            )
        ]
    )

    return InlineKeyboardMarkup(rows)


def normal_block_keyboard(block_key):

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📚 Повний тест",
                    callback_data=f"full|{block_key}",
                )
            ],
            [
                InlineKeyboardButton(
                    f"🎓 Фінальний тест ({FINAL_N})",
                    callback_data=f"final|{block_key}",
                )
            ],
            [
                InlineKeyboardButton(
                    "⬅️ Назад",
                    callback_data="back_main",
                )
            ],
        ]
    )


def subblock_keyboard(filename):

    label = SUBBLOCK_LABELS.get(
        filename,
        filename
    )

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"📚 Повний тест: {label}",
                    callback_data=f"subfull|{filename}",
                )
            ],
            [
                InlineKeyboardButton(
                    f"🎓 Фінальний тест ({FINAL_N})",
                    callback_data=f"subfinal|{filename}",
                )
            ],
            [
                InlineKeyboardButton(
                    "⬅️ Назад до законодавства",
                    callback_data="back_block2",
                )
            ],
        ]
    )


# ============================================================
# MENUS
# ============================================================

async def show_main_menu(
    update,
    context,
):

    text = (
        "📋 <b>Головне меню</b>\n\n"
        "Оберіть блок тестування:"
    )

    if update.callback_query:

        query = update.callback_query

        await query.answer()

        await query.edit_message_text(
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_keyboard(),
        )

    else:

        await update.message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_keyboard(),
        )


async def show_block_menu(
    update,
    context,
    block_key,
):

    query = update.callback_query

    await query.answer()

    block = MAIN_BLOCKS[block_key]

    if block_key == "2":
        keyboard = block2_keyboard()
    else:
        keyboard = normal_block_keyboard(
            block_key
        )

    await query.edit_message_text(
        text=(
            f"📂 <b>{block['title']}</b>\n\n"
            "Оберіть режим:"
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def show_subblock_menu(
    update,
    context,
    filename,
):

    query = update.callback_query

    await query.answer()

    label = SUBBLOCK_LABELS.get(
        filename,
        filename
    )

    await query.edit_message_text(
        text=(
            f"📂 <b>{label}</b>\n\n"
            "Оберіть режим:"
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=subblock_keyboard(
            filename
        ),
    )


# ============================================================
# BUILD TESTS
# ============================================================

def build_test_questions(
    mode,
    block_key=None,
    filename=None,
):

    if mode in (
        "subfull",
        "subfinal",
    ):

        if (
            not filename
            or filename not in blocks_cache
        ):
            return [], "Помилка: блок не знайдено."

        source = list(
            blocks_cache[filename].questions
        )

        label = SUBBLOCK_LABELS.get(
            filename,
            filename
        )

        if mode == "subfinal":

            questions = random_questions(
                source,
                FINAL_N
            )

            title = (
                f"🎓 Фінальний тест: {label}"
            )

        else:

            questions = random_questions(
                source
            )

            title = (
                f"📚 Повний тест: {label}"
            )

        return questions, title

    if mode in (
        "full",
        "final",
    ):

        if block_key not in MAIN_BLOCKS:

            return [], "Помилка: блок не знайдено."

        source = merge_questions(
            MAIN_BLOCKS[block_key]["files"]
        )

        if mode == "final":

            questions = random_questions(
                source,
                FINAL_N
            )

            title = (
                "🎓 Фінальний тест: "
                f"{MAIN_BLOCKS[block_key]['title']}"
            )

        else:

            questions = random_questions(
                source
            )

            title = (
                "📚 Повний тест: "
                f"{MAIN_BLOCKS[block_key]['title']}"
            )

        return questions, title

    return [], "Помилка режиму тесту."


def build_global_final():

    all_questions = []

    for block_key in MAIN_BLOCKS:

        source = merge_questions(
            MAIN_BLOCKS[block_key]["files"]
        )

        selected = random_questions(
            source,
            FINAL_N
        )

        all_questions.extend(
            selected
        )

    random.shuffle(all_questions)

    return (
        all_questions,
        "🎓 <b>Загальний фінальний тест</b>\n"
        "По 20 питань з кожного основного блоку."
    )


# ============================================================
# START SESSION
# ============================================================

async def start_session(
    update,
    context,
    questions,
    title,
):

    chat_id = update.effective_chat.id

    if not questions:

        await update.effective_chat.send_message(
            "❌ У цьому тесті немає питань."
        )

        return

    sessions = get_sessions(context)

    old_session = sessions.get(chat_id)

    if old_session:
        session_cancel_timer(
            old_session
        )

    session = {
        "title": title,
        "questions": questions,
        "i": 0,
        "correct": 0,
        "qid": 0,
        "current": None,
        "answered": False,
        "timer_job": None,
        "question_message_id": None,
    }

    sessions[chat_id] = session

    await update.effective_chat.send_message(
        f"{title}\n\n"
        f"📊 Кількість питань: "
        f"<b>{len(questions)}</b>\n"
        f"⏱ Час на питання: "
        f"<b>{PER_QUESTION_SECONDS} секунд</b>",
        parse_mode=ParseMode.HTML,
    )

    await send_question(
        chat_id,
        context
    )


# ============================================================
# SEND QUESTION
# ============================================================

async def send_question(
    chat_id,
    context,
):

    session = get_session(
        context,
        chat_id
    )

    if not session:
        return

    session_cancel_timer(
        session
    )

    if session["i"] >= len(
        session["questions"]
    ):

        await finish_session_direct(
            chat_id,
            context
        )

        return

    question = session["questions"][
        session["i"]
    ]

    options, correct_index = shuffle_answers(
        question
    )

    session["qid"] += 1

    session["current"] = {
        "question": question,
        "options": options,
        "correct_index": correct_index,
    }

    session["answered"] = False

    qid = session["qid"]

    text = (
        f"❓ <b>Питання "
        f"{session['i'] + 1}/"
        f"{len(session['questions'])}</b>\n\n"
        f"{question.q}\n\n"
        f"{format_options(options)}"
    )

    message = await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=ParseMode.HTML,
        reply_markup=answer_keyboard(
            qid,
            len(options)
        ),
    )

    session["question_message_id"] = (
        message.message_id
    )

    session["timer_job"] = (
        context.job_queue.run_once(
            timeout_question,
            PER_QUESTION_SECONDS,
            data={
                "chat_id": chat_id,
                "qid": qid,
            },
        )
    )


# ============================================================
# TIMEOUT
# ============================================================

async def timeout_question(context):

    data = context.job.data

    chat_id = data["chat_id"]
    qid = data["qid"]

    session = get_session(
        context,
        chat_id
    )

    if not session:
        return

    if session["qid"] != qid:
        return

    if session["answered"]:
        return

    session["answered"] = True
    session["timer_job"] = None

    current = session["current"]

    if not current:
        return

    try:

        await context.bot.edit_message_reply_markup(
            chat_id=chat_id,
            message_id=session[
                "question_message_id"
            ],
            reply_markup=None,
        )

    except Exception:
        pass

    correct_index = current[
        "correct_index"
    ]

    correct_answer = current[
        "options"
    ][correct_index]

    explanation = current[
        "question"
    ].explanation

    result = (
        "⏰ <b>Час вичерпано!</b>\n\n"
        f"❌ Правильна відповідь: "
        f"<b>{LETTERS[correct_index]}. "
        f"{correct_answer}</b>"
    )

    if explanation:
        result += (
            f"\n\n💡 {explanation}"
        )

    await context.bot.send_message(
        chat_id=chat_id,
        text=result,
        parse_mode=ParseMode.HTML,
    )

    session["i"] += 1

    if session["i"] >= len(
        session["questions"]
    ):

        await finish_session_direct(
            chat_id,
            context
        )

        return

    await context.bot.send_message(
        chat_id=chat_id,
        text="➡️ <b>Наступне питання</b>",
        parse_mode=ParseMode.HTML,
    )

    await send_question(
        chat_id,
        context
    )


# ============================================================
# FINISH
# ============================================================

async def finish_session_direct(
    chat_id,
    context,
):

    sessions = get_sessions(context)

    session = sessions.get(chat_id)

    if not session:
        return

    session_cancel_timer(
        session
    )

    total = len(
        session["questions"]
    )

    correct = session["correct"]

    wrong = total - correct

    percent = (
        round(
            correct / total * 100
        )
        if total
        else 0
    )

    text = (
        "🏁 <b>Тест завершено!</b>\n\n"
        f"📚 {session['title']}\n\n"
        f"✅ Правильних: <b>{correct}</b>\n"
        f"❌ Неправильних: <b>{wrong}</b>\n"
        f"📊 Результат: <b>{percent}%</b>\n\n"
        "Натисніть /start, "
        "щоб обрати інший тест."
    )

    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=ParseMode.HTML,
    )

    sessions.pop(
        chat_id,
        None
    )


# ============================================================
# /START
# ============================================================

async def start_command(
    update,
    context,
):

    chat_id = update.effective_chat.id

    sessions = get_sessions(context)

    old_session = sessions.get(
        chat_id
    )

    if old_session:
        session_cancel_timer(
            old_session
        )

        sessions.pop(
            chat_id,
            None
        )

    await show_main_menu(
        update,
        context
    )


# ============================================================
# CALLBACKS
# ============================================================

async def callback_handler(
    update,
    context,
):

    query = update.callback_query

    data = query.data or ""

    chat_id = update.effective_chat.id

    # --------------------------------------------------------
    # NOOP
    # --------------------------------------------------------

    if data == "noop":

        await query.answer()

        return

    # --------------------------------------------------------
    # BACK MAIN
    # --------------------------------------------------------

    if data == "back_main":

        await show_main_menu(
            update,
            context
        )

        return

    # --------------------------------------------------------
    # BACK BLOCK 2
    # --------------------------------------------------------

    if data == "back_block2":

        await show_block_menu(
            update,
            context,
            "2"
        )

        return

    # --------------------------------------------------------
    # MAIN BLOCK
    # --------------------------------------------------------

    if data.startswith("main|"):

        block_key = data.split(
            "|",
            1
        )[1]

        if block_key not in MAIN_BLOCKS:

            await query.answer(
                "Блок не знайдено.",
                show_alert=True
            )

            return

        await show_block_menu(
            update,
            context,
            block_key
        )

        return

    # --------------------------------------------------------
    # SUBBLOCK
    # --------------------------------------------------------

    if data.startswith("sub|"):

        filename = data.split(
            "|",
            1
        )[1]

        if filename not in blocks_cache:

            await query.answer(
                "Файл блоку не знайдено.",
                show_alert=True
            )

            return

        await show_subblock_menu(
            update,
            context,
            filename
        )

        return

    # --------------------------------------------------------
    # FULL MAIN
    # --------------------------------------------------------

    if data.startswith("full|"):

        block_key = data.split(
            "|",
            1
        )[1]

        questions, title = build_test_questions(
            "full",
            block_key=block_key
        )

        await query.answer()

        await start_session(
            update,
            context,
            questions,
            title
        )

        return

    # --------------------------------------------------------
    # FINAL MAIN
    # --------------------------------------------------------

    if data.startswith("final|"):

        block_key = data.split(
            "|",
            1
        )[1]

        questions, title = build_test_questions(
            "final",
            block_key=block_key
        )

        await query.answer()

        await start_session(
            update,
            context,
            questions,
            title
        )

        return

    # --------------------------------------------------------
    # FULL SUBBLOCK
    # --------------------------------------------------------

    if data.startswith("subfull|"):

        filename = data.split(
            "|",
            1
        )[1]

        questions, title = build_test_questions(
            "subfull",
            filename=filename
        )

        await query.answer()

        await start_session(
            update,
            context,
            questions,
            title
        )

        return

    # --------------------------------------------------------
    # FINAL SUBBLOCK
    # --------------------------------------------------------

    if data.startswith("subfinal|"):

        filename = data.split(
            "|",
            1
        )[1]

        questions, title = build_test_questions(
            "subfinal",
            filename=filename
        )

        await query.answer()

        await start_session(
            update,
            context,
            questions,
            title
        )

        return

    # --------------------------------------------------------
    # GLOBAL FINAL
    # --------------------------------------------------------

    if data == "global_final":

        questions, title = build_global_final()

        await query.answer()

        await start_session(
            update,
            context,
            questions,
            title
        )

        return

    # --------------------------------------------------------
    # QUIT
    # --------------------------------------------------------

    if data == "quit":

        await query.answer()

        session = get_session(
            context,
            chat_id
        )

        if not session:
            return

        session_cancel_timer(
            session
        )

        try:

            await query.edit_message_reply_markup(
                reply_markup=None
            )

        except Exception:
            pass

        get_sessions(
            context
        ).pop(
            chat_id,
            None
        )

        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "⛔ <b>Тест завершено "
                "достроково.</b>\n\n"
                "Натисніть /start, "
                "щоб обрати інший тест."
            ),
            parse_mode=ParseMode.HTML,
        )

        return

    # --------------------------------------------------------
    # ANSWER
    # --------------------------------------------------------

    if data.startswith("ans|"):

        parts = data.split("|")

        if len(parts) != 3:

            await query.answer()

            return

        try:

            qid = int(parts[1])
            selected_index = int(parts[2])

        except ValueError:

            await query.answer()

            return

        session = get_session(
            context,
            chat_id
        )

        if not session:

            await query.answer(
                "Тест вже завершено.",
                show_alert=True
            )

            return

        if session["qid"] != qid:

            await query.answer(
                "Це питання вже неактивне.",
                show_alert=True
            )

            return

        if session["answered"]:

            await query.answer(
                "Ви вже відповіли.",
                show_alert=True
            )

            return

        current = session["current"]

        if not current:

            await query.answer()

            return

        options = current["options"]

        if not (
            0 <= selected_index < len(options)
        ):

            await query.answer()

            return

        session["answered"] = True

        session_cancel_timer(
            session
        )

        try:

            await query.edit_message_reply_markup(
                reply_markup=None
            )

        except Exception:
            pass

        correct_index = current[
            "correct_index"
        ]

        correct_answer = options[
            correct_index
        ]

        selected_answer = options[
            selected_index
        ]

        is_correct = (
            selected_index == correct_index
        )

        if is_correct:

            session["correct"] += 1

            result = (
                "✅ <b>Правильно!</b>\n\n"
                f"Ваша відповідь: "
                f"<b>{LETTERS[selected_index]}. "
                f"{selected_answer}</b>"
            )

        else:

            result = (
                "❌ <b>Неправильно!</b>\n\n"
                f"Ваша відповідь: "
                f"<b>{LETTERS[selected_index]}. "
                f"{selected_answer}</b>\n\n"
                f"Правильна відповідь: "
                f"<b>{LETTERS[correct_index]}. "
                f"{correct_answer}</b>"
            )

        explanation = current[
            "question"
        ].explanation

        if explanation:

            result += (
                f"\n\n💡 {explanation}"
            )

        await query.answer(
            "Правильно!"
            if is_correct
            else "Неправильно!"
        )

        await context.bot.send_message(
            chat_id=chat_id,
            text=result,
            parse_mode=ParseMode.HTML,
        )

        session["i"] += 1

        if session["i"] >= len(
            session["questions"]
        ):

            await finish_session_direct(
                chat_id,
                context
            )

            return

        await context.bot.send_message(
            chat_id=chat_id,
            text="➡️ <b>Наступне питання</b>",
            parse_mode=ParseMode.HTML,
        )

        await send_question(
            chat_id,
            context
        )

        return

    await query.answer()


# ============================================================
# MAIN
# ============================================================

def main():

    print("========================================")
    print("Telegram MCQ Bot starting...")
    print("========================================")

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN environment variable is not set."
        )

    print("[OK] BOT_TOKEN found.")

    print(
        f"[INFO] BASE_DIR: {BASE_DIR}"
    )

    print(
        f"[INFO] DATA_DIR: {DATA_DIR}"
    )

    if not os.path.isdir(DATA_DIR):

        raise RuntimeError(
            f"Data directory not found: {DATA_DIR}"
        )

    print(
        "[OK] Data directory found."
    )

    print(
        "[INFO] Loading question files..."
    )

    load_all_blocks()

    print(
        "[INFO] Starting health server..."
    )

    start_health_server()

    print(
        "[INFO] Creating Telegram application..."
    )

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start_command
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            callback_handler
        )
    )

    print(
        "[OK] Telegram application created."
    )

    print(
        "[INFO] Starting polling..."
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
