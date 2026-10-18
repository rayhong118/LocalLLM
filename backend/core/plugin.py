import os
import importlib
from abc import ABC, abstractmethod
from typing import Optional

class BaseSitePlugin(ABC):
    @abstractmethod
    async def run_pre_flight(self, browser, prompt: str, context: str, log_path: str, llm) -> str:
        """
        Execute site-specific automation before the main agent loop.
        Returns scraped data as a string to be injected into the agent task.
        """
        pass

class PluginRegistry:
    @staticmethod
    def get_plugin(site_key: str) -> Optional[BaseSitePlugin]:
        """Dynamically load and return a plugin instance for the given site key."""
        if not site_key:
            return None
            
        # Resolve real file name case-insensitively so afterMarket / aftermarket both work
        actual_name = None
        if os.path.isdir("site_skills"):
            for fname in os.listdir("site_skills"):
                if fname.lower() == f"{site_key.lower()}.py":
                    actual_name = fname[:-3]
                    break
        if not actual_name:
            return None

        try:
            module = importlib.import_module(f"site_skills.{actual_name}")
            # Try to find a class that inherits from BaseSitePlugin
            for name in dir(module):
                obj = getattr(module, name)
                if isinstance(obj, type) and issubclass(obj, BaseSitePlugin) and obj is not BaseSitePlugin:
                    return obj()
            
            # Fallback for legacy functional plugins
            automation_fn = getattr(module, f"{site_key}_run_pre_flight", None)
            if automation_fn:
                # Wrap functional plugin in a shim
                class FunctionalShim(BaseSitePlugin):
                    async def run_pre_flight(self, browser, prompt, context, log_path, llm):
                        return await automation_fn(browser, prompt, context, log_path, llm)
                return FunctionalShim()
                
        except Exception as e:
            print(f"Error loading plugin '{site_key}': {e}")
            
        return None
