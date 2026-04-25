# LocalLLM Agent: Plugin-Oriented Automation

A local AI agent system that performs complex web automation tasks using `browser-use` and `Ollama`. It features a hybrid architecture combining general-purpose agent reasoning with high-stability, site-specific automation plugins.

## Key Features

- **Local-First**: Runs entirely on your hardware via Ollama.
- **Pre-Flight Hand-off**: High-performance "site-skills" handle the heavy lifting (navigation, filtering, scraping) before the main agent loop begins.
- **Skill-Based Stability**: Uses CDP-compatible `page.evaluate()` JS execution for ultra-reliable DOM interaction, bypassing the fragility of traditional CSS selectors.
- **Semantic Context Injection**: Automatically retrieves domain knowledge and site-specific instructions based on task analysis.

## Getting Started

### Prerequisites

1.  **Ollama**: Download and install from [ollama.com](https://ollama.com/download).
    *   Pull the required model: `ollama pull qwen3.5:9b` (or the model specified in `config.py`).
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

*   **[agent.py](file:///c:/LocalLLM/agent.py)**: The central orchestrator. Manages the hand-off between Pre-Flight plugins and the main Reasoning loop.
*   **[site_skills/](file:///c:/LocalLLM/site_skills/)**: Contains specialized Python plugins for specific domains.
    *   **[safeway.py](file:///c:/LocalLLM/site_skills/safeway.py)**: Handles complex SPA navigation, filter drawers, and deduplicated coupon scraping for Safeway.
*   **[llm_wrapper.py](file:///c:/LocalLLM/llm_wrapper.py)**: A custom Ollama integration that enforces clean JSON output and strips reasoning artifacts.
*   **[config.py](file:///c:/LocalLLM/config.py)**: Global settings for model selection, context window (32k), and browser behavior.
*   **[context_manager.py](file:///c:/LocalLLM/context_manager.py)**: Semantic analyzer that pairs tasks with relevant database knowledge.
*   **[skills.py](file:///c:/LocalLLM/skills.py)**: A library of deterministic UI skills (smart_click, smart_type, etc.) available to the agent.
*   **[database.py](file:///c:/LocalLLM/database.py)**: SQLite persistence layer for task tracking and knowledge retrieval.
*   **[run.ps1](file:///c:/LocalLLM/run.ps1)**: Orchestrates the background Ollama server, dependency sync, and environment initialization.

### Technology Stack

- **Reasoning/Planning**: `qwen3.5:9b` via `Ollama`
- **Browser Automation**: `browser-use` (Playwright / CDP)
- **Backend Framework**: `FastAPI`
- **Dependency Management**: `uv`
