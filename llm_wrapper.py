# llm_wrapper.py
import json
import re
import logging
from datetime import datetime
from typing import Optional, Any

# browser-use imports
from browser_use.llm.ollama.serializer import OllamaMessageSerializer
from browser_use import ChatOllama
from browser_use.llm.views import ChatInvokeCompletion
from browser_use.llm.exceptions import ModelProviderError

class JsonStrippingChatOllama(ChatOllama):
    """Refined ChatOllama wrapper with multi-stage JSON repair and Qwen/Gemma residency."""
    
    async def ainvoke(self, messages, output_format=None, **kwargs) -> ChatInvokeCompletion:
        ollama_messages = OllamaMessageSerializer.serialize_messages(messages)
        format_param = output_format.model_json_schema() if output_format else None
        
        async def call_llm(fmt):
            try:
                resp = await self.get_client().chat(
                    model=self.model,
                    messages=ollama_messages,
                    format=fmt,
                    options=self.ollama_options,
                )
                return resp.message.content or ''
            except Exception as e:
                print(f"DEBUG - LLM Call Error: {e}")
                return None

        # Try strict schema first, then generic JSON mode if it fails/returns empty
        content = await call_llm(format_param)
        if not content and format_param:
            content = await call_llm("json")
        
        if not content:
            raise ModelProviderError(message="LLM returned no content.", model=self.name)

        # Pre-processing: Strip reasoning blocks and common markdown wrappers
        content = self._clean_raw_content(content)

        # Repetition Loop Safety: Truncate but try to keep closing braces if it looks like JSON
        for seq in re.findall(r'(\s+\w+){3,}', content):
             if content.count(seq) > 20:
                 print("DEBUG - Detected repetition loop. Truncating safely.")
                 content = content.split(seq)[0]
                 if '{' in content and '}' not in content:
                     # Close the current string and the object
                     content += '"\n, "action": [], "thinking": "Loop detected" \n}'
                 break

        # Assistant Fallback
        if '{' not in content and ('|' in content or '###' in content or '1.' in content):
             print("DEBUG - Detected helpful assistant table/list instead of JSON.")

        # Parsing Pipeline
        try:
            return ChatInvokeCompletion(completion=output_format.model_validate_json(content), usage=None)
        except Exception:
            pass

        # 2. Heuristic extraction and repair
        repaired = self._repair_json(content, output_format)
        if repaired:
            return ChatInvokeCompletion(completion=repaired, usage=None)

        # 3. Persistent Failure Logging
        self._log_failure_trace(content)
        raise ModelProviderError(
            message=f"Failed to parse model output. Raw trace saved. Snippet: {content[:200]}",
            model=self.name
        )

    def _clean_raw_content(self, text: str) -> str:
        """Removes non-JSON blocks like <thought> or markdown wrappers."""
        for tag in ['thought', 'reasoning', 'thinking']:
            if f'<{tag}>' in text:
                text = text.split(f'</{tag}>')[-1]
        
        text = text.replace('---<channel|>', '').replace('---', '')

        if '```json' in text:
            text = text.split('```json')[-1].split('```')[0]
        return text.strip()

    def _repair_json(self, text: str, schema: Any) -> Optional[Any]:
        """Attempts to extract and fix malformed JSON strings or XML fallbacks."""
        # 1. XML-tag Fallback: Handle <action>verb(params)</action>
        if '<action>' in text:
            action_match = re.search(r'<action>\s*(\w+)\((.*?)\)\s*</action>', text, re.IGNORECASE | re.DOTALL)
            if action_match:
                func_name, params_str = action_match.groups()
                params = {}
                for param in re.findall(r'(\w+)=([^,)]+)', params_str):
                    key, val = param
                    params[key] = int(val) if val.isdigit() else val.strip("'\"")
                
                # Treat 'done' specially
                if func_name.lower() == 'done':
                    action_obj = {"done": {"text": params.get("text", "Done")}}
                else:
                    # Map common verbs
                    verb_map = {"click": "click_element", "type": "type_text", "hover": "hover_element"}
                    key = verb_map.get(func_name.lower(), func_name.lower())
                    action_obj = {key: params}
                
                return schema.model_validate({"thinking": f"Extracted from XML fallback: {func_name}", "action": [action_obj]})

        start, end = text.find('{'), text.rfind('}')
        
        if start == -1 or end == -1:
            try:
                terminal_keywords = ["final result:", "task complete", "i have found", "failure:", "cannot find", "summary of results", "the current", "value:", "based on the", "根据", "总结", "结果", "当前"]
                if any(kw in text.lower() for kw in terminal_keywords):
                    wrapped_data = {
                        "thinking": f"Extracted text answer from fallback: {text[:200]}...",
                        "evaluation_previous_goal": "Extraction via text fallback",
                        "memory": "Model provided a terminal text answer.",
                        "next_goal": "Finish",
                        "action": [{"done": {"text": text[:2000]}}]
                    }
                    return schema.model_validate(wrapped_data)
                return None
            except Exception:
                return None
        
        blocks = re.findall(r'\{.*\}', text, re.DOTALL)
        for block in reversed(blocks):
            try:
                cleaned = re.sub(r',\s*([\]}])', r'\1', block)
                data = json.loads(cleaned)
                
                if isinstance(data, list):
                    data = {"action": data}

                # Hallucination Guard: Catch 'plan' or 'action_input' before it gets wrapped as an action
                for hallucination in ["plan", "action_input"]:
                    if hallucination in data:
                        val = str(data.pop(hallucination))
                        data["thinking"] = (data.get("thinking", "") + "\n" + val).strip()
                
                if isinstance(data.get("action"), str) and data["action"].lower() in ["plan", "none", "null"]:
                    data["action"] = []

                if "action" in data:
                    if isinstance(data["action"], dict):
                        data["action"] = [data["action"]]
                    elif isinstance(data["action"], str):
                        lower_act = data["action"].lower()
                        halluc_verbs = ["plan", "thinking", "action", "step", "goal", "click", "type", "scroll", "wait", "press", "open", "navigate"]
                        if not any(lower_act.startswith(h) for h in halluc_verbs):
                            data["action"] = [{"done": {"text": data["action"]}}]
                        else:
                            data["action"] = []
                
                if "current_state" in data and isinstance(data["current_state"], dict):
                    cs = data.pop("current_state")
                    for k, v in cs.items():
                        if k in ["evaluation_previous_goal", "memory", "next_goal", "thinking"]:
                            data[k] = v
                
                if "action" in data and isinstance(data["action"], list):
                    for act in data["action"]:
                        # URL Sanitization: Prevent aspx.safeway.com hallucinations
                        if "navigate" in act and "url" in act["navigate"]:
                            act["navigate"]["url"] = act["navigate"]["url"].replace("aspx.safeway.com", "safeway.com")
                        
                        if "type" in act and "type_text" not in act:
                            act["type_text"] = {"text": act.pop("type"), "index": act.pop("index", 0)}
                        if ("press" in act or "press_key" in act) and "key_combination" not in act:
                            key = act.pop("press", act.pop("press_key", None))
                            act["key_combination"] = {"key_combination": key}
                        for field in ["click_element", "hover_element", "scroll_to_element"]:
                            if field in act and isinstance(act[field], dict) and "index" not in act[field]:
                                act[field]["index"] = 0
                
                return schema.model_validate(data)
            except Exception:
                continue
        return None

    def _log_failure_trace(self, content: str):
        log_path = getattr(self, "log_path", "format_error_trace.txt")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n--- FAILED LLM OUTPUT {datetime.now()} ---\n{content}\n--- END ---\n")
