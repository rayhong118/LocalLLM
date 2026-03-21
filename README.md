
run this script
.\run.ps1

to 
Kills any existing Ollama processes (ollama, ollama_llama_server, etc.)
Starts a fresh ollama serve in a hidden window, waits 3s, and verifies it launched
Runs uv sync to ensure dependencies are up to date
Runs uv run python agent.py
