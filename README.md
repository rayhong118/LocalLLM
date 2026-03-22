# LocalLLM Agent

A local AI agent system that allows users to submit tasks via a web UI or API, performing web automation using `browser-use` and `Ollama`.

## Getting Started

### Prerequisites

1.  **Ollama**: Download and install from [ollama.com](https://ollama.com/download).
    *   Pull the required model: `ollama pull qwen3.5-32k` (or the model specified in `agent.py`).
2.  **uv**: A fast Python package installer and resolver. Install it from [astral.sh/uv](https://astral.sh/uv).

### Installation & Running

The easiest way to start the application is by using the provided PowerShell script.

1.  Open PowerShell in the project directory.
2.  Run the launcher:
    ```powershell
    .\run.ps1
    ```

The `run.ps1` script will:
*   Stop any existing Ollama processes.
*   Start a fresh Ollama server in the background.
*   Sync project dependencies using `uv sync`.
*   Start the agent/server.

## Architecture

The project is built with a decoupled architecture focusing on local execution and ease of use.

### Components

*   **[main.py](file:///c:/LocalLLM/main.py)**: The entry point for the FastAPI application. It defines the API endpoints, serves the static frontend, and manages background tasks.
*   **[agent.py](file:///c:/LocalLLM/agent.py)**: Contains the core agent logic powered by `browser-use`. It interacts with the browser and the LLM (via Ollama) to accomplish tasks.
*   **[database.py](file:///c:/LocalLLM/database.py)**: Manages persistence using SQLAlchemy and SQLite. It stores tasks and their corresponding outputs in `tasks.db`.
*   **[utils.py](file:///c:/LocalLLM/utils.py)**: Provides utility functions for saving data in JSON or Markdown formats.
*   **[run.ps1](file:///c:/LocalLLM/run.ps1)**: An orchestration script that automates the setup and execution environment.
*   **frontend/**: A directory containing the web UI (HTML, CSS, JS) accessible at `http://localhost:8000` when the server is running.
*   **pyproject.toml**: Defines project metadata and dependencies.

### Technology Stack

*   **Browser Automation**: `browser-use` (with Playwright)
*   **LLM Provider**: `Ollama` (Local)
*   **Backend**: `FastAPI`
*   **Database**: `SQLAlchemy` (SQLite)
*   **Package Management**: `uv`
