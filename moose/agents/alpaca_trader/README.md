# alpaca_trader

`alpaca_trader` is a Moose agent for **paper trading** that ingests text events, scores/regimes them, generates trade plans via an LLM controller orchestrating specialist tools, applies deterministic risk gates, and (optionally) executes via **Alpaca MCP** sidecars over **streamable HTTP**.

Alpaca MCP server (official): [`alpacahq/alpaca-mcp-server`](https://github.com/alpacahq/alpaca-mcp-server)

---

## What it does

### Event-driven pipeline
1. `POST /events` accepts a text event
2. Persisted TTL dedupe (SQLite)
3. Symbol extraction (stocks + `*USD` crypto symbols like `BTCUSD`)
4. Update:
   - `regime_score` (global, -100..100)
   - `watch_score` (per-symbol, 0..100)
5. Enqueue research tasks
6. Plan controller builds a `TradePlan` + explicit `OrderSpec` (LLM orchestrates specialist tools)
7. Risk gate approves/rejects
8. If approved + enabled, execute via Alpaca MCP tools (paper)

### Multi-account support
- Configure multiple accounts in `custom.accounts`.
- Each account points to a single Alpaca MCP sidecar via `mcp_url`.
- Execution selects tools by `plan.account_id`.

### Stock market-hours gating
- Stocks are restricted to market open hours.
- Uses FMP `holidays-by-exchange` to skip closed days (same pattern as telegram bot).

### Persistent state (SQLite)
Default location is under your project directory (or override with `custom.storage.db_path`):
- dedupe keys, events, scores, tasks
- trade plans + audit trail
- orders + execution output
- reconciliation snapshots + daily summaries
- notifications (stub sink; Telegram integration later)

---

## Running

### Moose debug mode
Run:
- `moose agent debug --name alpaca_trader --debug`

### Docker / standalone mode
The agent folder can also be run as a standalone Python program when the agent directory is on `PYTHONPATH` (typical in Docker agent containers).

---

## Endpoints

### Public (no auth)
- `POST /events`
  - body: `{ "text": "...", "source": "optional", "timestamp": 1234567890 }`
- `GET /status`
- `GET /queue_stats`

### Auth required
- `POST /execute`
  - purpose: send a custom command to the **execution LLM** with Alpaca MCP tools enabled
  - body: `{ "text": "...", "account_id": "paper_moose1" }`
  - header: `X-auth-password: <password>`

Auth is configured by environment variable:
- `ALPACA_TRADER_AUTH_PASSWORD=...`

---

## Configuration (`agent_config.json`)

### Accounts (Alpaca MCP sidecars)
One container/sidecar per account:

```json
"custom": {
  "accounts": {
    "paper_moose1": {
      "mcp_url": "http://alpaca-mcp-paper_moose1:8000/mcp"
    }
  }
}
```

### Allowlists
```json
"custom": {
  "allowlists": {
    "allow_all": false,
    "stocks": ["AAPL", "MSFT"],
    "crypto": ["BTCUSD", "ETHUSD"]
  }
}
```

- `allow_all: true` bypasses allowlists (still subject to other guardrails like market hours and caps).

### Execution model override
Some providers (notably Gemini) can reject certain tool JSON schemas during function-calling. You can set a dedicated execution model:

```json
"custom": {
  "execution": {
    "auto_execute_approved": true,
    "llm_model": "claude-sonnet-4-5-20250929",
    "reconcile_interval_s": 120
  }
}
```

### Market hours / calendar
Set `FMP_API_KEY` in the environment.

---

## Quick start (local)

1. Start Alpaca MCP sidecar(s) for paper trading.
2. Configure `custom.accounts.*.mcp_url` to point to those sidecars.
3. Set env vars:
   - `ALPACA_TRADER_AUTH_PASSWORD=...`
   - `FMP_API_KEY=...`
4. Start agent:
   - `moose agent debug --name alpaca_trader --debug`
5. Send event:
   - `curl -X POST http://localhost:3505/events -H 'Content-Type: application/json' -d '{"text":"Breaking: AAPL strong earnings"}'`
6. Send authenticated command:
   - `curl -X POST http://localhost:3505/execute -H 'Content-Type: application/json' -H 'X-auth-password: ...' -d '{"text":"Get the latest quote of AAPL","account_id":"paper_moose1"}'`

---

## Known limitations / next improvements
- Execution still relies on LLM tool-calling (not yet a curated deterministic execution API).
- No live trading approval workflow (paper-only expected).
- Telegram `push_trading_activity` not implemented yet (DB-backed stub only).

---

## Roadmap (next)

### Research quality
- Integrate Finance Office as the canonical “research packet” generator for tasks.
- Cache + reuse research snapshots across strategies.

### Strategy/account routing
- Add config `custom.strategy_accounts` so specific strategies map to specific accounts (and thus specific MCP sidecars).
- Allow fallback lists (e.g., rotate across accounts) and per-account enable/disable per strategy.

### Safer execution layer
- Replace “LLM chooses tool names” with a curated execution interface (deterministic wrapper calls):
  - quote, positions, orders, submit/cancel/replace, order status, etc.
- Keep LLM for decision-making; keep execution code constrained.

### Portfolio-aware risk
- Tighten exposure math using reconciled positions/orders:
  - per-day notional, per-symbol exposure, max open orders, max drawdown, etc.
- Per-strategy risk profiles (caps and cooldowns).

### Live trading workflow
- `mode=live` with mandatory approval (Telegram or `/approve` endpoint).
- Kill switch + “exit-only” mode.

### Ops + observability
- Status endpoints/UI for recent events/plans/orders/fills/positions.
- Integration tests with a mocked MCP server.

---

## Improvement backlog
- **Correctness**: strict schema validation for orders (qty/notional, symbol normalization, idempotency retries).
- **Safety**: verify sidecar paper/live mode before order submission; better per-strategy permissions.
- **Execution**: partial fill tracking, cancel/replace workflows, stale order cleanup.
- **Scheduling**: better priority scheduling (watch_score + cooldowns + regime_score).
- **Notifications**: implement `telegram_stock_bot.push_trading_activity` client.
- **Testing**: MCP integration tests + endpoint auth tests.


