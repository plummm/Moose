## telegram_stock_bot

Telegram stock/crypto bot agent built on `python-telegram-bot` and Financial Modeling Prep (FMP).

### Persistence (SQLite “in another container”)

This agent stores its SQLite DB at `/data/db.sqlite`. To keep data even if the bot agent container/image is deleted, create a **named Docker volume** and a small **DB holder** container once:

```bash
docker volume create telegram_stock_bot_data

docker run -d --name telegram_stock_bot_db \
  --restart unless-stopped \
  -v telegram_stock_bot_data:/data \
  alpine:3.20 \
  sleep infinity
```

The agent mounts the same volume at `/data` (configured in `agent_config.json`).

### Secrets / environment variables

Set these in the host environment before running Moose:

- `TELEGRAM_BOT_TOKEN` (required)
- `FMP_API_KEY` (required)

Moose will pass these through into the agent container at startup.


