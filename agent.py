import asyncio
import sys
import logging
import config
from core.pipeline import AgentPipeline

# Set up global logging level for browser_use
logging.getLogger('browser_use').setLevel(logging.WARNING)

if config.IS_WINDOWS:
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

async def run_agent_task(task_id: int, prompt: str):
    pipeline = AgentPipeline(task_id, prompt)
    await pipeline.run()

if __name__ == "__main__":
    if len(sys.argv) > 2:
        asyncio.run(run_agent_task(int(sys.argv[1]), sys.argv[2]))
    else:
        # Default for local testing
        asyncio.run(run_agent_task(1, "Search for latest news on Ollama"))