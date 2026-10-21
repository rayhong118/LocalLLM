# 🤖 LocalLLM Agent: Plugin-Oriented Web Automation & Intelligence

A high-performance, local-first AI web automation and intelligence platform powered by `browser-use`, `FastAPI`, `React`, and `LM Studio` (or any OpenAI-compatible local model server).

It features a hybrid architecture combining general-purpose browser agent reasoning with high-stability, site-specific automation plugins, complete with automated scheduling, real-time SSE streaming, interactive traffic mapping, and a generic 4-tier task evaluation system.

---

## 🌟 Key Features

- **🔒 100% Local-First**: Runs entirely on your own hardware via local LLM endpoints (LM Studio, Ollama, vLLM). No cloud API keys or subscriptions required for core inference.
- **⚡ Pre-Flight Plugin Hand-off**: Specialized site plugins bypass agent trial-and-error by deterministically handling complex SPA navigation, drawer filters, high-speed DOM extraction, and direct HTTP/RSS data streams.
- **🎯 Two-Stage Semantic Validation**: Evaluates target items, grocery coupons, and deal feeds in rapid batches using local LLM reasoning, dramatically reducing execution time and token usage.
- **📊 Generic 4-Tier Outcome Evaluation**:
  - 🟢 **`SUCCESS`**: Workflow completed and all requested items/goals were fully satisfied.
  - 🟡 **`PARTIAL_SUCCESS`**: Workflow completed cleanly, but only a subset of requested items was available on the target website (e.g., 5 of 7 coupons found, partial deal matches).
  - ⚪ **`NO_RESULT`**: Workflow completed cleanly and checked valid sources, but 0 matching records exist on the website.
  - 🔴 **`FAILED`**: Automation crashed, got blocked by anti-bot detection/CAPTCHA, or encountered an unhandled exception.
- **📅 Advanced Task Scheduling**: Native support for **Once**, **Daily**, **Weekly**, and **Monthly** execution intervals via `APScheduler`, with manual **Run Now** triggers and live schedule editing.
- **🔕 Silent When No Result (`silent_if_no_result`)**: Option to suppress Telegram notifications when a task finishes with `NO_RESULT`, eliminating notification fatigue for recurring background watchers.
- **🗺️ Interactive GIS Mapping**: Embedded Leaflet traffic map visualization for Washington State DOT (WSDOT) road alerts, corridor filtering (I-5, I-405, I-90, SR-520), and incident tracking directly in the dashboard.
- **📡 Real-Time Dashboard**: Modern React 18 + Vite web interface with real-time Server-Sent Events (SSE) updates, live log streaming, markdown rendering, and comprehensive task history.
- **📲 Telegram Notifications**: Automatic execution reports delivered to Telegram with formatted markdown tables, direct links, and status badges.
- **🧠 Semantic Context Injection**: Manage domain instructions, user preferences, and site rules dynamically through the built-in Context Manager and attach them per-task.

---

## 🚀 Getting Started

### Prerequisites

1. **LM Studio**: Download and install from [lmstudio.ai](https://lmstudio.ai).
   - Load your preferred model (e.g., `google/gemma-4-e2b`, `google/gemma-4-12b-qat`, `qwen2.5-7b-instruct`, `llama-3.1-8b`).
   - Start the local inference server (default: `http://localhost:1234/v1` or configure your port in `.env`).
2. **Python & uv**: Install the fast package manager `uv` from [astral.sh/uv](https://astral.sh/uv).
3. **Node.js**: Node 18+ for the Vite dashboard.

### Environment Configuration

Create or edit `.env` in the project root (see [ENV.md](file:///c:/Dev/LocalLLM/ENV.md) for full field documentation):

```env
# LLM Endpoint (LM Studio / vLLM / Ollama)
LLM_BASE_URL=http://localhost:1234/v1
LLM_API_KEY=lm-studio
LLM_MODEL=google/gemma-4-12b-qat

# Browser Settings
CHROME_PATH=C:\Program Files\Google\Chrome\Application\chrome.exe
HEADLESS=false

# Telegram Notifications (Optional)
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
TELEGRAM_NOTIFY_ALL=true

# Database (Turso / libSQL) — Defaults to local SQLite (tasks.db) if omitted
# Option A: Remote Turso Cloud Database
TURSO_DATABASE_URL=libsql://your-db-org.turso.io
TURSO_AUTH_TOKEN=your_turso_auth_token_here

# Option B: Turso Embedded Replica (Local SQLite synced with Turso Cloud)
# TURSO_SYNC_URL=libsql://your-db-org.turso.io
# TURSO_AUTH_TOKEN=your_turso_auth_token_here
# TURSO_LOCAL_REPLICA_PATH=tasks_replica.db

# Site Skill APIs (Optional)
WSDOT_ACCESS_CODE=your_wsdot_api_code_here
FINNHUB_API_KEY=your_finnhub_api_key_here
```

### Launching the Application

Run the all-in-one startup script in PowerShell:
```powershell
.\run.ps1
```

This script automatically:
1. Validates your LM Studio server connection and verified model status.
2. Synchronizes backend Python dependencies via `uv`.
3. Starts the **FastAPI** backend on `http://localhost:8000`.
4. Starts the **Vite + React** frontend on `http://localhost:5173`.

### 🗄️ Database Migration to Turso

To migrate your existing SQLite database (`tasks.db`) to Turso:

1. Add your `TURSO_DATABASE_URL` and `TURSO_AUTH_TOKEN` to `.env`.
2. Run the migration script:
```powershell
uv run python -m backend.database.migrate_to_turso
```
To test without writing any data:
```powershell
uv run python -m backend.database.migrate_to_turso --dry-run
```

---

## 🏗️ Architecture

```
User Prompt ──► Context Evaluator ──► Plugin Match?
                     │                      │
                     │ (Yes)                │ (No)
                     ▼                      ▼
           Pre-Flight Plugin         Orchestrator Planner
       (Safeway/Slickdeals/WSDOT)    (browser-use agent loop)
                     │                      │
                     └──────────┬───────────┘
                                ▼
                     Generic 4-Tier Evaluator
             (SUCCESS / PARTIAL / NO_RESULT / FAILED)
                                │
                     ┌──────────┴──────────┐
                     ▼                     ▼
              SQLite / Turso DB     Telegram Notification
              & SSE Dashboard      (with silent_if_no_result)
```

### 1. Hybrid Execution Model
- **Pre-Flight Plugins (`site_skills/`)**: When a known site or service domain is recognized in the prompt, pre-flight takes control. Depending on the target, it either performs headless HTTP/RSS ingestion or executes CDP DOM-level navigation and data extraction (`page.evaluate()`), handing structured reports back to the pipeline.
- **General Agent Loop (`browser-use`)**: For arbitrary web tasks without dedicated plugins, the LLM agent plans and executes multi-step actions dynamically using Playwright.

### 2. Specialized Site Plugins

| Plugin | Path | Description | Key Capabilities |
| :--- | :--- | :--- | :--- |
| **Safeway** | [safeway.py](file:///c:/Dev/LocalLLM/site_skills/safeway.py) | Safeway "for U" digital grocery coupons | Sidebar category drawer filtering, SPA pagination, BEM card parsing, batch coupon clipping, and deal matching. |
| **SayWeee** | [weee.py](file:///c:/Dev/LocalLLM/site_skills/weee.py) | SayWeee Asian & Hispanic grocery platform | Automated discount scraping, product search, cart analysis, and promotional discount tracking. |
| **Slickdeals** | [slickdeals.py](file:///c:/Dev/LocalLLM/site_skills/slickdeals.py) | Slickdeals bargain discovery & monitoring | Fast HTTP/RSS search feeds, frontpage/popular deal tracking, 14-day recency filtering, and 2-stage LLM candidate verification. |
| **WSDOT Traffic** | [wsdot.py](file:///c:/Dev/LocalLLM/site_skills/wsdot.py) | Washington State DOT highway conditions | REST API ingestion, King County corridor filtering (I-5, I-405, I-90, SR-520), pass reports, and structured Leaflet GeoJSON alerts. |
| **Pre-Market Wire** | [premarket.py](file:///c:/Dev/LocalLLM/site_skills/premarket.py) | Morning pre-market financial brief | Finnhub news API, Google Finance RSS, macroeconomic headline categorization (Fed, CPI, jobs), and ticker watchlist tracking. |
| **After-Market Wrap** | [afterMarket.py](file:///c:/Dev/LocalLLM/site_skills/afterMarket.py) | Post-market daily stock & index report | Post-market price changes via `yfinance`, major index benchmarks (S&P 500, Nasdaq, Dow, Russell, VIX), sector ETFs, and earnings events. |
| **SoFi Careers** | [sofi.py](file:///c:/Dev/LocalLLM/site_skills/sofi.py) | SoFi Greenhouse engineering job board | Monitors new career listings, role categorization, remote engineering filters, and hiring pipeline updates. |

### 3. Evaluation Engine (`backend/core/evaluator.py`)
Separates technical execution integrity from data availability:
1. **Technical Crash Check**: Inspects browser errors, runner exceptions, and anti-bot blockers (CAPTCHA, Cloudflare).
2. **Deterministic Summary Parser**: Extracts numerical match counts (`X items searched | Y deals found | Z not found` or `X clipped | Y not found`) directly from structured reports.
3. **LLM Judge**: Classifies unstructured or open-ended task responses into the 4 outcome tiers with strict adherence to instructions.
4. **Heuristic Fallback**: Ensures safe outcome assignment even during local LLM timeouts.

---

## 📂 Project Structure

```
LocalLLM/
├── backend/
│   ├── automation/              # Browser automation utilities & Playwright skills
│   ├── core/
│   │   ├── evaluator.py         # Generic 4-tier task evaluation engine
│   │   ├── notifier.py          # Telegram notification service (with silent_if_no_result)
│   │   ├── pipeline.py          # Task lifecycle & orchestration pipeline
│   │   └── plugin.py            # BaseSitePlugin abstract interface & dynamic registry
│   ├── database/                # SQLAlchemy models, SQLite/Turso session & migrations
│   │   ├── models.py            # Task, TaskContext, and Execution models
│   │   ├── session.py           # Turso/libSQL and SQLite database engine
│   │   └── migrate_to_turso.py  # SQLite to Turso migration utility
│   ├── services/
│   │   ├── context.py           # Semantic context evaluator
│   │   └── llm.py               # Custom LM Studio client & JSON sanitizers
│   └── main.py                  # FastAPI application, REST endpoints, & SSE stream
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── ContextManager.tsx      # Dynamic domain instruction manager
│   │   │   ├── ContextSelectModal.tsx  # Context selector for task binding
│   │   │   ├── EditScheduleModal.tsx   # Cadence & schedule recurrence editor
│   │   │   ├── Modal.tsx               # Reusable glassmorphic modal wrapper
│   │   │   ├── TaskForm.tsx            # Task creator (Cadence, prompt, silent option)
│   │   │   ├── TaskItem.tsx            # Task card with 4-tier status badges & live logs
│   │   │   ├── TaskList.tsx            # Scheduled & history views with pagination
│   │   │   └── WsdotMap.tsx            # Interactive Leaflet traffic alert map
│   │   ├── store.ts             # Jotai global state management
│   │   └── index.css            # Dark-mode glassmorphic design system
│   ├── package.json
│   └── vite.config.ts
├── site_skills/                 # High-stability domain plugins
│   ├── afterMarket.py           # Post-market stock reports (yfinance + macro)
│   ├── premarket.py             # Pre-market financial news (Finnhub + Google RSS)
│   ├── safeway.py               # Safeway digital coupon clipping & deals
│   ├── slickdeals.py            # Slickdeals RSS deal search & monitoring
│   ├── sofi.py                  # SoFi engineering career board monitor
│   ├── weee.py                  # SayWeee grocery deals & discounts
│   └── wsdot.py                 # Washington State DOT traffic & pass reports
├── logs/                        # Timestamped execution logs for every task run
├── run.ps1                      # Single-command launcher for backend + frontend
├── pyproject.toml               # Python dependencies managed via uv
├── ENV.md                       # Comprehensive environment variable guide
└── README.md
```

---

## 🛠️ Tech Stack

- **Inference**: Local LLMs via [LM Studio](https://lmstudio.ai) / OpenAI-compatible API (Gemma, Qwen, LLaMA)
- **Browser Automation**: [browser-use](https://github.com/browser-use/browser-use) + Playwright (CDP)
- **Backend**: FastAPI, SQLAlchemy, libSQL / Turso, SQLite, APScheduler, httpx, Uvicorn, yfinance
- **Frontend**: React 18, Vite, Jotai, Leaflet, Lucide Icons, Marked
- **Package Management**: [uv](https://astral.sh/uv) (Python) & npm (Node)
