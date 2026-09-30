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

# ----- Render health-check server -----

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        pass


def start_health_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()


# ----- Main -----

blocks_cache = {}

def main() -> None:
    global blocks_cache

    import os
    print("=== ENV CHECK START ===")
    print("RAILWAY_ENVIRONMENT =", os.getenv("RAILWAY_ENVIRONMENT"))
    print("HAS BOT_TOKEN =", "BOT_TOKEN" in os.environ)
    print("ENV KEYS =", sorted(os.environ.keys()))
    print("=== ENV CHECK END ===")

    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise RuntimeError("Set BOT_TOKEN env var first")

    blocks_cache = load_all_blocks()


    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(on_callback))

    Thread(target=start_health_server, daemon=True).start()

    print("Bot is running…")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
