
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
from threading import Thread
from http.server import BaseHTTPRequestHandler, HTTPServer
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

