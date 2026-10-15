# LocalLLM Agent: Plugin-Oriented Automation

A local AI agent system that performs complex web automation tasks using `browser-use` and `LM Studio` (or any OpenAI-compatible local LLM server). It features a hybrid architecture combining general-purpose agent reasoning with high-stability, site-specific automation plugins.

## Key Features

- **Local-First**: Runs entirely on your hardware via LM Studio (OpenAI-compatible REST API).
- **Pre-Flight Hand-off**: High-performance "site-skills" handle the heavy lifting (navigation, filtering, scraping) before the main agent loop begins.
- **Skill-Based Stability**: Uses CDP-compatible `page.evaluate()` JS execution for ultra-reliable DOM interaction, bypassing the fragility of traditional CSS selectors.
- **Semantic Context Injection**: Automatically retrieves domain knowledge and site-specific instructions based on task analysis.

## Getting Started

### Prerequisites

1.  **LM Studio**: Download and install from [lmstudio.ai](https://lmstudio.ai).
    *   Load your preferred model.
    *   Start the local server (default: `http://localhost:1234/v1`).
    *   (Optional) If LM Studio is running on another machine or port, set `LLM_BASE_URL` in `.env` (e.g. `LLM_BASE_URL=http://<host>:1234/v1`).
2.  **uv**: A fast Python package installer and resolver. Install it from [astral.sh/uv](https://astral.sh/uv).

### Installation & Running

1.  Open PowerShell in the project directory.
2.  Run the launcher:
    ```powershell
    .\run.ps1
    ```

## Architecture

The project utilizes a tiered execution model to ensure production-grade reliability on dynamic websites.

### Core Workflow

1. **Context Manager**: Analyzes the user prompt to identify relevant domain knowledge and whether a site-specific **plugin** is required.
2. **Pre-Flight Hand-off**: If a plugin (e.g., `safeway.py`) matches, it executes a high-speed automation script to handle site navigation, category selection, and exhaustive data extraction.
3. **Orchestrator Loop**: If no plugin exists, or to process scraped data, the main agent loop executes an orchestrated plan using structured reasoning and "skills."

### Components

*   **[backend/agent.py](file:///c:/LocalLLM/backend/agent.py)**: The central orchestrator entry point.
*   **[site_skills/](file:///c:/LocalLLM/site_skills/)**: Contains specialized Python plugins for specific domains.
    *   **[safeway.py](file:///c:/LocalLLM/site_skills/safeway.py)**: Handles complex SPA navigation, filter drawers, and deduplicated coupon scraping for Safeway.
    *   **[weee.py](file:///c:/LocalLLM/site_skills/weee.py)**: Fast coupon scraping and card matching for SayWeee.
    *   **[sofi.py](file:///c:/LocalLLM/site_skills/sofi.py)**: Monitors the SoFi careers board for Fullstack/Frontend engineering roles matching user skillset.
*   **[backend/services/llm.py](file:///c:/LocalLLM/backend/services/llm.py)**: A custom LM Studio / OpenAI-compatible integration (`JsonStrippingChatLMStudio`) that enforces clean JSON output and strips reasoning artifacts.
*   **[backend/config.py](file:///c:/LocalLLM/backend/config.py)**: Global settings for model selection, context window, LM Studio endpoint, and browser behavior.
*   **[backend/core/pipeline.py](file:///c:/LocalLLM/backend/core/pipeline.py)**: Modular task orchestration pipeline coordinating planner, pre-flight plugins, browser-use execution, and evaluator.
*   **[run.ps1](file:///c:/LocalLLM/run.ps1)**: Launcher verifying LM Studio connectivity, dependency sync, Vite frontend dev server, and FastAPI backend initialization.

### Technology Stack

- **Reasoning/Planning**: Local LLM via `LM Studio` (`http://localhost:1234/v1`)
- **Browser Automation**: `browser-use` (Playwright / CDP)
- **Backend Framework**: `FastAPI`
- **Frontend Dashboard**: `React` + `Vite`
- **Dependency Management**: `uv`

