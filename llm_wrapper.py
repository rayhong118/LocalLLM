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
    
    # Circuit breaker: abort if LLM fails to produce valid JSON N times in a row
    MAX_CONSECUTIVE_FAILURES = 5
    _consecutive_failures: int = 0
    
    # Action-level deduplication: track last N actions to detect semantic loops
    _recent_actions: list = []
    _action_repeat_count: int = 0
    MAX_ACTION_REPEATS = 3  # After this many identical actions, force failure

    async def ainvoke(self, messages, output_format=None, **kwargs) -> ChatInvokeCompletion:
        # 0. Early intercept for Bot Detection in the page DOM
        # IMPORTANT: Only scan the LAST message (page state), not system prompts
        # which contain our own protocol mentioning "security check"
        if messages and output_format:
            last_msg = str(getattr(messages[-1], 'content', '')).lower()
            bot_signatures = [
                "verify you are human", "attention required! | cloudflare", 
                "checking your browser before accessing", "security check to access",
                "just a moment...", "are you a robot", "verifying you are human",
                "please stand by, while we are checking your browser",
                "enable javascript and cookies to continue",
            ]
            
            if any(sig in last_msg for sig in bot_signatures):
                print(f"DEBUG - Bot detection intercepted in page DOM. Failing fast.")
                wrapped_data = {
                    "thinking": "Bot detection or CAPTCHA present on page.",
                    "memory": "BLOCKED. Security check.",
                    "action": [{"done": {"text": "security check encountered"}}]
                }
                try:
                    return ChatInvokeCompletion(completion=output_format.model_validate(wrapped_data), usage=None)
                except Exception as e:
                    print(f"DEBUG - Fast fail validation error: {e}")

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
        
        # If still empty, likely context overflow — retry with truncated messages
        if not content and len(ollama_messages) > 2:
            print("DEBUG - LLM returned empty. Likely context overflow. Retrying with truncated messages...")
            # Keep only system message + last message (current page state)
            truncated = [ollama_messages[0], ollama_messages[-1]]
            
            # Truncate the last message content if it's extremely long
            if isinstance(truncated[-1].get('content', ''), str) and len(truncated[-1]['content']) > 8000:
                truncated[-1] = dict(truncated[-1])
                truncated[-1]['content'] = truncated[-1]['content'][:8000] + "\n\n[DOM TRUNCATED - page too large. Focus on visible elements and complete your current goal.]"
            
            orig_messages = ollama_messages
            ollama_messages = truncated
            content = await call_llm(format_param)
            if not content:
                content = await call_llm("json")
            ollama_messages = orig_messages  # restore
        
        if not content:
            raise ModelProviderError(message="LLM returned no content.", model=self.name)

        # Pre-processing: Strip reasoning blocks and common markdown wrappers
        content = self._clean_raw_content(content)

        # Repetition Loop Safety: Truncate but try to keep closing braces if it looks like JSON
        if len(content) > 8000:
            print("DEBUG - Output extremely long, likely a repetition loop. Truncating.")
            content = content[:2000]
            if '{' in content and '}' not in content:
                content += '"\n, "action": [], "thinking": "Loop detected" \n}'

        # Catch consecutive repeated token patterns (e.g., "is-is", "0000000")
        match = re.search(r'(.{1,20}?)\1{15,}', content)
        if match:
             print(f"DEBUG - Detected repetition loop featuring '{match.group(1)}'. Truncating safely.")
             content = content[:match.start()]
             if '{' in content and '}' not in content:
                 # Force a failure response to break the agent out of the loop
                 print("DEBUG - Overwriting repeated generation with forced loop break.")
                 return ChatInvokeCompletion(
                     completion=output_format.model_validate({
                         "thinking": "CRITICAL LOOP DETECTED. I am repeating myself. I must try a different strategy.", 
                         "memory": "Stuck in repetition.", 
                         "action": [{"done": {"text": "FAILED: Agent stuck in generation loop. Need to rethink strategy."}}]
                     }), 
                     usage=None
                 )

        # Assistant Fallback
        if '{' not in content and ('|' in content or '###' in content or '1.' in content):
             print("DEBUG - Detected helpful assistant table/list instead of JSON.")

        # Parsing Pipeline
        try:
            bot_detection_keywords = ["captcha", "verify you are human", "are you a robot", "security check", "cloudflare", "attention required"]
            # Force fail if valid JSON says it's a security check but isn't terminating
            if any(kw in content.lower() for kw in bot_detection_keywords) and '"done"' not in content.lower():
                print("DEBUG - LLM valid JSON contained bot keywords but no 'done' action. Forcing failure.")
                content = '{"thinking": "Security check detected in output.", "memory": "BLOCKED.", "action": [{"done": {"text": "security check encountered"}}]}'

            parsed = output_format.model_validate_json(content)
            self._consecutive_failures = 0  # Reset on success
            
            # Action-level dedup: check if this is the same action as recent ones
            parsed = self._check_action_dedup(parsed, output_format)
            
            return ChatInvokeCompletion(completion=parsed, usage=None)
        except Exception:
            pass

        # 2. Heuristic extraction and repair
        repaired = self._repair_json(content, output_format)
        if repaired:
            self._consecutive_failures = 0  # Reset on success
            # Action-level dedup: check repair path too
            repaired = self._check_action_dedup(repaired, output_format)
            return ChatInvokeCompletion(completion=repaired, usage=None)

        # 3. Circuit breaker: abort after too many consecutive failures
        self._consecutive_failures += 1
        self._log_failure_trace(content)
        
        if self._consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
            self._consecutive_failures = 0  # Reset for next task
            raise ModelProviderError(
                message=f"CIRCUIT BREAKER: {self.MAX_CONSECUTIVE_FAILURES} consecutive parse failures. "
                        f"LLM is not producing valid JSON. Aborting task.",
                model=self.name
            )
        
        raise ModelProviderError(
            message=f"Failed to parse model output (attempt {self._consecutive_failures}/{self.MAX_CONSECUTIVE_FAILURES}). Snippet: {content[:200]}",
            model=self.name
        )

    def _clean_raw_content(self, text: str) -> str:
        """Removes non-JSON blocks like <thought> or markdown wrappers, saving thought content."""
        # 1. Extract thought content if present to preserve it before stripping
        thought_match = re.search(r'<(thought|reasoning|thinking|think)>(.*?)</\1>', text, flags=re.IGNORECASE | re.DOTALL)
        thought_content = thought_match.group(2).strip() if thought_match else ""

        # 2. Strip tags
        text = re.sub(r'<(thought|reasoning|thinking|think)>.*?(?:</\1>|$)', '', text, flags=re.IGNORECASE | re.DOTALL)
        
        # 3. Strip other wrappers
        text = re.sub(r'<action>\s*([\[{].*?[\]}])\s*</action>', r'\1', text, flags=re.IGNORECASE | re.DOTALL)
        text = text.replace('---<channel|>', '').replace('---', '')

        if '```json' in text:
            text = text.split('```json')[-1].split('```')[0]
        
        text = text.strip()

        # 4. If we found thought content but the resulting JSON has no thinking, inject it
        if thought_content and text.startswith('{') and '"thinking"' not in text:
            text = text.replace('{', f'{{"thinking": "{thought_content[:500].replace('"', "'")}", ', 1)

        return text

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
                bot_detection_keywords = ["captcha", "verify you are human", "are you a robot", "cloudflare", "access denied", "please verify", "bot detection", "security check", "unusual traffic", "blocked", "forbidden"]
                
                lower_text = text.lower()
                
                # Bot detection: immediately signal failure
                if any(kw in lower_text for kw in bot_detection_keywords):
                    wrapped_data = {
                        "thinking": "Bot detection or CAPTCHA encountered. Cannot proceed.",
                        "memory": "BLOCKED: Site has bot detection that cannot be bypassed.",
                        "next_goal": "Abort",
                        "action": [{"done": {"text": f"FAILED: Bot detection encountered. {text[:500]}"}}]
                    }
                    return schema.model_validate(wrapped_data)
                
                if any(kw in lower_text for kw in terminal_keywords):
                    wrapped_data = {
                        "thinking": f"Extracted text answer from fallback: {text[:200]}...",
                        "evaluation_previous_goal": "Extraction via text fallback",
                        "memory": "Model provided a terminal text answer.",
                        "next_goal": "Finish",
                        "action": [{"done": {"text": text[:2000]}}]
                    }
                    return schema.model_validate(wrapped_data)
                
                if "next steps:" in lower_text or "progress:" in lower_text:
                    wrapped_data = {
                        "thinking": f"Extracted task progress: {text[:200]}...",
                        "memory": "Model outputted plain text plan instead of JSON action.",
                        "action": []
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

                # Extract current_state fields BEFORE key purge so they get merged properly
                if "current_state" in data and isinstance(data["current_state"], dict):
                    cs = data.pop("current_state")
                    for k, v in cs.items():
                        if k in ["memory", "thinking"]:
                            data[k] = v

                # Aggressive Key Purge: Move all extra keys to thinking
                valid_keys = {"thinking", "memory", "action"}
                extra_keys = [k for k in data.keys() if k not in valid_keys]
                for k in extra_keys:
                    val = str(data.pop(k))
                    data["thinking"] = (data.get("thinking", "") + f" [{k}: {val[:100]}]").strip()

                if isinstance(data.get("action"), str) and data["action"].lower() in ["plan", "none", "null"]:
                    data["action"] = []

                if "action" in data:
                    if isinstance(data["action"], dict):
                        data["action"] = [data["action"]]
                    elif isinstance(data["action"], str):
                        lower_act = data["action"].lower()
                        halluc_verbs = ["plan", "thinking", "action", "step", "goal", "click", "type", "scroll", "wait", "press", "open", "navigate", "smart_click", "smart_type", "scroll_to_text", "nav_to_url", "safeway_"]
                        if not any(lower_act.startswith(h) for h in halluc_verbs):
                            data["action"] = [{"done": {"text": data["action"]}}]
                        else:
                            data["action"] = []
                
                if "action" in data and isinstance(data["action"], list):
                    unique_actions = []
                    seen_actions = set()
                    for act in data["action"]:
                        act_str = json.dumps(act, sort_keys=True)
                        if act_str not in seen_actions:
                            unique_actions.append(act)
                            seen_actions.add(act_str)
                    
                    if len(unique_actions) < len(data["action"]):
                        data["thinking"] = (data.get("thinking", "") + " [Deduplicated repetitive actions in response]").strip()
                        data["action"] = unique_actions

                    for act in data["action"]:
                        if "input" in act:
                            val = act.pop("input")
                            if isinstance(val, dict):
                                idx = val.get("index", val.get("selector", 0))
                                if isinstance(idx, str) and idx.isdigit(): idx = int(idx)
                                elif isinstance(idx, str): idx = int(''.join(filter(str.isdigit, idx)) or 0)
                                act["input_text"] = {"text": val.get("text", ""), "index": idx}
                            
                        if "click" in act:
                            val = act.pop("click")
                            if isinstance(val, dict):
                                act["click_element"] = {"index": val.get("index", 0)}
                            elif isinstance(val, int):
                                act["click_element"] = {"index": val}
                                
                        if "type" in act and "input_text" not in act:
                            act["input_text"] = {"text": act.pop("type"), "index": act.pop("index", 0)}
                        if ("press" in act or "press_key" in act) and "key_combination" not in act:
                            key = act.pop("press", act.pop("press_key", None))
                            act["key_combination"] = {"key_combination": key}
                        
                        if "search_page" in act:
                            if isinstance(act["search_page"], str):
                                act["search_page"] = {"pattern": act["search_page"]}
                            elif isinstance(act["search_page"], dict) and "pattern" not in act["search_page"]:
                                act["search_page"]["pattern"] = act["search_page"].pop("text", act["search_page"].pop("query", ""))
                        if "find_elements" in act:
                            if isinstance(act["find_elements"], str):
                                act["find_elements"] = {"selector": act["find_elements"]}
                            elif isinstance(act["find_elements"], dict) and "selector" not in act["find_elements"]:
                                act["find_elements"]["selector"] = act["find_elements"].pop("css_selector", act["find_elements"].pop("query", ""))
                            
                        for field in ["click_element", "hover_element", "scroll_to_element"]:
                            if field in act and isinstance(act[field], dict) and "index" not in act[field]:
                                act[field]["index"] = 0
                                
                        # Handle smart_click / smart_type / scroll_to_text if parameters are loose
                        if "smart_click" in act and isinstance(act["smart_click"], str):
                            act["smart_click"] = {"text": act["smart_click"]}
                        if "smart_type" in act and isinstance(act["smart_type"], dict):
                            if "label" not in act["smart_type"]:
                                act["smart_type"]["label"] = act["smart_type"].pop("text", "")
                        if "scroll_to_text" in act and isinstance(act["scroll_to_text"], str):
                            act["scroll_to_text"] = {"text": act["scroll_to_text"]}
                
                # Final safety purge: remove any straggler keys before validation
                final_extra = [k for k in data.keys() if k not in valid_keys]
                for k in final_extra:
                    data.pop(k)
                
                return schema.model_validate(data)
            except Exception:
                continue
        return None
    def _check_action_dedup(self, parsed, output_format):
        """Check if the parsed action is identical to recent actions and escalate."""
        import json as _json
        try:
            # Extract action signature for comparison
            action_list = getattr(parsed, 'action', [])
            if not action_list:
                return parsed  # No action to dedup
            
            action_sig = _json.dumps(
                [a.model_dump(exclude_none=True) for a in action_list], 
                sort_keys=True
            )
            
            if self._recent_actions and action_sig == self._recent_actions[-1]:
                self._action_repeat_count += 1
                log_path = getattr(self, "log_path", None)
                
                if self._action_repeat_count == 1:
                    # First repeat: inject warning into thinking
                    print(f"DEBUG - Action dedup: 1st repeat detected. Injecting warning.")
                    if log_path:
                        with open(log_path, "a", encoding="utf-8") as f:
                            f.write(f"\n--- ACTION DEDUP WARNING (repeat #{self._action_repeat_count}) ---\n")
                
                elif self._action_repeat_count == 2:
                    # Second repeat: replace action with scroll_down
                    print(f"DEBUG - Action dedup: 2nd repeat. Replacing with scroll_down.")
                    if log_path:
                        with open(log_path, "a", encoding="utf-8") as f:
                            f.write(f"\n--- ACTION DEDUP: Forced scroll_down (repeat #{self._action_repeat_count}) ---\n")
                    try:
                        replacement = output_format.model_validate({
                            "thinking": "ACTION DEDUP: I was about to repeat the same action. Scrolling instead to discover new elements.",
                            "memory": "Must try a DIFFERENT action. Do NOT repeat navigate.",
                            "action": [{"scroll_down": {"amount": 500}}]
                        })
                        return replacement
                    except Exception:
                        pass  # If scroll_down isn't in schema, fall through
                
                elif self._action_repeat_count >= self.MAX_ACTION_REPEATS:
                    # Third+ repeat: force failure
                    print(f"DEBUG - Action dedup: {self._action_repeat_count} repeats. Forcing done/failure.")
                    if log_path:
                        with open(log_path, "a", encoding="utf-8") as f:
                            f.write(f"\n--- ACTION DEDUP: Forced FAILURE (repeat #{self._action_repeat_count}) ---\n")
                    try:
                        replacement = output_format.model_validate({
                            "thinking": f"CRITICAL: I have repeated the same action {self._action_repeat_count} times. This is a loop. I must stop.",
                            "memory": "Loop detected. Aborting.",
                            "action": [{"done": {"text": f"FAILED: Agent stuck repeating the same action {self._action_repeat_count} times. The page may not have loaded correctly or a required element is missing."}}]
                        })
                        self._action_repeat_count = 0
                        self._recent_actions.clear()
                        return replacement
                    except Exception:
                        pass
            else:
                self._action_repeat_count = 0  # Reset on new action
            
            # Track this action
            self._recent_actions.append(action_sig)
            if len(self._recent_actions) > 5:
                self._recent_actions.pop(0)
                
        except Exception as e:
            print(f"DEBUG - Action dedup check failed (non-fatal): {e}")
        
        return parsed

    def _log_failure_trace(self, content: str):
        log_path = getattr(self, "log_path", "format_error_trace.txt")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n--- FAILED LLM OUTPUT {datetime.now()} ---\n{content}\n--- END ---\n")
