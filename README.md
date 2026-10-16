# 🤖 LocalLLM Agent: Plugin-Oriented Web Automation

A high-performance, local-first AI web automation platform powered by `browser-use`, `FastAPI`, `React`, and `LM Studio` (or any OpenAI-compatible local model server). 

It features a hybrid architecture combining general-purpose browser agent reasoning with high-stability, site-specific automation plugins, complete with automated scheduling, real-time SSE streaming, and a generic 4-tier task evaluation system.

---

## 🌟 Key Features

- **🔒 100% Local-First**: Runs entirely on your own hardware via local LLM endpoints (LM Studio, Ollama, vLLM). No cloud API keys required.
- **⚡ Pre-Flight Plugin Hand-off**: Specialized site plugins bypass agent trial-and-error by deterministically handling complex SPA navigation, drawer filters, and high-speed DOM extraction.
- **🎯 Category-First Semantic Matching**: Evaluates multiple target items in a single pass across category cards using local LLM reasoning, cutting execution time by over 60%.
- **📊 4-Tier Outcome Evaluation**:
  - 🟢 **`SUCCESS`**: Workflow completed and all requested items/goals were fully satisfied.
  - 🟡 **`PARTIAL_SUCCESS`**: Workflow completed cleanly, but only a subset of requested items was available on the target website (e.g., 5 of 7 coupons found).
  - ⚪ **`NO_RESULT`**: Workflow completed cleanly and checked valid sources, but 0 matching records exist on the website.
  - 🔴 **`FAILED`**: Automation crashed, got blocked by bot detection/CAPTCHA, or encountered an unhandled exception.
- **📅 Advanced Task Scheduling**: Native support for **Once**, **Daily**, **Weekly**, and **Monthly** execution intervals via `APScheduler`, with manual **Run Now** triggers.
- **📡 Real-Time Dashboard**: Modern React + Vite web interface with real-time Server-Sent Events (SSE) updates, live log viewing, markdown rendering, and task management.
- **📲 Telegram Notifications**: Automatic completion reports delivered to Telegram with formatted markdown tables and status indicators.
- **🧠 Semantic Context Injection**: Manage domain instructions, user preferences, and site rules dynamically through the built-in Context Manager.

---

## 🚀 Getting Started

### Prerequisites

1. **LM Studio**: Download and install from [lmstudio.ai](https://lmstudio.ai).
   - Load your preferred model (e.g., `google/gemma-4-e2b`, `qwen2.5-7b-instruct`, `llama-3.1-8b`).
   - Start the local inference server (default: `http://localhost:1234/v1` or configure your port in `.env`).
2. **Python & uv**: Install the fast package manager `uv` from [astral.sh/uv](https://astral.sh/uv).
3. **Node.js**: Node 18+ for the Vite dashboard.

### Environment Configuration

Create or edit `.env` in the project root:
```env
# LLM Endpoint (LM Studio / vLLM / Ollama)
LLM_BASE_URL=http://localhost:1234/v1
LLM_API_KEY=lm-studio
LLM_MODEL=google/gemma-4-e2b

# Browser Settings
CHROME_PATH=C:\Program Files\Google\Chrome\Application\chrome.exe
HEADLESS=false

# Telegram Notifications (Optional)
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
TELEGRAM_NOTIFY_ALL=true
```

### Launching the Application

Run the all-in-one startup script in PowerShell:
```powershell
.\run.ps1
```
This script automatically:
1. Validates your LM Studio server connection.
2. Synchronizes backend Python dependencies via `uv`.
3. Starts the **FastAPI** backend on `http://localhost:8000`.
4. Starts the **Vite + React** frontend on `http://localhost:5173`.

---

## 🏗️ Architecture

```
User Prompt ──► Context Evaluator ──► Plugin Match?
                     │                      │
                     │ (Yes)                │ (No)
                     ▼                      ▼
           Pre-Flight Plugin         Orchestrator Planner
           (e.g., Safeway / Weee)     (browser-use agent loop)
                     │                      │
                     └──────────┬───────────┘
                                ▼
                     Generic 4-Tier Evaluator
                     (SUCCESS / PARTIAL / NO_RESULT / FAILED)
                                │
                     ┌──────────┴──────────┐
                     ▼                     ▼
              SQLite Database     Telegram Notification
              & SSE Dashboard
```

### 1. Hybrid Execution Model
- **Pre-Flight Plugins (`site_skills/`)**: When a known site is recognized in the prompt, pre-flight takes control of the page session, executes DOM-level navigation and data gathering using CDP `page.evaluate()`, and hands structured results back to the pipeline.
- **General Agent Loop (`browser-use`)**: For arbitrary web tasks without dedicated plugins, the LLM agent plans and executes multi-step actions using Playwright.

### 2. Specialized Site Plugins
- **[site_skills/safeway.py](file:///c:/Dev/LocalLLM/site_skills/safeway.py)**:
  - Automates Safeway "for U" digital coupons.
  - Sidebar category drawer filtering and SPA pagination.
  - Scrapes deals using exact BEM card selectors (`[class*="coupon-card__card-body"]`).
  - Batch-evaluates cards in chunks of 10 with dedicated token budgets for local reasoning models.
  - Distinguishes clipped coupons, auto-sale promotions, and unclipped deals.
- **[site_skills/weee.py](file:///c:/Dev/LocalLLM/site_skills/weee.py)**:
  - Automated coupon scraping and product search on SayWeee.
- **[site_skills/sofi.py](file:///c:/Dev/LocalLLM/site_skills/sofi.py)**:
  - Monitors and extracts engineering roles from the SoFi careers portal.

### 3. Evaluation Engine (`backend/core/evaluator.py`)
Separates technical execution integrity from data availability:
1. **Technical Crash Check**: Inspects browser errors, runner exceptions, and anti-bot blockers (CAPTCHA, Cloudflare).
2. **Deterministic Summary Parser**: Extracts numerical match counts (`X searched | Y clipped | Z not found`) directly from structured reports.
3. **LLM Judge**: Classifies unstructured or open-ended task responses into the 4 outcome tiers.
4. **Heuristic Fallback**: Ensures safe outcome assignment even during LLM timeouts.

---

## 📂 Project Structure

```
LocalLLM/
├── backend/
│   ├── automation/          # Browser automation utilities & Playwright skills
│   ├── core/
│   │   ├── evaluator.py     # Generic 4-tier task evaluation engine
│   │   ├── notifier.py      # Telegram notification service
│   │   └── pipeline.py      # Task lifecycle & orchestration pipeline
│   ├── database/            # SQLAlchemy models, SQLite session & migrations
│   ├── services/
│   │   ├── context.py       # Semantic context evaluator
│   │   └── llm.py           # Custom LM Studio client & JSON sanitizers
│   └── main.py              # FastAPI application, REST endpoints, & SSE stream
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── ContextManager.tsx  # Dynamic domain instruction manager
│   │   │   ├── TaskForm.tsx        # Task creator (Cadence & prompt inputs)
│   │   │   ├── TaskItem.tsx        # Task card with 4-tier status badges
│   │   │   └── TaskList.tsx        # Scheduled & history views with pagination
│   │   ├── store.ts         # Jotai global state management
│   │   └── index.css        # Premium dark-mode glassmorphic design system
│   ├── package.json
│   └── vite.config.ts
├── site_skills/             # High-stability domain plugins (Safeway, Weee, SoFi)
├── logs/                    # Timestamped execution logs for every task run
├── run.ps1                  # Single-command launcher for backend + frontend
├── pyproject.toml           # Python dependencies managed via uv
└── README.md
```

---

## 🛠️ Tech Stack

- **Inference**: Local LLMs via [LM Studio](https://lmstudio.ai) / OpenAI-compatible API
- **Browser Automation**: [browser-use](https://github.com/browser-use/browser-use) + Playwright (CDP)
- **Backend**: FastAPI, SQLAlchemy, SQLite, APScheduler, httpx, Uvicorn
- **Frontend**: React 18, Vite, Jotai, Lucide Icons, Marked
- **Package Management**: [uv](https://astral.sh/uv) (Python) & npm (Node)
