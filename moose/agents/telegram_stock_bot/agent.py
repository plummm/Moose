from __future__ import annotations

import asyncio
import json
import os
import threading
import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, available_timezones

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ForceReply,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from moose.framework import BaseAgent

# Import local agent modules (agent code is mounted into /app; do not import from the installed `moose` package)
from db import StockBotDB
from fmp_client import FmpClient, FmpConfig
from market_times import NY_TZ, is_weekend, near_time, next_open_phrase, now_in_ny
from render import fmt_symbol_html, quote_card_html
from symbols import candidates_for_input, normalize_user_symbol, asset_type_from_symbol
from ticker_heuristics import looks_like_ticker
from llm_tools import create_tools
from llm_router import RouterConfig, run_router
from finance_office_client import FinanceOfficeClient


ASK_TICKER, ASK_TZ = range(2)


def _get_required_env(key: str) -> str:
    val = os.getenv(key, "").strip()
    if not val:
        raise RuntimeError(f"Missing required environment variable: {key}")
    return val


def _chat_title(update: Update) -> str | None:
    chat = update.effective_chat
    if not chat:
        return None
    return chat.title or chat.username or None


class TelegramStockBotAgent(BaseAgent):
    name = "telegram_stock_bot"
    description = "Telegram stock/crypto bot with watchlists and alerts (FMP-backed)"

    def __init__(self, config_path=None, debug: bool = False):
        super().__init__(config_path=config_path, debug=debug)
        self._db_path = os.getenv("STOCKBOT_DB_PATH", "/data/db.sqlite")
        self._exchange = os.getenv("STOCKBOT_EXCHANGE", "NASDAQ").strip() or "NASDAQ"
        self._token = _get_required_env("TELEGRAM_BOT_TOKEN")
        self._fmp_key = _get_required_env("FMP_API_KEY")

        self.db = StockBotDB(self._db_path)
        self.fmp = FmpClient(FmpConfig(api_key=self._fmp_key))
        self.finance_office = FinanceOfficeClient()

        custom = self.config.get("custom", {}) if isinstance(self.config.get("custom"), dict) else {}
        polling = custom.get("polling", {}) if isinstance(custom.get("polling"), dict) else {}
        self.alert_interval_seconds = int(polling.get("alert_interval_seconds", 300))
        self.clock_tick_seconds = int(polling.get("clock_tick_seconds", 60))

        stockbot_llm = custom.get("stockbot_llm_config", {}) if isinstance(custom.get("stockbot_llm_config"), dict) else {}

        def _as_bool(v, default: bool) -> bool:
            if isinstance(v, bool):
                return v
            if isinstance(v, (int, float)):
                return bool(v)
            if isinstance(v, str):
                s = v.strip().lower()
                if s in ("1", "true", "t", "yes", "y", "on"):
                    return True
                if s in ("0", "false", "f", "no", "n", "off"):
                    return False
            return default

        self.router_cfg = RouterConfig(
            model=str(stockbot_llm.get("model") or "gpt-5-mini"),
            temperature=float(stockbot_llm.get("temperature", 0.2)),
            max_tool_iterations=int(stockbot_llm.get("max_tool_iterations", 5)),
            enable_multi_stage_reasoning=_as_bool(stockbot_llm.get("enable_multi_stage_reasoning"), True),
        )
        # Only include messages newer than this window in LLM context (hard cutoff).
        # Keeping it configurable prevents stale context from influencing tool routing.
        try:
            self.context_max_age_seconds = int(custom.get("context_max_age_seconds", 5 * 60))
        except Exception:
            self.context_max_age_seconds = 5 * 60
        if self.context_max_age_seconds <= 0:
            self.context_max_age_seconds = 5 * 60

        self._app: Application | None = None
        self._stop_event = asyncio.Event()
        self._holiday_closed_dates: set[str] = set()
        self._holiday_cache_day: str | None = None
        self._bot_thread: threading.Thread | None = None
        self._bot_thread_started = False

    # ---- Moose run mode (override) ----
    def run_http_server(self, port: int | None = None, host: str = "0.0.0.0"):
        """
        Run the Telegram bot in the background while exposing the standard Moose HTTP server
        (health + homepage + logs). This matches Moose's http-mode container expectations.
        """
        if not self._bot_thread_started:
            self._bot_thread_started = True
            self._bot_thread = threading.Thread(target=self._run_bot_thread, daemon=True)
            self._bot_thread.start()
            self.logger.info("Telegram polling started in background thread")

        return super().run_http_server(port=port, host=host)

    def _run_bot_thread(self) -> None:
        try:
            asyncio.run(self._run_bot())
        except Exception as e:
            # If the bot crashes, keep HTTP server alive for logs/visibility.
            try:
                self.logger.error(f"Telegram bot thread crashed: {e}")
            except Exception:
                pass

    def run_stdin_mode(self):
        """
        Moose's BaseAgent stdin mode is a JSON loop; for this agent we run a long-lived
        Telegram bot process instead.
        """
        self.logger.info("Starting telegram_stock_bot (stdin mode overridden)")
        try:
            asyncio.run(self._run_bot())
        finally:
            try:
                self.db.close()
            except Exception:
                pass

    async def _run_bot(self) -> None:
        app = ApplicationBuilder().token(self._token).build()
        self._app = app

        # Conversations
        conv_ticker = ConversationHandler(
            entry_points=[
                CommandHandler("get_price", self.cmd_get_price),
                CommandHandler("add_to_watchlist", self.cmd_add_to_watchlist),
                CommandHandler("remove_from_watchlist", self.cmd_remove_from_watchlist),
            ],
            states={
                ASK_TICKER: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_ticker_reply),
                ],
            },
            fallbacks=[CommandHandler("cancel", self.cmd_cancel)],
            name="ticker_conv",
            persistent=False,
        )

        conv_tz = ConversationHandler(
            entry_points=[CommandHandler("start", self.cmd_start), CommandHandler("set_timezone", self.cmd_set_timezone)],
            states={
                ASK_TZ: [
                    CallbackQueryHandler(self.on_tz_button, pattern=r"^tz:"),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_tz_text),
                ],
            },
            fallbacks=[CommandHandler("cancel", self.cmd_cancel)],
            name="tz_conv",
            persistent=False,
        )

        app.add_handler(conv_tz)
        app.add_handler(conv_ticker)

        # Non-conversation commands
        app.add_handler(CommandHandler("show_the_watchlist", self.cmd_show_watchlist))
        app.add_handler(CommandHandler("get_price_from_watchlist", self.cmd_price_from_watchlist))
        app.add_handler(CommandHandler("next_market_open_time", self.cmd_next_market_open_time))
        app.add_handler(CallbackQueryHandler(self.on_watchlist_pick, pattern=r"^wl:"))
        app.add_handler(CallbackQueryHandler(self.on_symbol_pick, pattern=r"^pick:"))
        app.add_handler(CallbackQueryHandler(self.on_clarify_submit, pattern=r"^cf_submit:"))
        app.add_handler(CallbackQueryHandler(self.on_clarify_pick, pattern=r"^cf:"))

        # Free text router trigger (DM always; group only mention or reply-to-bot)
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_free_text))

        # Background jobs
        app.job_queue.run_repeating(self.job_alerts, interval=self.alert_interval_seconds, first=10)
        app.job_queue.run_repeating(self.job_clock_tick, interval=self.clock_tick_seconds, first=5)

        self.logger.info("Telegram application initialized; starting polling")
        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)

        # Block until stop requested
        while not self.shutdown_requested and not self._stop_event.is_set():
            await asyncio.sleep(0.5)

        self.logger.info("Stopping Telegram application")
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        await self.fmp.aclose()
        await self.finance_office.aclose()

    # ---- Helpers ----
    def _get_text(self, msg) -> str:
        return (getattr(msg, "text", None) or getattr(msg, "caption", None) or "").strip()

    def _entities_as_dicts(self, msg) -> list[dict] | None:
        if not msg or not getattr(msg, "entities", None):
            return None
        out = []
        for ent in msg.entities:
            try:
                out.append({"type": ent.type, "offset": ent.offset, "length": ent.length})
            except Exception:
                pass
        return out or None

    def _store_incoming(self, update: Update) -> None:
        msg = update.effective_message
        chat = update.effective_chat
        if not msg or not chat:
            return
        txt = self._get_text(msg)
        if not txt:
            return
        from_user = getattr(msg, "from_user", None)
        self.db.add_chat_message(
            chat_id=chat.id,
            message_id=msg.message_id,
            date_ts=int(msg.date.timestamp()),
            from_user_id=(from_user.id if from_user else None),
            from_username=(from_user.username if from_user else None),
            from_is_bot=bool(from_user.is_bot) if from_user else False,
            text=txt,
            reply_to_message_id=(msg.reply_to_message.message_id if msg.reply_to_message else None),
            entities=self._entities_as_dicts(msg),
            keep_last=100,
        )

    async def _send_and_store(
        self,
        *,
        chat_id: int,
        text: str,
        reply_to_message_id: int | None = None,
        parse_mode: ParseMode | None = None,
    ):
        assert self._app is not None
        msg = await self._app.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_to_message_id=reply_to_message_id,
            parse_mode=parse_mode,
        )
        self.db.add_chat_message(
            chat_id=msg.chat_id,
            message_id=msg.message_id,
            date_ts=int(msg.date.timestamp()),
            from_user_id=None,
            from_username=None,
            from_is_bot=True,
            text=self._get_text(msg),
            reply_to_message_id=(msg.reply_to_message.message_id if msg.reply_to_message else None),
            entities=None,
            keep_last=100,
        )
        return msg

    def _should_route_free_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> tuple[bool, str]:
        chat = update.effective_chat
        msg = update.effective_message
        if not chat or not msg:
            return False, "none"
        if chat.type == "private":
            return True, "dm"
        # Reply-to-bot triggers
        if msg.reply_to_message and getattr(msg.reply_to_message, "from_user", None) and msg.reply_to_message.from_user.is_bot:
            return True, "reply"
        # Mention triggers
        username = (getattr(context.bot, "username", None) or "").strip()
        if username and msg.entities and msg.text:
            for ent in msg.entities:
                if ent.type == "mention":
                    mention = msg.text[ent.offset : ent.offset + ent.length]
                    if mention.lower() == f"@{username.lower()}":
                        return True, "mention"
        return False, "none"

    def _build_context_window(self, *, chat_id: int, trigger_date_ts: int) -> tuple[str, list[int]]:
        rows = self.db.get_context_window_before(
            chat_id=chat_id,
            before_date_ts=trigger_date_ts,
            limit=10,
            max_age_seconds=self.context_max_age_seconds,
        )
        ids: list[int] = []
        lines: list[str] = []
        for r in rows:
            ids.append(int(r["message_id"]))
            speaker = "bot" if int(r["from_is_bot"]) == 1 else f"user:{r['from_user_id'] or 'unknown'}"
            lines.append(f"[{int(r['date_ts'])}] {speaker}: {str(r['text'] or '')}")
        return "\n".join(lines), ids

    def _normalize_clarification_fields(self, question: str, fields: list[dict]) -> list[dict]:
        """
        Normalize router-provided clarification fields into a safe, compact button set.

        Rules enforced:
        - At most 4 options per field (plus optional "All of the above")
        - Drop options that repeat the question text
        - If allow_multiple=true and there are >=2 options, add final "All of the above"
        """
        q = (question or "").strip()
        out: list[dict] = []
        for f in fields or []:
            if not isinstance(f, dict):
                continue
            field = str(f.get("field") or "").strip()
            if not field:
                continue
            allow_multiple = bool(f.get("allow_multiple", False))
            raw_opts = f.get("options") or []
            opts: list[str] = []
            for o in raw_opts:
                s = str(o or "").strip()
                if not s:
                    continue
                if q and s == q:
                    continue
                opts.append(s)

            # De-dupe while preserving order
            deduped: list[str] = []
            seen: set[str] = set()
            for s in opts:
                key = s.lower()
                if key in seen:
                    continue
                seen.add(key)
                deduped.append(s)

            deduped = deduped[:4]
            if allow_multiple and len(deduped) >= 2:
                if all(x.lower() != "all of the above" for x in deduped):
                    deduped.append("All of the above")
            out.append({"field": field, "options": deduped, "allow_multiple": allow_multiple})
        return out

    def _render_clarify_keyboard(
        self, *, flow_id: str, fields: list[dict], answers: dict
    ) -> InlineKeyboardMarkup:
        """
        Build a single inline keyboard for all missing_fields:
        - Option buttons toggle selection state (single- or multi-select per field)
        - A final Submit button triggers routing with the collected answers
        """
        kb: list[list[InlineKeyboardButton]] = []
        for fi, f in enumerate(fields or []):
            if not isinstance(f, dict):
                continue
            field = str(f.get("field") or "").strip()
            if not field:
                continue
            allow_multiple = bool(f.get("allow_multiple", False))
            options = list(f.get("options") or [])

            selected = answers.get(field)
            selected_list = selected if isinstance(selected, list) else []
            for oi, opt in enumerate(options):
                opt_s = str(opt)
                is_selected = False
                if allow_multiple:
                    is_selected = opt_s in selected_list
                else:
                    is_selected = selected == opt_s
                mark = "✅" if is_selected else "⬜"
                # Field grouping is shown in the message text; keep button labels short and unambiguous.
                label = f"{mark} {opt_s}"
                kb.append([InlineKeyboardButton(label, callback_data=f"cf:{flow_id}:{fi}:{oi}")])

        kb.append([InlineKeyboardButton("Submit", callback_data=f"cf_submit:{flow_id}")])
        return InlineKeyboardMarkup(kb)

    async def _run_llm(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        *,
        trigger_type: str,
        intent: str = "",
        text_override: str | None = None,
        trigger_date_ts_override: int | None = None,
    ) -> dict:
        chat = update.effective_chat
        msg = update.effective_message
        assert chat and msg

        self.db.upsert_chat(chat.id, chat.type, _chat_title(update))
        chat_row = self.db.get_chat(chat.id)
        tz_name = chat_row.timezone if chat_row else "America/New_York"

        trigger_text = (text_override or self._get_text(msg)).strip()
        replied_text = self._get_text(msg.reply_to_message) if msg.reply_to_message else ""
        watchlist = [s for (s, _t) in self.db.list_watchlist(chat.id)]
        trigger_ts = int(trigger_date_ts_override or msg.date.timestamp())
        context_window, context_ids = self._build_context_window(
            chat_id=chat.id,
            trigger_date_ts=trigger_ts,
        )

        user_prompt = (
            f"chat_type: {chat.type}\n"
            f"trigger_type: {trigger_type}\n"
            f"intent: {intent}\n"
            f"bot_username: @{getattr(context.bot, 'username', '')}\n\n"
            f"timezone: {tz_name}\n"
            f"current_time_chat_tz: {datetime.now(tz=ZoneInfo(tz_name)).isoformat()}\n"
            f"current_time_ny: {datetime.now(tz=NY_TZ).isoformat()}\n"
            f"exchange: {self._exchange}\n\n"
            f"watchlist: {watchlist}\n\n"
            f"trigger_message:\n{trigger_text}\n\n"
            f"replied_to_message (if any):\n{replied_text}\n\n"
            f"context_window (10 prior messages, oldest->newest):\n{context_window}\n"
        )

        tools = create_tools(
            db=self.db,
            fmp=self.fmp,
            chat_id=chat.id,
            chat_timezone=tz_name,
            exchange=self._exchange,
        )
        out = await run_router(cfg=self.router_cfg, tools=tools, user_prompt=user_prompt)

        if isinstance(out, dict) and out.get("action") == "clarify":
            clar = out.get("clarification") if isinstance(out.get("clarification"), dict) else {}
            qtext = str(clar.get("question") or "Can you clarify?").strip()
            norm_fields = self._normalize_clarification_fields(qtext, list(clar.get("missing_fields") or []))
            flow_id = str(uuid.uuid4())
            now_ts = int(datetime.utcnow().timestamp())
            self.db.create_pending_flow(
                chat_id=chat.id,
                flow_id=flow_id,
                status="awaiting_clarification",
                created_at_ts=now_ts,
                expires_at_ts=now_ts + 20 * 60,
                initiator_message_id=msg.message_id,
                context_message_ids=context_ids,
                router_state=out,
                expected_fields=norm_fields,
                answers={},
                clarification_message_id=None,
            )
            out["_flow_id"] = flow_id

        return out

    async def _handle_router_out(self, update: Update, context: ContextTypes.DEFAULT_TYPE, out: dict) -> None:
        chat = update.effective_chat
        msg = update.effective_message
        assert chat and msg
        action = (out or {}).get("action")

        if action == "answer_direct":
            payload = (out.get("direct_answer") or {}) if isinstance(out.get("direct_answer"), dict) else {}
            txt = str(payload.get("text") or "").strip() or "(empty)"
            await self._send_and_store(
                chat_id=chat.id,
                text=txt,
                reply_to_message_id=msg.message_id,
                parse_mode=ParseMode.HTML,
            )
            return

        if action == "refuse":
            await self._send_and_store(
                chat_id=chat.id,
                text="I can only help with finance/market questions.",
                reply_to_message_id=msg.message_id,
            )
            return

        if action == "clarify":
            clar = out.get("clarification") if isinstance(out.get("clarification"), dict) else {}
            qtext = str(clar.get("question") or "Can you clarify?").strip()
            flow_id = str(out.get("_flow_id") or "")
            fields = self._normalize_clarification_fields(qtext, list(clar.get("missing_fields") or []))
            answers: dict = {}

            # Build per-field section headings in message text so button groups are not mixed.
            sections: list[str] = []
            for i, f in enumerate(fields):
                if not isinstance(f, dict):
                    continue
                fname = str(f.get("field") or "").strip()
                if not fname:
                    continue
                multi = bool(f.get("allow_multiple", False))
                hint = "pick one or more" if multi else "pick one"
                sections.append(f"<b>{i+1}. {fname}</b> — {hint}")

            section_text = ("\n".join(sections)).strip()
            if section_text:
                section_text = "\n\n" + section_text

            sent = await self._send_and_store(
                chat_id=chat.id,
                text=(
                    qtext
                    + section_text
                    + "\n\nSelect options below (you can toggle), then press Submit."
                    + "\n(You can also reply in your own words.)"
                ),
                reply_to_message_id=msg.message_id,
                parse_mode=ParseMode.HTML,
            )
            if flow_id:
                self.db.update_pending_flow(chat_id=chat.id, flow_id=flow_id, clarification_message_id=sent.message_id)
                # Persist the final displayed options so callback indices always line up.
                self.db.update_pending_flow(chat_id=chat.id, flow_id=flow_id, expected_fields=fields)

            try:
                await self._app.bot.edit_message_reply_markup(
                    chat_id=chat.id,
                    message_id=sent.message_id,
                    reply_markup=self._render_clarify_keyboard(flow_id=flow_id, fields=fields, answers=answers),
                )
            except Exception:
                pass
            return

        if action == "dispatch_finance_office":
            fo = out.get("finance_office") if isinstance(out.get("finance_office"), dict) else {}
            instruction = str(fo.get("instruction") or "").strip()
            ctx_text = str(fo.get("context") or "").strip()
            analyzer_data = fo.get("analyzer_data") if isinstance(fo.get("analyzer_data"), dict) else {}

            await self._send_and_store(
                chat_id=chat.id,
                text="Got it — I’m gathering data and running the analysis. I’ll get back to you in a few minutes.",
                reply_to_message_id=msg.message_id,
            )

            async def _do():
                try:
                    resp = await self.finance_office.run_task(
                        instruction=instruction,
                        context=ctx_text,
                        analyzer_data=analyzer_data,
                    )
                    if resp.get("status") != "success":
                        text = f"FinanceOffice error: {resp.get('error') or 'unknown'}"
                    else:
                        text = str(resp.get("result") or "") or "(FinanceOffice returned empty result)"
                    await self._send_and_store(chat_id=chat.id, text=text, reply_to_message_id=msg.message_id)
                except Exception as e:
                    await self._send_and_store(
                        chat_id=chat.id,
                        text=f"FinanceOffice call failed: {e}",
                        reply_to_message_id=msg.message_id,
                    )

            asyncio.create_task(_do())
            return

        await self._send_and_store(
            chat_id=chat.id,
            text="I couldn't understand that. Try rephrasing.",
            reply_to_message_id=msg.message_id,
        )

    async def on_clarify_pick(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        q = update.callback_query
        if not q or not q.data:
            return
        await q.answer()
        parts = q.data.split(":")
        if len(parts) != 4:
            return
        _, flow_id, fi_s, oi_s = parts
        chat_id = q.message.chat_id
        row = self.db.get_pending_flow(chat_id, flow_id)
        if not row:
            await q.edit_message_text("This prompt expired. Please ask again.")
            return
        expected = json.loads(row["expected_fields_json"])
        answers = json.loads(row["answers_json"])
        fi = int(fi_s)
        oi = int(oi_s)
        field = (expected[fi] or {}).get("field")
        allow_multiple = bool((expected[fi] or {}).get("allow_multiple", False))
        options = list((expected[fi] or {}).get("options") or [])
        option = options[oi] if 0 <= oi < len(options) else None
        field_key = str(field or "").strip()
        if not field_key or option is None:
            return

        opt_s = str(option)
        all_key = "all of the above"
        base_opts = [str(o) for o in options if str(o).strip().lower() != all_key]

        if allow_multiple:
            cur = answers.get(field_key)
            cur_list = cur if isinstance(cur, list) else []
            cur_list = [str(x) for x in cur_list]
            if opt_s.strip().lower() == all_key:
                if len(cur_list) == len(base_opts) and set(cur_list) == set(base_opts):
                    answers[field_key] = []
                else:
                    answers[field_key] = list(base_opts)
            else:
                if opt_s in cur_list:
                    cur_list = [x for x in cur_list if x != opt_s]
                else:
                    cur_list.append(opt_s)
                answers[field_key] = cur_list
        else:
            cur = answers.get(field_key)
            if cur == opt_s:
                answers[field_key] = ""
            else:
                answers[field_key] = opt_s
        self.db.update_pending_flow(chat_id=chat_id, flow_id=flow_id, answers=answers)

        try:
            await self._app.bot.edit_message_reply_markup(
                chat_id=chat_id,
                message_id=q.message.message_id,
                reply_markup=self._render_clarify_keyboard(flow_id=flow_id, fields=expected, answers=answers),
            )
        except Exception:
            pass

    async def on_clarify_submit(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        q = update.callback_query
        if not q or not q.data:
            return
        await q.answer()
        parts = q.data.split(":")
        if len(parts) != 2:
            return
        _, flow_id = parts
        chat_id = q.message.chat_id
        row = self.db.get_pending_flow(chat_id, flow_id)
        if not row:
            try:
                await q.edit_message_text("This prompt expired. Please ask again.")
            except Exception:
                pass
            return

        expected = json.loads(row["expected_fields_json"])
        answers = json.loads(row["answers_json"])

        missing: list[str] = []
        for f in expected or []:
            if not isinstance(f, dict):
                continue
            field = str(f.get("field") or "").strip()
            if not field:
                continue
            val = answers.get(field)
            if isinstance(val, list):
                if not val:
                    missing.append(field)
            else:
                if not str(val or "").strip():
                    missing.append(field)

        if missing:
            try:
                await q.answer(text="Please select: " + ", ".join(missing), show_alert=True)
            except Exception:
                pass
            return

        # Prevent accidental double-submits by removing the keyboard.
        try:
            await self._app.bot.edit_message_reply_markup(chat_id=chat_id, message_id=q.message.message_id, reply_markup=None)
        except Exception:
            pass

        self.db.update_pending_flow(chat_id=chat_id, flow_id=flow_id, status="submitted")

        # Persist a synthetic "user" message for this submit so future context windows include it.
        # (CallbackQuery clicks are not chat messages, so otherwise they won't appear in chat history.)
        now_ts = int(datetime.utcnow().timestamp())
        try:
            u = update.effective_user
            synthetic_msg_id = -(now_ts * 1_000_000 + (uuid.uuid4().int % 1_000_000))
            self.db.add_chat_message(
                chat_id=chat_id,
                message_id=int(synthetic_msg_id),
                date_ts=int(now_ts),
                from_user_id=(u.id if u else None),
                from_username=(u.username if u else None),
                from_is_bot=False,
                text=f"Clarification answers: {answers}",
                reply_to_message_id=(q.message.message_id if q and q.message else None),
                entities=None,
                keep_last=100,
            )
        except Exception:
            pass

        out2 = await self._run_llm(
            update,
            context,
            trigger_type="clarification",
            intent="clarify",
            text_override=f"Clarification answers: {answers}",
            # Anchor context window to now (not the older bot prompt timestamp).
            # Also ensures our synthetic message (date_ts=now_ts) is included (query is date_ts < before_date_ts).
            trigger_date_ts_override=now_ts + 1,
        )
        await self._handle_router_out(update, context, out2)

    async def on_free_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        self._store_incoming(update)
        chat = update.effective_chat
        msg = update.effective_message
        if not chat or not msg:
            return

        # If replying to a clarification prompt, treat as clarification answer
        if msg.reply_to_message:
            row = self.db.get_pending_flow_by_clarification_message(chat.id, msg.reply_to_message.message_id)
            if row:
                flow_id = str(row["flow_id"])
                answers = json.loads(row["answers_json"])
                answers["free_text"] = self._get_text(msg)
                self.db.update_pending_flow(chat_id=chat.id, flow_id=flow_id, answers=answers)
                out2 = await self._run_llm(
                    update,
                    context,
                    trigger_type="clarification",
                    intent="clarify",
                    text_override=f"Clarification answers: {answers}",
                )
                await self._handle_router_out(update, context, out2)
                return

        should, trig = self._should_route_free_text(update, context)
        if not should:
            return

        out2 = await self._run_llm(update, context, trigger_type=trig, intent="free_text")
        await self._handle_router_out(update, context, out2)
    async def _resolve_symbol_interactive(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        user_text: str,
        purpose: str,
    ) -> str | None:
        """
        Resolve ticker input to canonical symbol (AAPL or BTCUSD).
        If ambiguous, ask user via inline keyboard and store pending action in chat_data.
        """
        input_sym = normalize_user_symbol(user_text)
        if not input_sym:
            return None

        cands = candidates_for_input(input_sym)
        # Validate candidates via quote-short (fast)
        ok: list[str] = []
        for s in cands:
            try:
                q = await self.fmp.quote_short(s)
            except Exception:
                q = None
            if q:
                ok.append(s)

        if not ok:
            await update.effective_message.reply_text("I couldn't find that symbol. Try again.")
            return None

        if len(ok) == 1:
            return ok[0]

        # Ambiguous: ask user
        context.chat_data["pending_symbol_choice"] = {"purpose": purpose, "options": ok}
        kb = [
            [InlineKeyboardButton(f"{s} ({asset_type_from_symbol(s)})", callback_data=f"pick:{s}")]
            for s in ok
        ]
        await update.effective_message.reply_text(
            "That ticker is ambiguous. Please choose:",
            reply_markup=InlineKeyboardMarkup(kb),
        )
        return None

    async def _maybe_handle_pick_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        """
        Handle callback for symbol disambiguation. Returns True if handled.
        """
        q = update.callback_query
        if not q or not q.data or not q.data.startswith("pick:"):
            return False
        await q.answer()
        symbol = q.data.split("pick:", 1)[1].strip()
        pending = context.chat_data.pop("pending_symbol_choice", None) or {}
        purpose = (pending.get("purpose") or "").strip()
        if not purpose:
            await q.edit_message_text("Selection expired. Please retry the command.")
            return True

        # Re-dispatch based on purpose
        if purpose == "get_price":
            await q.edit_message_text("Fetching price…")
            await self._send_price(update, context, symbol, edit_message=True)
        elif purpose == "add":
            await q.edit_message_text("Adding…")
            await self._add_symbol(update, context, symbol, edit_message=True)
        elif purpose == "remove":
            await q.edit_message_text("Removing…")
            await self._remove_symbol(update, context, symbol, edit_message=True)
        else:
            await q.edit_message_text("Selection handled.")
        return True

    async def on_symbol_pick(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        self._store_incoming(update)
        # Dedicated callback handler for disambiguation picks
        await self._maybe_handle_pick_callback(update, context)

    # ---- Commands ----
    async def cmd_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        await update.effective_message.reply_text("Cancelled.")
        return ConversationHandler.END

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        chat = update.effective_chat
        if not chat:
            return ConversationHandler.END
        self.db.upsert_chat(chat.id, chat.type, _chat_title(update))
        row = self.db.get_chat(chat.id)
        assert row is not None

        # Ask timezone if still default AND first time (simple heuristic).
        if row.timezone == "America/New_York" and (row.market_open_sent_date is None and row.market_close_sent_date is None):
            kb = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("New York (ET)", callback_data="tz:America/New_York")],
                    [InlineKeyboardButton("Los Angeles (PT)", callback_data="tz:America/Los_Angeles")],
                    [InlineKeyboardButton("UTC", callback_data="tz:UTC")],
                    [InlineKeyboardButton("Other (type it)", callback_data="tz:OTHER")],
                ]
            )
            await update.effective_message.reply_text(
                "Welcome! Please choose your timezone (used for alerts and market messages):",
                reply_markup=kb,
            )
            return ASK_TZ

        await update.effective_message.reply_text(
            "Registered.\n\nCommands:\n"
            "/get_price [TICKER]\n"
            "/add_to_watchlist [TICKER]\n"
            "/remove_from_watchlist [TICKER]\n"
            "/show_the_watchlist\n"
            "/get_price_from_watchlist\n",
        )
        return ConversationHandler.END

    async def cmd_set_timezone(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("New York (ET)", callback_data="tz:America/New_York")],
                [InlineKeyboardButton("Los Angeles (PT)", callback_data="tz:America/Los_Angeles")],
                [InlineKeyboardButton("UTC", callback_data="tz:UTC")],
                [InlineKeyboardButton("Other (type it)", callback_data="tz:OTHER")],
            ]
        )
        await update.effective_message.reply_text(
            "Choose your timezone (or type it):",
            reply_markup=kb,
        )
        return ASK_TZ

    async def on_tz_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        q = update.callback_query
        if not q:
            return ConversationHandler.END
        await q.answer()
        value = (q.data or "").split("tz:", 1)[-1]
        if value == "OTHER":
            await q.edit_message_text("Please type your timezone (e.g., Europe/London):")
            return ASK_TZ
        if not self._validate_tz(value):
            await q.edit_message_text("Invalid timezone. Please type a valid IANA timezone (e.g., Europe/London):")
            return ASK_TZ
        self.db.set_timezone(q.message.chat_id, value)
        await q.edit_message_text(self._setup_complete_text(value), parse_mode=ParseMode.HTML)
        return ConversationHandler.END

    async def on_tz_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        text = (update.effective_message.text or "").strip()
        if not self._validate_tz(text):
            await update.effective_message.reply_text("Invalid timezone. Example: Europe/London")
            return ASK_TZ
        chat = update.effective_chat
        if chat:
            self.db.set_timezone(chat.id, text)
        await update.effective_message.reply_text(self._setup_complete_text(text), parse_mode=ParseMode.HTML)
        return ConversationHandler.END

    def _validate_tz(self, tz: str) -> bool:
        try:
            ZoneInfo(tz)
            return True
        except Exception:
            # fallback validation list (works with tzdata)
            return tz in available_timezones()

    def _setup_complete_text(self, tz: str) -> str:
        return (
            f"<b>Setup complete</b>\n"
            f"Timezone: <code>{tz}</code>\n\n"
            f"<b>Try these commands</b>\n"
            f"- /get_price AAPL\n"
            f"- /get_price BTC (will ask stock vs BTCUSD)\n"
            f"- /add_to_watchlist AAPL\n"
            f"- /add_to_watchlist BTCUSD\n"
            f"- /show_the_watchlist\n"
            f"- /get_price_from_watchlist\n\n"
            f"Change timezone anytime: /set_timezone"
        )

    async def cmd_get_price(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        self._store_incoming(update)
        if await self._maybe_handle_pick_callback(update, context):
            return ConversationHandler.END

        args = context.args or []
        if not args:
            # If user replied to a message, try extracting ticker from replied text.
            if update.effective_message.reply_to_message:
                reply_text = self._get_text(update.effective_message.reply_to_message)
                if looks_like_ticker(reply_text):
                    symbol = await self._resolve_symbol_interactive(update, context, reply_text, purpose="get_price")
                    if symbol:
                        await self._send_price(update, context, symbol, edit_message=False)
                    return ConversationHandler.END
                # Not a ticker: route via LLM
                out = await self._run_llm(
                    update,
                    context,
                    trigger_type="command_reply",
                    intent="get_price",
                    text_override=f"User requested /get_price for replied message: {reply_text}",
                )
                await self._handle_router_out(update, context, out)
                return ConversationHandler.END

            context.chat_data["pending_ticker_action"] = {"purpose": "get_price"}
            await update.effective_message.reply_text("Which ticker?", reply_markup=ForceReply(selective=True))
            return ASK_TICKER
        symbol = await self._resolve_symbol_interactive(update, context, args[0], purpose="get_price")
        if symbol:
            await self._send_price(update, context, symbol, edit_message=False)
        return ConversationHandler.END

    async def cmd_add_to_watchlist(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        self._store_incoming(update)
        if await self._maybe_handle_pick_callback(update, context):
            return ConversationHandler.END
        args = context.args or []
        if not args:
            if update.effective_message.reply_to_message:
                reply_text = self._get_text(update.effective_message.reply_to_message)
                if looks_like_ticker(reply_text):
                    symbol = await self._resolve_symbol_interactive(update, context, reply_text, purpose="add")
                    if symbol:
                        await self._add_symbol(update, context, symbol, edit_message=False)
                    return ConversationHandler.END
                out = await self._run_llm(
                    update,
                    context,
                    trigger_type="command_reply",
                    intent="add_to_watchlist",
                    text_override=f"User requested /add_to_watchlist for replied message: {reply_text}",
                )
                await self._handle_router_out(update, context, out)
                return ConversationHandler.END

            context.chat_data["pending_ticker_action"] = {"purpose": "add"}
            await update.effective_message.reply_text("Add which ticker?", reply_markup=ForceReply(selective=True))
            return ASK_TICKER
        symbol = await self._resolve_symbol_interactive(update, context, args[0], purpose="add")
        if symbol:
            await self._add_symbol(update, context, symbol, edit_message=False)
        return ConversationHandler.END

    async def cmd_remove_from_watchlist(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        self._store_incoming(update)
        if await self._maybe_handle_pick_callback(update, context):
            return ConversationHandler.END
        args = context.args or []
        if not args:
            if update.effective_message.reply_to_message:
                reply_text = self._get_text(update.effective_message.reply_to_message)
                if looks_like_ticker(reply_text):
                    symbol = await self._resolve_symbol_interactive(update, context, reply_text, purpose="remove")
                    if symbol:
                        await self._remove_symbol(update, context, symbol, edit_message=False)
                    return ConversationHandler.END
                out = await self._run_llm(
                    update,
                    context,
                    trigger_type="command_reply",
                    intent="remove_from_watchlist",
                    text_override=f"User requested /remove_from_watchlist for replied message: {reply_text}",
                )
                await self._handle_router_out(update, context, out)
                return ConversationHandler.END

            context.chat_data["pending_ticker_action"] = {"purpose": "remove"}
            await update.effective_message.reply_text("Remove which ticker?", reply_markup=ForceReply(selective=True))
            return ASK_TICKER
        symbol = await self._resolve_symbol_interactive(update, context, args[0], purpose="remove")
        if symbol:
            await self._remove_symbol(update, context, symbol, edit_message=False)
        return ConversationHandler.END

    async def on_ticker_reply(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        self._store_incoming(update)
        text = (update.effective_message.text or "").strip()
        pending = context.chat_data.pop("pending_ticker_action", None) or {}
        purpose = pending.get("purpose")
        if purpose not in ("get_price", "add", "remove"):
            await update.effective_message.reply_text("Please retry the command.")
            return ConversationHandler.END
        symbol = await self._resolve_symbol_interactive(update, context, text, purpose=purpose)
        if not symbol:
            return ConversationHandler.END
        if purpose == "get_price":
            await self._send_price(update, context, symbol, edit_message=False)
        elif purpose == "add":
            await self._add_symbol(update, context, symbol, edit_message=False)
        elif purpose == "remove":
            await self._remove_symbol(update, context, symbol, edit_message=False)
        return ConversationHandler.END

    async def cmd_show_watchlist(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        self._store_incoming(update)
        chat = update.effective_chat
        if not chat:
            return
        self.db.upsert_chat(chat.id, chat.type, _chat_title(update))
        items = self.db.list_watchlist(chat.id)
        if not items:
            await update.effective_message.reply_text("Watchlist is empty.")
            return
        lines = ["<b>Watchlist</b>"]
        for sym, _atype in items:
            q = await self.fmp.quote(sym) or await self.fmp.quote_short(sym)
            if not q:
                lines.append(f"- {fmt_symbol_html(sym)}: n/a")
                continue
            price = q.get("price")
            chg = q.get("change")
            lines.append(f"- {fmt_symbol_html(sym)}: {price} ({chg})")
        await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    async def cmd_price_from_watchlist(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        self._store_incoming(update)
        chat = update.effective_chat
        if not chat:
            return
        items = self.db.list_watchlist(chat.id)
        if not items:
            await update.effective_message.reply_text("Watchlist is empty.")
            return
        kb = [[InlineKeyboardButton(sym, callback_data=f"wl:{sym}")] for (sym, _t) in items]
        await update.effective_message.reply_text(
            "Select a ticker:",
            reply_markup=InlineKeyboardMarkup(kb),
        )

    async def cmd_next_market_open_time(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Show the next US market open time (09:30 New York time), rendered in the chat timezone.
        Uses FMP holidays-by-exchange to skip closed days.
        """
        self._store_incoming(update)
        chat = update.effective_chat
        if not chat:
            return
        self.db.upsert_chat(chat.id, chat.type, _chat_title(update))
        chat_row = self.db.get_chat(chat.id)
        tz_name = chat_row.timezone if chat_row else "America/New_York"

        now_ny = datetime.now(tz=NY_TZ)
        today = now_ny.date()

        # Refresh holiday cache (today..today+30) and compute next open day.
        start = today.isoformat()
        end = (today + timedelta(days=30)).isoformat()
        try:
            holidays = await self.fmp.holidays_by_exchange(self._exchange, start, end)
        except Exception:
            holidays = []
        closed = {h.get("date") for h in holidays if h.get("isClosed") is True and h.get("date")}

        def is_open_day(d: date) -> bool:
            if is_weekend(d):
                return False
            return d.isoformat() not in closed

        open_dt_today = datetime.combine(today, time(9, 30), tzinfo=NY_TZ)
        if is_open_day(today) and now_ny < open_dt_today:
            next_open_day = today
        else:
            d = today + timedelta(days=1)
            while not is_open_day(d) and d <= today + timedelta(days=30):
                d += timedelta(days=1)
            next_open_day = d

        next_open_ny = datetime.combine(next_open_day, time(9, 30), tzinfo=NY_TZ)
        chat_tz = ZoneInfo(tz_name)
        next_open_chat = next_open_ny.astimezone(chat_tz)

        phrase = next_open_phrase(today, next_open_day)
        msg = (
            f"<b>Next market open</b>: {phrase}\n"
            f"- Your time (<code>{tz_name}</code>): <b>{next_open_chat.strftime('%Y-%m-%d %H:%M')}</b>\n"
            f"- New York time (<code>America/New_York</code>): <b>{next_open_ny.strftime('%Y-%m-%d %H:%M')}</b>\n"
            f"- Exchange: <code>{self._exchange}</code>"
        )
        await update.effective_message.reply_text(msg, parse_mode=ParseMode.HTML)

    async def on_watchlist_pick(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        self._store_incoming(update)
        q = update.callback_query
        if not q:
            return
        await q.answer()
        sym = (q.data or "").split("wl:", 1)[-1].strip()
        await q.edit_message_text("Fetching…")
        await self._send_price(update, context, sym, edit_message=True)

    # ---- Actions ----
    async def _send_price(self, update: Update, context: ContextTypes.DEFAULT_TYPE, symbol: str, *, edit_message: bool) -> None:
        quote = await self.fmp.quote(symbol)
        if not quote:
            msg = f"Not found: {symbol}"
            await self._reply_or_edit(update, msg, edit_message=edit_message)
            return
        msg = quote_card_html(symbol, quote)
        await self._reply_or_edit(update, msg, edit_message=edit_message, parse_mode=ParseMode.HTML)

    async def _add_symbol(self, update: Update, context: ContextTypes.DEFAULT_TYPE, symbol: str, *, edit_message: bool) -> None:
        chat = update.effective_chat
        if not chat:
            return
        self.db.upsert_chat(chat.id, chat.type, _chat_title(update))
        # validate quickly
        q = await self.fmp.quote_short(symbol)
        if not q:
            await self._reply_or_edit(update, f"Not found: {symbol}", edit_message=edit_message)
            return
        inserted = self.db.add_to_watchlist(chat.id, symbol, asset_type_from_symbol(symbol))
        if inserted:
            await self._reply_or_edit(update, f"Added {fmt_symbol_html(symbol)} to watchlist.", edit_message=edit_message, parse_mode=ParseMode.HTML)
        else:
            await self._reply_or_edit(update, f"{fmt_symbol_html(symbol)} is already in the watchlist.", edit_message=edit_message, parse_mode=ParseMode.HTML)

    async def _remove_symbol(self, update: Update, context: ContextTypes.DEFAULT_TYPE, symbol: str, *, edit_message: bool) -> None:
        chat = update.effective_chat
        if not chat:
            return
        removed = self.db.remove_from_watchlist(chat.id, symbol)
        if removed:
            await self._reply_or_edit(update, f"Removed {fmt_symbol_html(symbol)}.", edit_message=edit_message, parse_mode=ParseMode.HTML)
        else:
            await self._reply_or_edit(update, f"{fmt_symbol_html(symbol)} was not in the watchlist.", edit_message=edit_message, parse_mode=ParseMode.HTML)

    async def _reply_or_edit(self, update: Update, text: str, *, edit_message: bool, parse_mode: ParseMode | None = None) -> None:
        chat = update.effective_chat
        if not chat:
            return
        if edit_message and update.callback_query:
            await update.callback_query.edit_message_text(text, parse_mode=parse_mode)
            # Store the edited content as the bot message (replace by message_id).
            try:
                msg = update.callback_query.message
                self.db.add_chat_message(
                    chat_id=chat.id,
                    message_id=msg.message_id,
                    date_ts=int(datetime.utcnow().timestamp()),
                    from_user_id=None,
                    from_username=None,
                    from_is_bot=True,
                    text=text,
                    reply_to_message_id=(msg.reply_to_message.message_id if msg.reply_to_message else None),
                    entities=None,
                    keep_last=100,
                )
            except Exception:
                pass
        else:
            sent = await update.effective_message.reply_text(text, parse_mode=parse_mode)
            try:
                self.db.add_chat_message(
                    chat_id=sent.chat_id,
                    message_id=sent.message_id,
                    date_ts=int(sent.date.timestamp()),
                    from_user_id=None,
                    from_username=None,
                    from_is_bot=True,
                    text=self._get_text(sent),
                    reply_to_message_id=(sent.reply_to_message.message_id if sent.reply_to_message else None),
                    entities=None,
                    keep_last=100,
                )
            except Exception:
                pass

    # ---- Jobs ----
    async def job_alerts(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        thresholds = [5, 10, 20, 30, 50, 70, 100]
        chats = self.db.list_chats()
        for chat in chats:
            items = self.db.list_watchlist(chat.chat_id)
            if not items:
                continue
            tz = ZoneInfo(chat.timezone)
            day_key = datetime.now(tz=tz).date().isoformat()
            for symbol, asset_type in items:
                q = await self.fmp.quote(symbol) or await self.fmp.quote_short(symbol)
                if not q:
                    continue
                price = q.get("price")
                change = q.get("change")

                try:
                    p = float(price)
                except Exception:
                    continue

                state = self.db.get_daily_state(chat.chat_id, symbol, day_key)
                base = None
                last_thr = None
                if state:
                    base = state["base_price"]
                    last_thr = state["last_alert_threshold"]
                if base is None:
                    # For stocks, try previousClose; otherwise fallback to inferred prev (p - change)
                    base_candidate = q.get("previousClose")
                    if base_candidate is None:
                        try:
                            base_candidate = p - float(change)
                        except Exception:
                            base_candidate = p
                    base = float(base_candidate) if base_candidate is not None else p
                    self.db.upsert_daily_state(chat.chat_id, symbol, day_key, base_price=base, last_price=p)
                else:
                    self.db.upsert_daily_state(chat.chat_id, symbol, day_key, last_price=p)

                if base == 0:
                    continue
                pct = ((p - float(base)) / float(base)) * 100.0
                crossed = [t for t in thresholds if abs(pct) >= t]
                if not crossed:
                    continue
                new_thr = max(crossed)
                if last_thr is not None and int(last_thr) >= new_thr:
                    continue

                direction = "up" if pct >= 0 else "down"
                msg = f"Alert: {fmt_symbol_html(symbol)} is {direction} {pct:+.2f}% today (base {base:.4f} → now {p:.4f})."
                try:
                    sent = await context.bot.send_message(chat_id=chat.chat_id, text=msg, parse_mode=ParseMode.HTML)
                    try:
                        self.db.add_chat_message(
                            chat_id=sent.chat_id,
                            message_id=sent.message_id,
                            date_ts=int(sent.date.timestamp()),
                            from_user_id=None,
                            from_username=None,
                            from_is_bot=True,
                            text=self._get_text(sent),
                            reply_to_message_id=(sent.reply_to_message.message_id if sent.reply_to_message else None),
                            entities=None,
                            keep_last=100,
                        )
                    except Exception:
                        pass
                    self.db.upsert_daily_state(chat.chat_id, symbol, day_key, last_alert_threshold=new_thr)
                except Exception as e:
                    self.logger.warning(f"Failed to send alert to {chat.chat_id}: {e}")

    async def job_clock_tick(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        clock = now_in_ny()
        today = clock.ny_date
        if is_weekend(today):
            return

        if await self._is_exchange_closed_today(today):
            return

        # Market open ~09:30 NY
        if near_time(clock.now_ny, 9, 30, window_seconds=self.clock_tick_seconds):
            await self._send_market_open(context, today)

        # Market close ~16:00 NY
        if near_time(clock.now_ny, 16, 0, window_seconds=self.clock_tick_seconds):
            await self._send_market_close(context, today)

    async def _is_exchange_closed_today(self, today: date) -> bool:
        day_str = today.isoformat()
        if self._holiday_cache_day != day_str:
            # refresh cache for today..today+14; used for close + to avoid open on holiday
            start = day_str
            end = (today + timedelta(days=14)).isoformat()
            try:
                holidays = await self.fmp.holidays_by_exchange(self._exchange, start, end)
            except Exception as e:
                self.logger.warning(f"Failed to fetch holidays-by-exchange: {e}")
                holidays = []
            self._holiday_closed_dates = {h.get("date") for h in holidays if h.get("isClosed") is True and h.get("date")}
            self._holiday_cache_day = day_str
        return day_str in self._holiday_closed_dates

    async def _send_market_open(self, context: ContextTypes.DEFAULT_TYPE, today: date) -> None:
        chats = self.db.list_chats()
        for chat in chats:
            if chat.market_open_sent_date == today.isoformat():
                continue
            items = self.db.list_watchlist(chat.chat_id)
            if not items:
                self.db.set_market_open_sent(chat.chat_id, today.isoformat())
                continue
            lines = [f"<b>Market Open</b> — {today.isoformat()} (NY)"]
            for symbol, _t in items:
                q = await self.fmp.quote(symbol)
                if not q:
                    continue
                price = q.get("price")
                op = q.get("open")
                prev = q.get("previousClose")
                move = None
                try:
                    if op is not None and prev is not None:
                        move = ((float(op) - float(prev)) / float(prev)) * 100.0 if float(prev) != 0 else None
                except Exception:
                    move = None
                move_txt = f"{move:+.2f}%" if move is not None else "n/a"
                lines.append(f"- {fmt_symbol_html(symbol)} open: {op} (overnight: {move_txt}) now: {price}")
            try:
                sent = await context.bot.send_message(chat_id=chat.chat_id, text="\n".join(lines), parse_mode=ParseMode.HTML)
                try:
                    self.db.add_chat_message(
                        chat_id=sent.chat_id,
                        message_id=sent.message_id,
                        date_ts=int(sent.date.timestamp()),
                        from_user_id=None,
                        from_username=None,
                        from_is_bot=True,
                        text=self._get_text(sent),
                        reply_to_message_id=(sent.reply_to_message.message_id if sent.reply_to_message else None),
                        entities=None,
                        keep_last=100,
                    )
                except Exception:
                    pass
            except Exception as e:
                self.logger.warning(f"Failed to send open msg to {chat.chat_id}: {e}")
            self.db.set_market_open_sent(chat.chat_id, today.isoformat())

    async def _send_market_close(self, context: ContextTypes.DEFAULT_TYPE, today: date) -> None:
        # Ensure holiday cache is current (today..today+14)
        await self._is_exchange_closed_today(today)
        closed = set(self._holiday_closed_dates)

        def is_open_day(d: date) -> bool:
            if is_weekend(d):
                return False
            return d.isoformat() not in closed

        d = today + timedelta(days=1)
        while not is_open_day(d) and d <= today + timedelta(days=30):
            d += timedelta(days=1)
        next_open = d
        phrase = next_open_phrase(today, next_open)

        chats = self.db.list_chats()
        for chat in chats:
            if chat.market_close_sent_date == today.isoformat():
                continue
            items = self.db.list_watchlist(chat.chat_id)
            lines = [f"<b>Market Close</b> — {today.isoformat()} (NY)"]
            if items:
                for symbol, _t in items:
                    q = await self.fmp.quote(symbol)
                    if not q:
                        continue
                    price = q.get("price")
                    chg = q.get("change")
                    pct = q.get("changesPercentage") or q.get("changePercent")
                    lines.append(f"- {fmt_symbol_html(symbol)} close: {price} ({chg}, {pct}%)")
            lines.append(f"Next market open: {phrase} ({next_open.isoformat()})")
            try:
                sent = await context.bot.send_message(chat_id=chat.chat_id, text="\n".join(lines), parse_mode=ParseMode.HTML)
                try:
                    self.db.add_chat_message(
                        chat_id=sent.chat_id,
                        message_id=sent.message_id,
                        date_ts=int(sent.date.timestamp()),
                        from_user_id=None,
                        from_username=None,
                        from_is_bot=True,
                        text=self._get_text(sent),
                        reply_to_message_id=(sent.reply_to_message.message_id if sent.reply_to_message else None),
                        entities=None,
                        keep_last=100,
                    )
                except Exception:
                    pass
            except Exception as e:
                self.logger.warning(f"Failed to send close msg to {chat.chat_id}: {e}")
            self.db.set_market_close_sent(chat.chat_id, today.isoformat())


