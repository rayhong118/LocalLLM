# ⚙️ Environment Variables Reference (`.env`)

This document describes all environment variables supported by **LocalLLM**. 

> **Security Note:** The actual `.env` file contains sensitive credentials and API tokens. It is excluded from Git via `.gitignore`. To set up your local environment, copy `.env.example` to `.env` and fill in your values.

```powershell
Copy-Item .env.example .env
```

---

## 📋 Quick Reference

| Variable | Category | Required? | Default / Example | Description |
| :--- | :--- | :---: | :--- | :--- |
| `LLM_BASE_URL` | LLM | Optional | `http://localhost:1234/v1` | Base URL for OpenAI-compatible local/remote LLM endpoint |
| `LLM_MODEL` | LLM | Optional | `google/gemma-4-12b-qat` | Model identifier loaded in LM Studio or inference server |
| `LLM_API_KEY` | LLM | Optional | `lm-studio` | API key (use `lm-studio` for local LM Studio) |
| `LLM_PROVIDER` | LLM | Optional | `lm_studio` | Provider type identifier |
| `CONTEXT_WINDOW` | LLM | Optional | `32768` | Maximum token context length |
| `LLM_TIMEOUT` | LLM | Optional | `1200` | Request timeout in seconds for agent planning/execution |
| `TEMPERATURE` | LLM | Optional | `0` | Sampling temperature (`0` for deterministic outputs) |
| `TURSO_DATABASE_URL` | Database | **Recommended** | `libsql://your-db-org.turso.io` | Turso cloud database connection URL |
| `TURSO_AUTH_TOKEN` | Database | **Recommended** | `eyJhbGci...` | Turso authentication JWT token |
| `TURSO_SYNC_URL` | Database | Optional | `libsql://your-db-org.turso.io` | Remote sync URL for embedded replica mode |
| `TURSO_LOCAL_REPLICA_PATH`| Database | Optional | `tasks_replica.db` | Local replica SQLite path when using sync mode |
| `TELEGRAM_BOT_TOKEN` | Notifications | Optional | `123456789:ABC...` | Telegram bot token from [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_CHAT_ID` | Notifications | Optional | `987654321` | Telegram destination chat ID |
| `TELEGRAM_NOTIFY_ALL` | Notifications | Optional | `true` | Send notifications for all completed tasks (or failures only) |
| `FINNHUB_API_KEY` | Site Skills | Optional | `db95a...` | API key for Finnhub stock news & market sentiment |
| `WSDOT_ACCESS_CODE` | Site Skills | Optional | `0e803...` | Access code for Washington State DOT highway alerts API |
| `CHROME_PATH` | Browser | Optional | `C:\Program Files\Google\Chrome\Application\chrome.exe` | Path to Google Chrome executable |
| `HEADLESS` | Browser | Optional | `false` | Run browser in headless mode (`false` recommended for anti-bot) |

---

## 🔍 Detailed Field Breakdown

### 1. LLM & Inference Engine

- **`LLM_BASE_URL`** *(string)*
  - The HTTP endpoint pointing to your local LLM server (LM Studio, vLLM, Ollama, or OpenAI-compatible server).
  - Defaults to `http://localhost:1234/v1`.
  - Also accepts legacy fallback alias `LM_STUDIO_URL`.

- **`LLM_MODEL`** *(string)*
  - The model name or path loaded into the inference engine.
  - Used by `run.ps1` to detect if the target model is loaded, and by backend agent sessions.
  - Examples: `google/gemma-4-e2b`, `google/gemma-4-12b-qat`, `qwen2.5-7b-instruct`.

- **`LLM_API_KEY`** *(string)*
  - API key sent in HTTP `Authorization: Bearer <token>` headers.
  - For local LM Studio, any non-empty string works (default: `lm-studio`).

- **`CONTEXT_WINDOW`** *(integer)*
  - Context window size in tokens allocated for prompts and memory.
  - Default: `32768`.

- **`LLM_TIMEOUT`** *(integer)*
  - Timeout in seconds for individual LLM inference completions.
  - Default: `1200` (20 minutes, suitable for slow local quantized models).

- **`TEMPERATURE`** *(float)*
  - Model temperature parameter between `0.0` and `1.0`.
  - Default: `0` (recommended for predictable, structured JSON outputs).

---

### 2. Database (Turso & libSQL)

- **`TURSO_DATABASE_URL`** *(string)*
  - The primary connection URL for your Turso cloud database.
  - Format: `libsql://<database-name>-<org-name>.turso.io` or `https://<database-name>-<org-name>.turso.io`.
  - **Fallback:** If omitted, LocalLLM automatically falls back to local SQLite (`tasks.db`).

- **`TURSO_AUTH_TOKEN`** *(string)*
  - Turso authorization JWT token.
  - Generated using the Turso CLI:
    ```bash
    turso db tokens create <database-name>
    ```

- **`TURSO_SYNC_URL`** *(string, optional)*
  - When specified, activates **Turso Embedded Replica** mode. The app writes to a local SQLite replica on disk and syncs changes with Turso cloud.
  - Format: `libsql://<database-name>-<org-name>.turso.io`.

- **`TURSO_LOCAL_REPLICA_PATH`** *(string, optional)*
  - Local database file name used by the embedded replica.
  - Default: `tasks_replica.db`.

---

### 3. Telegram Notifications

- **`TELEGRAM_BOT_TOKEN`** *(string, optional)*
  - Telegram bot token generated via Telegram's `@BotFather`.
  - Leave empty to disable Telegram alerts.

- **`TELEGRAM_CHAT_ID`** *(string/integer, optional)*
  - Your personal Telegram user ID or group chat ID where execution reports are posted.
  - Retrieve by messaging `@userinfobot` or `@getmyid_bot` on Telegram.

- **`TELEGRAM_NOTIFY_ALL`** *(boolean, optional)*
  - `true` (default): Sends a message for every completed run (`SUCCESS`, `PARTIAL_SUCCESS`, `NO_RESULT`, `FAILED`).
  - `false`: Only notifies when a task has `FAILED`.

---

### 4. External Data & Site Skills

- **`FINNHUB_API_KEY`** *(string, optional)*
  - Free API key obtained from [finnhub.io](https://finnhub.io).
  - Used by `site_skills/premarket.py` and `site_skills/afterMarket.py` to pull market headlines and analyst sentiment.

- **`WSDOT_ACCESS_CODE`** *(string, optional)*
  - API Access code from Washington State Department of Transportation [wsdot.wa.gov](https://wsdot.wa.gov/traffic/api/).
  - Used by `site_skills/wsdot.py` for automated Seattle / WA highway pass reports and travel alerts.

---

### 5. Browser Automation

- **`CHROME_PATH`** *(string, optional)*
  - Path to Google Chrome on Windows.
  - Default: `C:\Program Files\Google\Chrome\Application\chrome.exe`.

- **`HEADLESS`** *(boolean, optional)*
  - Default: `false`.
  - Headed mode is strongly recommended because anti-bot systems (Safeway, Akamai, Cloudflare) frequently block headless browsers.
