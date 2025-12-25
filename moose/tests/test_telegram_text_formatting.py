import pytest


def test_split_telegram_html_respects_limit():
    from moose.agents.telegram_stock_bot.text_formatting import split_telegram_html, TELEGRAM_SAFE_CHAR_LIMIT

    text = "a" * (TELEGRAM_SAFE_CHAR_LIMIT * 3 + 123)
    chunks = split_telegram_html(text, limit=TELEGRAM_SAFE_CHAR_LIMIT)
    assert len(chunks) >= 3
    assert all(isinstance(c, str) and c for c in chunks)
    assert all(len(c) <= TELEGRAM_SAFE_CHAR_LIMIT for c in chunks)


def test_split_telegram_html_prefers_newlines():
    from moose.agents.telegram_stock_bot.text_formatting import split_telegram_html

    text = ("para\n" * 1000) + "\n\n" + ("more\n" * 1000)
    chunks = split_telegram_html(text, limit=400)
    assert len(chunks) > 1
    assert all(len(c) <= 400 for c in chunks)


def test_format_finance_office_reply_escapes_result():
    from moose.agents.telegram_stock_bot.text_formatting import format_finance_office_reply

    resp = {
        "status": "success",
        "result": {
            "ok": True,
            "error": None,
            "result": {
                "by_ticker": {
                    "AAPL": {
                        "objective": "Analyze Apple",
                        "approach": "Step1\nStep2",
                        "analysis_results": "Hello <b>world</b>\nLine2",
                    }
                },
                "tickers": ["AAPL"],
            },
        },
        "llm_usage_total": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
        "llm_cost_total": 0.00123,
    }
    msg = format_finance_office_reply(instruction="Analyze ACME", resp=resp)
    assert "<b>🎯 Objective</b>" in msg
    assert "<b>📌 Results</b>" in msg
    # Must be escaped so it doesn't render as HTML from external content
    assert "&lt;b&gt;world&lt;/b&gt;" in msg
    assert "<b>🏷️ AAPL</b>" in msg
    assert "Objective" in msg
    assert "Approach" in msg
    assert "Analysis" in msg
    assert "Tokens:" in msg
    assert "Cost:" in msg


def test_format_finance_office_reply_error_path():
    from moose.agents.telegram_stock_bot.text_formatting import format_finance_office_reply

    resp = {"status": "error", "error": "boom <tag>"}
    msg = format_finance_office_reply(instruction="Do X", resp=resp)
    assert "FinanceOffice error" in msg
    assert "&lt;tag&gt;" in msg


