import os
import importlib
from abc import ABC, abstractmethod
from typing import Optional

class BaseSitePlugin(ABC):
    description: str = ""

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

    @staticmethod
    def get_plugin_descriptions() -> dict[str, str]:
        """Dynamically scans site_skills/ and returns a dict mapping skill key to its description."""
        descriptions = {}
        if not os.path.isdir("site_skills"):
            return descriptions

        for fname in os.listdir("site_skills"):
            if fname.endswith(".py") and not fname.startswith("__"):
                skill_key = fname[:-3]
                plugin = PluginRegistry.get_plugin(skill_key)
                if plugin:
                    desc = getattr(plugin, "description", "").strip()
                    if not desc and plugin.__doc__:
                        first_line = plugin.__doc__.strip().split("\n")[0].strip()
                        if not first_line.startswith("Plugin wrapper"):
                            desc = first_line
                    descriptions[skill_key] = desc or f"{skill_key} website automation skill."
        return descriptions
