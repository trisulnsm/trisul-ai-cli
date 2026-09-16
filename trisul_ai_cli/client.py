import warnings
warnings.filterwarnings("ignore",category=FutureWarning,module="google.api_core")

import asyncio
import json
from contextlib import AsyncExitStack
from typing import Any, Dict, List, Optional
import nest_asyncio
import sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import logging
from dotenv import set_key, dotenv_values
from pathlib import Path
import os
try:
    import readline
except ImportError:
    pass
from importlib.metadata import version
import re
import subprocess
from trisul_ai_cli.tools.utils import TrisulAIUtils
from trisul_ai_cli.llm_factory import LLMFactory
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage
import json
import stdiomask
import time




class TrisulAIClient:
    def __init__(self):
        # Initialize asyncio
        nest_asyncio.apply()
        if os.name != "nt":
            os.environ["QT_QPA_PLATFORM"] = "xcb"
        
        # Initialize logging
        logging.basicConfig(
            filename= Path(os.getcwd()) / "trisul_ai_cli.log",
            level=logging.INFO,
            format="%(asctime)s - %(levelname)s - %(message)s",
            force=True
        )
        
        
        # Initialize utils
        self.utils = TrisulAIUtils(logging=logging)
        
        # Initialize MCP session
        self.session: ClientSession = None
        self.exit_stack = AsyncExitStack()
        self.stdio = None
        self.write = None
        
        # Initialize Global variables
        self.root_dir = Path(__file__).resolve().parent
        self.env_path = self.root_dir / ".env"
        self.llm_factory = LLMFactory(env_path=self.env_path, logging=logging)
        self.existing_ai_memory = []
        self.memory_json_path = self.root_dir / "trisul_ai_memory.json"
        with open(self.memory_json_path, "r", encoding="utf-8") as file:
            self.existing_ai_memory = json.load(file)
        self.confidence_threshold = 90
        self.max_iterations = 15
        self.line_chart_data = {}
        self.pie_chart_data = {}
        self.table_data = {}
        self.report_path = None
        self.verified_report_path = None
        self.dashboard_path = None
        self.dashboard_json = None
        self.dashboard_filename = None
        self.auto_open_reports = True
        self.pending_report_request = False
        self.report_data_fetched = False
        self.report_continue_attempts = 0
        self.sessions = {} # session_id -> conversation_history (list of Messages)
        self.zmq_endpoint: Optional[str] = None
        self.trisul_context: str = "context0"
                
        
        # Load main system prompt
        system_prompt_path = self.root_dir / "prompts/system_main.txt"
        template = system_prompt_path.read_text(encoding="utf-8")
        # Literal replace: str.format() treats JSON braces in the prompt as placeholders.
        main_system_prompt = template.replace(
            "{existing_ai_memory}",
            str(self.existing_ai_memory),
        )
    
        self.conversation_history = [
            SystemMessage(content=main_system_prompt)
        ]



    # Set your API key here
    def set_api_key(self, provider_type: str = "llm"):
        try:            
            if provider_type == "llm":
                provider = self.llm_factory.get_current_provider()
            elif provider_type == "embedding":
                provider = self.llm_factory.get_current_embedding_provider()
                if not provider:
                    print("\n🤖 (Bot) : No embedding provider set. Please select an embedding model first.")
                    return
            else:
                logging.error(f"[Client] Invalid provider type: {provider_type}")
                return

            while True:
                api_key = stdiomask.getpass(f"\n🤖 (Bot) : Enter your {provider.capitalize()} API Key ({provider_type}): ").strip()
                if api_key:
                    break
            
            if not self.env_path.exists():
                self.env_path.touch()
            
            if provider_type == "llm":
                self.llm_factory.set_api_key(api_key)
            else:
                self.llm_factory.set_api_key_for_provider(provider, api_key)
                
            print(f"\n🤖 (Bot) : API Key for {provider} ({provider_type}) set successfully.")
            logging.info(f"[Client] API Key set successfully for {provider} ({provider_type}).")
            
        except KeyboardInterrupt:
            print("\n\n🤖 (Bot) : API Key entry cancelled by user.\n")
            logging.info("[Client] API Key entry cancelled by user.")
            sys.exit(0)


    # Get your API key here
    def get_api_key(self) -> str:
        provider = self.llm_factory.get_current_provider()
        if provider == "custom":
            if (
                not self.llm_factory.get_custom_api_base_url()
                or not self.llm_factory.config.get("TRISUL_AI_MODEL")
            ):
                print("\n🎉 Welcome to Trisul AI CLI — turn raw network data into answers using plain English.\n")
                self.set_custom_llm()
            return

        api_key = self.llm_factory.get_current_api_key()
        if not api_key:
            # If the model is not configured in the environment, force model selection.
            if not self.llm_factory.config.get("TRISUL_AI_MODEL"):
                print("\n🎉 Welcome to Trisul AI CLI — turn raw network data into answers using plain English.\n")
                self.set_llm_model()
            else:
                self.set_api_key()



    # Change the LLM model version
    def set_llm_model(self):
        try:
            # Retrieve the full mapping of providers to models
            all_models = self.llm_factory.get_all_models()
            # Flatten into a list of (provider, model) tuples for display
            flat_list = []
            for provider, models_dict in all_models.items():
                for model in models_dict.get("llm", []):
                    flat_list.append((provider, model))

            current_provider = self.llm_factory.get_current_provider()
            current_model = self.llm_factory.get_current_model()

            # Display the list with indices
            print("\n🤖 (Bot) : Select an LLM model from the list below (provider:model): \n")
            for idx, (prov, mdl) in enumerate(flat_list, start=1):
                current_marker = ''
                if prov == current_provider and mdl == current_model:
                    current_marker = ' (current)'
                print(f"{idx}) {prov}:{mdl}{current_marker}")

            custom_option = len(flat_list) + 1
            custom_marker = ''
            if current_provider == "custom":
                custom_marker = ' (current)'
            print(
                f"{custom_option}) custom:local "
                f"(Ollama, LM Studio, vLLM, or any OpenAI-compatible endpoint){custom_marker}"
            )

            selected_index = None
            while True:
                choice = input(f"\n🤖 (Bot) : Enter your choice (1-{custom_option}): ").strip()
                if not choice.isdigit():
                    print("\n🤖 (Bot) : ❌ Invalid choice. Please enter a number.")
                    continue
                idx = int(choice)
                if 1 <= idx <= len(flat_list):
                    selected_index = idx - 1
                    break
                if idx == custom_option:
                    return self.set_custom_llm()
                print("\n🤖 (Bot) : ❌ Choice out of range. Try again.")

            selected_provider, selected_model = flat_list[selected_index]
            # Use the new factory method to set both provider and model
            embedding_set = self.llm_factory.set_model_by_name(selected_model)
            logging.info(f"[Client] Model set to {selected_model} with provider {selected_provider}")
            
            # Ensure API key for the selected provider is set
            if not self.llm_factory.get_current_api_key():
                print(f"\n🤖 (Bot) : API Key for {selected_provider} is missing.")
                self.set_api_key(provider_type="llm")
            
            # Handle Embedding Model Notification
            if not embedding_set:
                # Check if we have a valid embedding model set
                if not self.llm_factory.get_current_embedding_provider():
                     print(f"\n🤖 (Bot) : Note: No embedding model is currently set. You may want to run 'change_embedding_model'.")
            else:
                 print(f"\n🤖 (Bot) : Embedding model automatically updated to match {selected_provider}.")

            # Ensure API key for embedding provider is set if we have one
            emb_provider = self.llm_factory.get_current_embedding_provider()
            if emb_provider and not self.llm_factory.get_current_embedding_api_key():
                print(f"\n🤖 (Bot) : API Key for embedding provider '{emb_provider}' is missing.")
                self.set_api_key(provider_type="embedding")

            print(f"\n🤖 (Bot) : LLM Model changed to {selected_model} ({selected_provider})\n")
            return selected_model
        except KeyboardInterrupt:
            print("\n\n🤖 (Bot) : Model Selection cancelled by user.")
            logging.info("[Client] Model Selection cancelled by user.")
            sys.exit(0)

    def set_custom_llm(self):
        """Configure a local or self-hosted OpenAI-compatible LLM endpoint."""
        try:
            current_url = self.llm_factory.get_custom_api_base_url() or "http://localhost:11434"
            current_model = self.llm_factory.get_current_model() or "llama3.2"

            print(
                "\n🤖 (Bot) : Configure a custom OpenAI-compatible LLM endpoint "
                "(Ollama, LM Studio, vLLM, LocalAI, etc.)\n"
            )
            print("   Example base URL: http://localhost:11434  (Ollama)")
            print("   Example model:    llama3.2\n")

            base_url = input(f"API base URL [{current_url}]: ").strip() or current_url
            model_name = input(f"Model name [{current_model}]: ").strip() or current_model
            api_key = stdiomask.getpass(
                "API key (press Enter to skip for local servers like Ollama): "
            ).strip()

            if not self.env_path.exists():
                self.env_path.touch()

            self.llm_factory.set_custom_llm(
                base_url=base_url,
                model_name=model_name,
                api_key=api_key or "not-needed",
            )

            normalized_url = self.llm_factory.get_custom_api_base_url()
            print(
                f"\n🤖 (Bot) : Custom LLM configured: {model_name} @ {normalized_url}\n"
            )
            logging.info(
                f"[Client] Custom LLM configured: model={model_name}, base_url={normalized_url}"
            )
            return model_name
        except KeyboardInterrupt:
            print("\n\n🤖 (Bot) : Custom LLM configuration cancelled by user.")
            logging.info("[Client] Custom LLM configuration cancelled by user.")
            sys.exit(0)

    # Change the Embedding model version
    def set_embedding_model(self):
        try:
            embedding_models = self.llm_factory.get_all_embedding_models()
            
            current_emb_model = self.llm_factory.embedding_model
            
            print("\n🤖 (Bot) : Select an Embedding model from the list below (provider:model): \n")
            for idx, (prov, mdl) in enumerate(embedding_models, start=1):
                current_marker = ''
                if mdl == current_emb_model:
                    current_marker = ' (current)'
                print(f"{idx}) {prov}:{mdl}{current_marker}")
            
            selected_emb_index = None
            while True:
                choice = input(f"\n🤖 (Bot) : Enter your choice (1-{len(embedding_models)}): ").strip()
                if not choice.isdigit():
                    print("\n🤖 (Bot) : ❌ Invalid choice. Please enter a number.")
                    continue
                idx = int(choice)
                if 1 <= idx <= len(embedding_models):
                    selected_emb_index = idx - 1
                    break
                else:
                    print("\n🤖 (Bot) : ❌ Choice out of range. Try again.")
            
            emb_prov, emb_model = embedding_models[selected_emb_index]
            self.llm_factory.set_embedding_model(emb_model)
            print(f"\n🤖 (Bot) : Embedding Model changed to {emb_model} ({emb_prov})\n")

            # Ensure API key for embedding provider is set
            if not self.llm_factory.get_current_embedding_api_key():
                print(f"\n🤖 (Bot) : API Key for embedding provider '{emb_prov}' is missing.")
                self.set_api_key(provider_type="embedding")
                
            return emb_model

        except KeyboardInterrupt:
            print("\n\n🤖 (Bot) : Embedding Model Selection cancelled by user.")
            logging.info("[Client] Embedding Model Selection cancelled by user.")
            sys.exit(0)

    def get_current_model_status(self) -> dict:
        env_file = Path(self.env_path)
        if not env_file.exists():
            raise FileNotFoundError(f"{self.env_path} not found")

        result = {}

        with env_file.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()

                if not line or line.startswith("#") or "=" not in line:
                    continue

                key, value = line.split("=", 1)
                value = value.strip().strip("'").strip('"')

                if "API_KEY" in key:
                    result[key] = "*****"
                else:
                    result[key] = value

        return result



    async def connect_to_server(self, server_module: str = "trisul_ai_cli.server"):
        server_params = StdioServerParameters(
            command=sys.executable,
            args=["-m", server_module],
        )

        stdio_transport = await self.exit_stack.enter_async_context(stdio_client(server_params))
        self.stdio, self.write = stdio_transport
        self.session = await self.exit_stack.enter_async_context(ClientSession(self.stdio, self.write))
        await self.session.initialize()

        logging.info("[Client] Connected to server")



    async def get_mcp_tools(self) -> List[Dict[str, Any]]:
        tools_result = await self.session.list_tools()
        tool_list = []
        for tool in tools_result.tools:
            schema = self._adapt_tool_schema_for_llm(dict(tool.inputSchema or {}))
            # Convert to OpenAI function format which is widely supported by LangChain bind_tools
            tool_list.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": schema,
                }
            })
        return tool_list

    def _adapt_tool_schema_for_llm(self, schema: dict) -> dict:
        """Gemini/LangChain reject a tool parameter literally named 'title'."""
        props = schema.get("properties")
        if isinstance(props, dict) and "title" in props:
            props = dict(props)
            props["report_title"] = props.pop("title")
            if isinstance(props["report_title"], dict):
                props["report_title"]["title"] = "Report Title"
            schema = dict(schema)
            schema["properties"] = props
        return schema

    def _parse_connect_endpoint(self, text: str) -> Optional[str]:
        if not text:
            return None
        m = re.search(r"connect\s+to\s+(tcp://\S+)", text, re.I)
        if m:
            return m.group(1).rstrip(".,;")
        m = re.search(r"connect\s+to\s+([\d.]+:\d+)", text, re.I)
        if m:
            return f"tcp://{m.group(1)}"
        return None

    def _parse_connect_context(self, text: str) -> Optional[str]:
        if not text:
            return None
        m = re.search(r"connect\s+to\s+(context\S+)", text, re.I)
        if m:
            ctx = m.group(1).strip()
            return ctx if ctx.startswith("context") else f"context{ctx}"
        return None

    def _update_connection_from_text(self, text: str) -> None:
        endpoint = self._parse_connect_endpoint(text)
        if endpoint:
            self.zmq_endpoint = endpoint
            logging.info(f"[Client] TRP endpoint set to {endpoint}")
            return
        ctx = self._parse_connect_context(text)
        if ctx:
            self.trisul_context = ctx
            self.zmq_endpoint = None
            logging.info(f"[Client] Trisul context set to {ctx}")

    def _sync_connection_from_history(self) -> None:
        for msg in reversed(self.conversation_history):
            if isinstance(msg, HumanMessage):
                self._update_connection_from_text(str(msg.content or ""))
                if self.zmq_endpoint:
                    return

    @staticmethod
    def _canonical_context(ctx: str) -> str:
        """Normalize a context name the same way the MCP server does."""
        if not ctx:
            return ""
        ctx = str(ctx).strip().lower()
        if ctx.startswith("context_"):
            ctx = ctx.split("_", 1)[-1]
        if ctx in ("default", "context0"):
            return "context0"
        return f"context_{ctx}"

    def _contexts_equivalent(self, left: str, right: str) -> bool:
        return bool(left) and bool(right) and self._canonical_context(left) == self._canonical_context(right)

    def _resolve_locked_context(self, context_id: Optional[str]) -> Optional[str]:
        if context_id is None:
            return None
        context_id = str(context_id).strip()
        return context_id or None

    _LOCKED_CONTEXT_MENTION_RE = re.compile(
        r"\bcontext[_-]?0\b"
        r"|\bcontext[_-][A-Za-z][\w-]*\b"
        r"|(?:connect\s+to|use|switch\s+to|query)\s+(?:the\s+)?context\s+['\"]?([A-Za-z][\w-]*)['\"]?",
        re.I,
    )

    def _mentioned_other_contexts(self, text: str, locked_context: str) -> List[str]:
        if not text or not locked_context:
            return []
        others = []
        seen = set()
        for match in self._LOCKED_CONTEXT_MENTION_RE.finditer(text):
            raw = match.group(1) or match.group(0)
            raw = raw.strip().strip("'\"")
            if not raw or self._contexts_equivalent(raw, locked_context):
                continue
            key = self._canonical_context(raw)
            if key and key not in seen:
                seen.add(key)
                others.append(raw)
        return others

    def _locked_context_switch_message(self, locked_context: str, requested: str = None) -> str:
        requested_bit = f" '{requested}'" if requested else " a different context"
        return (
            f"This chat is connected to the **{locked_context}** context selected in WebTrisul. "
            f"I cannot use{requested_bit} from here. To use that context, switch to it in the "
            f"WebTrisul UI, then open the Trisul AI chat window from that context."
        )

    def _locked_context_instructions(self, locked_context: str) -> str:
        return (
            "\n\n### 🔒 WEB UI LOCKED CONTEXT (MANDATORY)\n"
            f"This Web UI session is bound to Trisul context `{locked_context}` only.\n"
            f"- ALWAYS pass `context=\"{locked_context}\"` to every tool that accepts a context argument.\n"
            "- NEVER use any other context name, even if the user asks for it.\n"
            "- NEVER use a ZMQ/tcp endpoint to reach another context or server.\n"
            "- If the user asks to use a different context, do NOT switch and do NOT query it. "
            "Tell them clearly: they must switch to that context in the WebTrisul UI and open "
            "the Trisul AI chat window from there.\n"
            f"- After explaining, continue answering their question using `{locked_context}` "
            "when the question is otherwise valid.\n"
        )

    def _ensure_locked_context_prompt(self, history: list, locked_context: str) -> None:
        if not locked_context or not history:
            return
        marker = "### 🔒 WEB UI LOCKED CONTEXT"
        first = history[0]
        if not isinstance(first, SystemMessage):
            return
        content = first.content or ""
        if marker not in content:
            first.content = content + self._locked_context_instructions(locked_context)
            return
        if locked_context not in content and self._canonical_context(locked_context) not in content:
            first.content = content + self._locked_context_instructions(locked_context)

    def _locked_context_override_notice(
        self, function_name: str, original_args: dict, locked_context: str
    ) -> Optional[str]:
        if not locked_context or function_name not in self.CONTEXT_TOOLS:
            return None
        original_args = original_args or {}
        requested_ctx = original_args.get("context")
        if requested_ctx and not self._contexts_equivalent(requested_ctx, locked_context):
            return (
                f"[SYSTEM] The requested context '{requested_ctx}' was ignored. This Web UI "
                f"session is locked to '{locked_context}'. Tell the user: "
                f"{self._locked_context_switch_message(locked_context, requested_ctx)}"
            )
        return None

    def _normalize_tool_args(
        self, function_name: str, function_args: dict, locked_context: str = None
    ) -> dict:
        args = dict(function_args or {})
        if "report_title" in args and "title" not in args:
            args["title"] = args.pop("report_title")
        if locked_context and function_name in self.CONTEXT_TOOLS:
            args["context"] = locked_context
            args.pop("zmq_endpoint", None)
            return args
        if function_name in self.TRP_ENDPOINT_TOOLS:
            if not args.get("zmq_endpoint") and self.zmq_endpoint:
                args["zmq_endpoint"] = self.zmq_endpoint
        if function_name in self.CONTEXT_TOOLS:
            if not args.get("context") and self.trisul_context:
                args.setdefault("context", self.trisul_context)
        if args.get("zmq_endpoint"):
            self.zmq_endpoint = args["zmq_endpoint"]
        return args

    def _last_tool_had_connection_error(self) -> bool:
        for msg in reversed(self.conversation_history):
            if isinstance(msg, ToolMessage):
                content = str(msg.content or "").lower()
                return (
                    "zmq timeout" in content
                    or "no response from ipc://" in content
                    or '"status": "error"' in content and "zmq" in content
                )
            if isinstance(msg, AIMessage):
                break
        return False


    def extract_message(self, e):
        s = str(e)
        m = re.search(r'message["\']?\s*[:=]\s*["\']?([^,"\}\]]+)', s)
        if m:
            return m.group(1).strip()
        
        return s

    def extract_text_from_content(self, content):
        if isinstance(content, str):
            return content
        
        if isinstance(content, list):
            # Gemini 3+ and other thinking models return content blocks; only the text
            # blocks belong in the user-facing answer. Reasoning blocks carry the
            # thought signatures and must never be rendered.
            text_parts = []
            for item in content:
                if isinstance(item, str):
                    text_parts.append(item)
                elif isinstance(item, dict):
                    if item.get('type') not in (None, 'text'):
                        continue
                    if 'text' in item:
                        text_parts.append(item['text'])
            return '\n'.join(p for p in text_parts if p)
        
        return str(content)


    REPORT_GENERATION_TOOLS = frozenset({
        "generate_excel_report",
        "generate_dynamic_report",
        "generate_dynamic_excel_report",
        "generate_key_monitor_excel_report",
        "generate_trisul_report",
    })
    REPORT_DATA_TOOLS = frozenset({
        "get_counter_group_topper",
        "get_key_traffic_data",
        "get_cginfo_from_countergroup_name",
        "search_keys",
        "list_all_available_counter_groups",
    })
    TRP_ENDPOINT_TOOLS = frozenset({
        "list_all_available_counter_groups",
        "get_cginfo_from_countergroup_name",
        "get_counter_group_topper",
        "get_key_traffic_data",
        "get_alerts_data",
        "search_keys",
        "generate_dynamic_report",
        "generate_dynamic_excel_report",
        "generate_key_monitor_excel_report",
        "generate_dashboard_json",
        "create_crosskey_counter_group",
        "create_filter_counter_group",
        "create_keyset_counter_group",
    })
    CONTEXT_TOOLS = TRP_ENDPOINT_TOOLS
    MAX_REPORT_CONTINUE_ATTEMPTS = 8
    _REPORT_IN_PROGRESS_RE = re.compile(
        r"stay tuned|hang tight|gathering|fetching|working on|"
        r"i'?ll get|let me|first,? i|on it|whip up|pull the|"
        r"now,? let|next,? i|still gathering|takes a moment",
        re.I,
    )

    def _is_report_request(self, query):
        q = (query or "").lower()
        if any(token in q for token in ("excel", "xlsx", "spreadsheet", "pdf", "report")):
            if any(w in q for w in ("generate", "export", "download", "create", "show", "regenerate", "recreate", "rebuild", "traffic")):
                return True
        if "report" in q and any(
            w in q for w in ("generate", "export", "download", "create", "regenerate", "recreate", "rebuild")
        ):
            return True
        if any(w in q for w in ("regenerate", "recreate", "re-run", "rerun")) and any(
            w in q for w in ("report", "excel", "same")
        ):
            return True
        if any(w in q for w in ("remove", "drop", "exclude", "add", "delete")) and any(
            w in q for w in ("column", "collumn", "header", "field")
        ):
            return True
        return False

    _DASHBOARD_CREATE_RE = re.compile(
        r"\b(create|build|generate|design|make|compose|draft|new)\b.{0,60}\bdashboard\b"
        r"|\bdashboard\b.{0,40}\b(create|build|generate|design|make)\b",
        re.I,
    )
    _IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
    _GUID_RE = re.compile(r"\{[0-9A-Fa-f]{8}-")
    _SPECIFIC_PANEL_RE = re.compile(
        r"\b(pie|donut|doughnut|table|chart|graph|list|tree|sankey)\b.{0,24}\b(of|for|showing)\b"
        r"|\b(of|for|showing)\b.{0,24}\b(pie|donut|table|chart|graph|list)\b",
        re.I,
    )
    _THEME_DASHBOARD_HINT = (
        "THEME DASHBOARD: The user named a theme, not specific modules, a counter group, "
        "or a key. Do NOT ask clarifying questions. Design an attractive dashboard with "
        "AT LEAST 6 modules: (1) 3-4 single-value badges (template 61, width 3 or 4) first, "
        "using Aggregates keys TOTALBW, DIR_INTOHOME, DIR_OUTOFHOME with different colors; "
        "(2) one toppers chart template 102 with surface PIE or DONUT; (3) another chart "
        "template 102 or 103 with surface LINE, STACKEDAREA, or SQUARESTACKEDAREA; "
        "(4) at least two toppers tables (template 3) on different dimensions of the theme; "
        "(5) if list_all_available_counter_groups returns a related crosskey (likely_crosskey, "
        "names with _X_ or _bx_ such as Hosts_X_Apps or FlowIntf_bx_Apps), add a sankey "
        "(110) or crosskey tree (109). Do NOT create a new crosskey just to fill that slot. "
        "Map the theme to live groups (host→Hosts, netflow→Flowgens/FlowIntfs, general→"
        "Aggregates+Hosts+Apps+Country). Set each module intent to the designed panel "
        "including the presentation word (badge/pie/donut/line/table/sankey). Keep names short."
    )
    _REALTIME_THEME_DASHBOARD_HINT = (
        "REALTIME THEME DASHBOARD: The user asked for a realtime/real-time/live dashboard "
        "without naming specific modules. Do NOT use retro templates 61, 3, 101, 102, or 103. "
        "Design at least 6 live stabber modules: (1) 3-4 real-time single-value badges "
        "(template 105, width 4) first, Aggregates TOTALBW / DIR_INTOHOME / DIR_OUTOFHOME, "
        "options counter_group+meter+probe=probe0, undecorated true; (2) at least one "
        "real-time key traffic chart (template 54, surface LINE) of named keys — resolve "
        "https/http/dns with search_keys on Apps, or chart Aggregates TOTALBW; (3) at least "
        "two real-time toppers lists (template 56) on different groups (Hosts, Apps, Country). "
        "There is no live pie/donut. Skip sankey unless the user asked. Put 'real time' in "
        "each module intent (e.g. 'real time total bandwidth badge', 'real time https chart', "
        "'real time top hosts list'). Query list_dashboard_module_types with "
        "'real time badge', 'real time chart', 'real time toppers'."
    )
    _REALTIME_DASHBOARD_HINT = (
        "REALTIME DASHBOARD: The user asked for realtime/live modules. Use template 105 "
        "for live KPIs, 54 for live named-key charts (surface LINE), and 56 for live "
        "toppers lists. Do not use 61, 3, 101, 102, or 103 on this dashboard."
    )

    def _is_dashboard_request(self, query: str) -> bool:
        q = (query or "").lower()
        if "dashboard" not in q:
            return False
        return bool(self._DASHBOARD_CREATE_RE.search(query or "")) or any(
            phrase in q for phrase in ("dashboard json", "dashboard package", "dashboard layout")
        )

    def _is_theme_dashboard_request(self, query: str) -> bool:
        """True when the user wants a dashboard but named no modules, CG, or key."""
        if not self._is_dashboard_request(query):
            return False
        q = query or ""
        lowered = q.lower()
        if self._IPV4_RE.search(q) or self._GUID_RE.search(q):
            return False
        if "counter group" in lowered or "cgguid" in lowered:
            return False
        specific_module_words = (
            "sankey", "flowmap", "flow map", "kpi", "badge", "single value",
            "template", "donut", "doughnut", "pie chart", "line chart", "area chart",
        )
        if any(word in lowered for word in specific_module_words):
            return False
        if self._SPECIFIC_PANEL_RE.search(q):
            return False
        if re.search(r"\btop\s+\d+\b", lowered) or "topper" in lowered:
            return False
        if re.search(r"\b(https?|dns|ssh)\b", lowered):
            return False
        return True

    def _query_has_explicit_realtime(self, query: str) -> bool:
        """True when the user asked for real time / realtime / live modules."""
        return bool(
            re.search(
                r"\b(?:real[\s-]?time|realtime|live(?:[\s-]?updating)?)\b",
                query or "",
                re.I,
            )
        )

    def _augment_query_with_hints(self, query: str) -> str:
        hints = []
        if self.pending_report_request:
            report_hints = self._report_query_hints(query)
            if report_hints:
                hints.append(report_hints)
        if self._is_theme_dashboard_request(query):
            if self._query_has_explicit_realtime(query):
                logging.info("[Client] Injecting realtime theme-dashboard layout hint")
                hints.append(self._REALTIME_THEME_DASHBOARD_HINT)
            else:
                logging.info("[Client] Injecting theme-dashboard layout hint")
                hints.append(self._THEME_DASHBOARD_HINT)
        elif self._is_dashboard_request(query) and self._query_has_explicit_realtime(query):
            logging.info("[Client] Injecting realtime dashboard template hint")
            hints.append(self._REALTIME_DASHBOARD_HINT)
        if not hints:
            return query
        return f"{query}\n\n[SYSTEM: {' '.join(hints)}]"

    def _report_query_hints(self, query: str) -> str:
        """Derive server-side report routing hints from natural language."""
        q = (query or "").lower()
        hints = []

        is_key_traffic = any(
            p in q for p in (
                "key traffic", "traffic for", "traffic of", "traffic over",
                "traffic trend", "each minute", "per minute", "timestamp",
                "time series", "timeseries",
            )
        ) or (
            any(p in q for p in ("https", "http", "dns", "ssh"))
            and "top" not in q
            and "topper" not in q
        )
        is_topper = bool(re.search(r"\btop\s+\d+\b", q)) or "topper" in q or "top " in q

        if is_key_traffic and not is_topper:
            hints.append(
                'REPORT ROUTE: intent="key_traffic", keys=[...], use generate_dynamic_report. '
                "Do NOT use source=topper or max_count. One row per time bucket (COUNTER_ITEM)."
            )
        elif is_topper:
            hints.append(
                'REPORT ROUTE: intent="topper", max_count=N, use generate_dynamic_report. '
                "Do NOT use intent=key_traffic."
            )

        if "pdf" in q:
            hints.append('output_format="pdf" on generate_dynamic_report.')

        from trisul_ai_cli.time_utils import extract_time_range_from_query

        time_range = extract_time_range_from_query(query)
        if time_range:
            start_raw, end_raw = time_range
            hints.append(
                f'TIME WINDOW: pass start_time="{start_raw}" and end_time="{end_raw}" to '
                "generate_dynamic_report exactly as written. Do NOT pass start_ts/end_ts — "
                "the server parses IST datetimes."
            )

        if is_topper and any(
            token in q for token in ("utilization", "util", "recv-util", "xmit-util")
        ):
            hints.append(
                'FLOWINTFS UTIL: meters=["Recv-Util","Xmit-Util"], sort_meter=4 '
                "(server infers these if omitted, but always pass for utilization reports)."
            )

        if "excel" in q or "xlsx" in q:
            hints.append('output_format="xlsx" (default).')

        is_column_edit = (
            any(w in q for w in ("remove", "drop", "exclude", "delete", "hide"))
            and any(w in q for w in ("column", "collumn", "field", "header"))
        ) or (
            any(w in q for w in ("add", "insert", "new"))
            and any(w in q for w in ("column", "collumn", "total"))
        ) or "sum of" in q or "sum_of" in q

        is_column_order = any(
            phrase in q
            for phrase in ("same order", "exact order", "column order", "in this order", "order i mentioned")
        )

        if is_column_order:
            hints.append(
                "COLUMN ORDER: put every column in the `columns` array in the exact order requested. "
                "For Total Utilization at a specific position, include it in `columns` with "
                "sum_of_meters on that entry. Do NOT also pass computed_columns for the same field."
            )

        if is_column_edit:
            hints.append(
                "COLUMN EDIT: re-call generate_dynamic_report with the same counter_group_guid, "
                "keys, and time window from the prior report. Use exclude_columns to drop columns "
                "and computed_columns with sum_of_meters to add totals. "
                "Do NOT call get_key_traffic_data or generate_excel_report with assembled rows."
            )

        return " ".join(hints)

    def _get_finish_reason(self, response):
        meta = getattr(response, "response_metadata", None) or {}
        return (
            meta.get("finish_reason")
            or meta.get("finishReason")
            or meta.get("stop_reason")
            or ""
        )

    TRUNCATED_FINISH_REASONS = frozenset({"max_tokens", "MAX_TOKENS", "length"})

    def _is_truncated_response(self, response) -> bool:
        return self._get_finish_reason(response) in self.TRUNCATED_FINISH_REASONS

    def _truncated_tool_call_id(self, response):
        """The id of the tool call whose arguments the token limit cut short, if any.

        Only the last block of a truncated completion can be incomplete; the ones
        before it were emitted in full and are still safe to execute.
        """
        if not response.tool_calls or not self._is_truncated_response(response):
            return None
        return response.tool_calls[-1]["id"]

    def _truncated_tool_call_error(self, function_name: str) -> str:
        return (
            f"Error: the arguments of this {function_name} call were cut off by the model's "
            "output token limit, so the call was NOT executed and nothing was written. "
            "The arguments you sent were incomplete — a required field is missing, not wrong. "
            "Retry with a smaller payload instead of resending the same one: keep names and "
            "descriptions short. For a dashboard where the user listed specific panels, send "
            "the 4-5 most important modules first, then extend it once this call succeeds. "
            "For a theme dashboard (no named modules/keys) still send at least 6 compact "
            "modules (KPI badges, pie/donut, line/area, tables, optional sankey)."
        )

    def _log_llm_response_stats(self, response, prefix: str):
        usage = getattr(response, "usage_metadata", None) or {}
        logging.info(
            f"{prefix} LLM response: finish_reason={self._get_finish_reason(response)!r}, "
            f"tool_calls={len(response.tool_calls or [])}, "
            f"output_tokens={usage.get('output_tokens')}"
        )
        if self._is_truncated_response(response):
            logging.warning(
                f"{prefix} LLM output hit the token limit — any trailing tool call is incomplete."
            )

    def _response_has_tool_issues(self, response):
        if getattr(response, "invalid_tool_calls", None):
            return True
        return self._get_finish_reason(response) in (
            "MALFORMED_FUNCTION_CALL",
            "RECITATION",
            "OTHER",
        )

    def _is_blank_response(self, content):
        return not (content or "").strip()

    def _should_continue_report_workflow(self, content):
        """Return True when a report was requested but not yet written to disk."""
        if self.verified_report_path:
            return False
        if not self.pending_report_request:
            return False
        if self._is_blank_response(content):
            return True
        if self.report_data_fetched:
            return True
        if content and self._REPORT_IN_PROGRESS_RE.search(content):
            return True
        return False

    def _should_retry_after_no_tools(self, response, content):
        if response.tool_calls:
            return False
        if self._response_has_tool_issues(response):
            return True
        if self._is_blank_response(content):
            if self._last_tool_had_connection_error():
                return False
            return True
        return self._should_continue_report_workflow(content)

    def _retry_message(self, response, content):
        reason = self._get_finish_reason(response)
        if reason == "MALFORMED_FUNCTION_CALL" or getattr(response, "invalid_tool_calls", None):
            return (
                "SYSTEM: Your previous tool call failed (malformed function call) and was NOT executed. "
                "Retry immediately. Reuse the data already fetched in this conversation — do not restart from scratch. "
                "Call generate_dynamic_excel_report for Trisul data (do NOT pass assembled rows). "
                "For non-Trisul tables use generate_excel_report with valid JSON: columns, rows, title, "
                "from_ts, to_ts, filename, sheet_name. Do NOT pass zmq_endpoint to generate_excel_report. "
                "Apply the header changes the user requested, then reply only after status success."
            )
        if self._is_blank_response(content):
            return (
                "SYSTEM: Your previous reply was empty. Continue the user's request now by calling "
                "the required tool(s). Do not return an empty response."
            )
        return self._report_continue_message()

    def _finalize_user_response(self, content, api_mode: bool = False):
        content = (content or "").strip()
        if self._is_blank_response(content) and self._last_tool_had_connection_error():
            endpoint_hint = self.zmq_endpoint or "tcp://<host>:<port>"
            content = (
                "Could not reach Trisul TRP. The report/data request failed because no "
                f"TRP endpoint responded (tried default local IPC if none was set).\n\n"
                f"Connect first, e.g. `connect to {endpoint_hint}`, then retry the report."
            )
        if self.verified_report_path and os.path.isfile(self.verified_report_path):
            if self.verified_report_path not in content:
                suffix = f"Excel report saved to `{self.verified_report_path}`."
                content = f"{content}\n\n{suffix}" if content else suffix
        if self.dashboard_path and os.path.isfile(self.dashboard_path):
            if api_mode and self.dashboard_json:
                content = self._sanitize_dashboard_answer_for_api(content)
            elif self.dashboard_path not in content:
                suffix = f"Dashboard package saved to `{self.dashboard_path}`."
                content = f"{content}\n\n{suffix}" if content else suffix
        if not content:
            logging.warning("[Client] Empty final response after agent loop")
            return (
                "Sorry, I couldn't complete that request — the model returned an empty response. "
                "Please try again."
            )
        return self._guard_report_path_claims(content)

    def _sanitize_dashboard_answer_for_api(self, content: str) -> str:
        """Remove server file paths from dashboard answers in Web UI / API mode."""
        sanitized = content or ""
        # Rewrite "…saved to /tmp/….json" into a clean "has been generated"
        sanitized = re.sub(
            r"(?i)(?:has\s+)?(?:been\s+)?(?:successfully\s+)?(?:generated\s+and\s+)?"
            r"saved\s+to\s*`?/tmp/[^\s`\"']+\.json`?",
            "has been generated",
            sanitized,
        )
        # Strip any remaining bare /tmp/...json paths
        sanitized = re.sub(r"`?/tmp/[^\s`\"']+\.json`?", "", sanitized, flags=re.IGNORECASE)
        # Drop how-to about Download / Install dashboard buttons or Admin > Packages
        sanitized = re.sub(
            r"(?is)(?:\n|^)#{1,6}\s*(?:📥\s*)?(?:How to Install|Next Steps)\s*\n"
            r"(?:.*?)(?=\n#{1,6}\s|\Z)",
            "\n",
            sanitized,
        )
        sanitized = re.sub(
            r"(?im)^[-*]\s+.*(?:Install dashboard|Admin\s*[→>\-]+\s*Packages).*\n?",
            "",
            sanitized,
        )
        sanitized = re.sub(
            r"(?i)[^\n]*?(?:Download and Install dashboard buttons|"
            r"Install dashboard buttons(?: already shown)?|"
            r"Admin\s*[→>\-]+\s*Packages\s*[→>\-]+\s*Install dashboard)[^\n]*\n?",
            "",
            sanitized,
        )
        sanitized = re.sub(r"[ \t]{2,}", " ", sanitized)
        sanitized = re.sub(r" +\.", ".", sanitized)
        sanitized = re.sub(r"\n{3,}", "\n\n", sanitized).strip()
        return sanitized or "The dashboard package has been generated."

    def _report_continue_message(self):
        return (
            "SYSTEM: The Excel/report request is NOT complete yet. "
            "Do NOT send progress messages like 'stay tuned' or 'hang tight'. "
            "For Trisul data use generate_dynamic_report with intent=key_traffic or intent=topper. "
            "For column changes use exclude_columns and computed_columns on generate_dynamic_report. "
            "Do NOT assemble rows from get_counter_group_topper/get_key_traffic_data. "
            "Do NOT use generate_excel_report for Trisul traffic. "
            "Call the report tool immediately. Only reply to the user after "
            '"status": "success" with a verified file_path.'
        )

    def _parse_tool_json(self, tool_result):
        clean = tool_result.replace("\n", "").replace("\r", "").replace("\t", " ").replace("   ", " ")
        try:
            return json.loads(clean), tool_result
        except Exception:
            return None, tool_result

    def _handle_report_tool_result(self, function_name, json_result, tool_result):
        """Verify report files exist on disk before telling the LLM generation succeeded."""
        if function_name not in self.REPORT_GENERATION_TOOLS:
            return tool_result, json_result

        if not json_result or json_result.get("status") != "success":
            return tool_result, json_result

        file_path = json_result.get("file_path")
        if file_path and os.path.isfile(file_path):
            verification = json_result.get("verification") or {}
            issues = verification.get("issues") or []
            duplicate_issues = [
                i for i in issues
                if i.startswith("duplicate_column")
            ]
            if duplicate_issues:
                error_payload = {
                    "status": "error",
                    "message": (
                        "Report has duplicate columns: "
                        f"{', '.join(duplicate_issues)}. "
                        "Put computed fields only once — in `columns` at the desired position "
                        "with sum_of_meters, OR in computed_columns to append at end, not both."
                    ),
                    "file_path": file_path,
                    "columns": json_result.get("columns"),
                    "verification": verification,
                }
                logging.warning(f"[Client] Report duplicate columns: {duplicate_issues}")
                return json.dumps(error_payload), error_payload

            self.report_path = file_path
            self.verified_report_path = file_path
            if verification and not verification.get("verified", True):
                logging.warning(
                    f"[Client] Report verification issues: {verification.get('issues')}"
                )
            data_type = json_result.get("data_type") or verification.get("data_type")
            if data_type:
                logging.info(f"[Client] Report data_type={data_type} verified={verification.get('verified')}")
            logging.info(f"[Client] Verified report file on disk: {file_path}")
            return tool_result, json_result

        error_payload = {
            "status": "error",
            "message": (
                f"Report file was NOT created at {file_path!r}. "
                "Do NOT tell the user the report was generated. "
                "You MUST call the report tool again with the assembled data."
            ),
            "file_path": None,
        }
        logging.warning(
            f"[Client] Report tool '{function_name}' reported success but file is missing: {file_path}"
        )
        return json.dumps(error_payload), error_payload

    def _record_dashboard_file(self, json_result, tool_result) -> None:
        """Remember where the generated dashboard package landed.

        The path is reported once, in the final assistant reply, so nothing is
        printed here. Also capture raw JSON for API/UI download.
        """
        if not json_result or json_result.get("status") != "success":
            logging.warning(
                f"[Client] [generate_dashboard_json] {json_result.get('message') if json_result else tool_result}"
            )
            return

        file_path = json_result.get("file_path")
        if not file_path or not os.path.isfile(file_path):
            logging.warning(f"[Client] Dashboard tool reported success but file is missing: {file_path}")
            return

        self.dashboard_path = file_path
        self.dashboard_filename = (
            json_result.get("filename")
            or Path(file_path).name
        )
        dashboard_json = json_result.get("dashboard_json")
        if not dashboard_json:
            try:
                dashboard_json = Path(file_path).read_text(encoding="utf-8")
            except OSError as exc:
                logging.warning(f"[Client] Could not read dashboard JSON from {file_path}: {exc}")
                dashboard_json = None
        elif not isinstance(dashboard_json, str):
            dashboard_json = json.dumps(dashboard_json)
        self.dashboard_json = dashboard_json
        logging.info(f"[Client] Dashboard package written to {file_path}")

    def _api_tool_result_for_dashboard(self, json_result, tool_result_text: str) -> str:
        """Rewrite dashboard tool output for the LLM in API/Web UI mode.

        Hide server paths and omit the raw JSON payload so the model does not
        print file paths or install how-to.
        """
        if not json_result or json_result.get("status") != "success":
            return tool_result_text

        sanitized = {
            k: v
            for k, v in json_result.items()
            if k not in ("dashboard_json", "file_path", "message_to_llm")
        }
        sanitized["message"] = "Dashboard package generated successfully."
        sanitized["message_to_llm"] = (
            "Tell the user the dashboard package was successfully generated. "
            "Do NOT mention any server file path, /tmp location, or filename path. "
            "Do NOT mention Download or Install dashboard buttons, how to install, "
            "or Admin > Packages > Install dashboard. "
            "List the modules that were included. Do not print the JSON contents."
        )
        return json.dumps(sanitized)

    def _open_report_file(self, file_path: str) -> bool:
        """Open a generated report with the OS default application."""
        if not file_path or not os.path.isfile(file_path):
            return False
        try:
            if os.name == "nt":
                os.startfile(file_path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(
                    ["open", file_path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                subprocess.Popen(
                    ["xdg-open", file_path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            logging.info(f"[Client] Opened report file: {file_path}")
            return True
        except Exception as exc:
            logging.warning(f"[Client] Failed to open report file {file_path}: {exc}")
            return False

    def _maybe_open_generated_report(self) -> None:
        """Open the verified report file after a CLI query completes."""
        if not self.auto_open_reports:
            return
        file_path = self.verified_report_path or self.report_path
        if file_path and os.path.isfile(file_path):
            self._open_report_file(file_path)

    def _guard_report_path_claims(self, content):
        """Append a warning if the LLM cites a report path that was never verified."""
        if not content:
            return content

        claimed_paths = re.findall(r'`(/tmp/[^`]+\.xlsx)`|(/tmp/\S+\.xlsx)', content)
        flat_paths = [p for pair in claimed_paths for p in pair if p]
        if not flat_paths:
            return content

        if self.verified_report_path and os.path.isfile(self.verified_report_path):
            for path in flat_paths:
                if path != self.verified_report_path and not os.path.isfile(path):
                    content = content.replace(path, self.verified_report_path)
            return content

        logging.warning("[Client] LLM claimed Excel path without verified report tool result")
        return (
            f"{content.rstrip()}\n\n"
            "Note: The file path above was not verified on disk — no Excel report was "
            "successfully created in this request. Fetching data alone does not create a file; "
            "the report generation step must complete successfully."
        )


    async def process_query(self, query: str) -> str:
        """Process a query using LangChain and MCP tools."""
        
        self.verified_report_path = None
        self.report_path = None
        self.dashboard_path = None
        self.dashboard_json = None
        self.dashboard_filename = None
        self.pending_report_request = self._is_report_request(query)
        self.report_data_fetched = False
        self.report_continue_attempts = 0
        user_content = self._augment_query_with_hints(query)
        self.conversation_history.append(HumanMessage(content=user_content))
        self._update_connection_from_text(query)
        self._sync_connection_from_history()

        llm = self.llm_factory.get_llm()
        if not llm:
             return "Error: API Key not set or LLM not initialized."

        tools = await self.get_mcp_tools()
        logging.info(f"[Client] [process_query] Tools: {tools}")
        llm_with_tools = llm.bind_tools(tools)

        iteration = 0
        
        try:
            while iteration < self.max_iterations:
                iteration += 1
                
                try:
                    response = await llm_with_tools.ainvoke(self.conversation_history)
                except Exception as e:
                    logging.error(f"[Client] LLM Error: {e}")
                    msg = self.extract_message(str(e))
                    return f"Error communicating with LLM: {msg}"
                
                self.conversation_history.append(response)
                self._log_llm_response_stats(response, "[Client]")
                
                if not response.tool_calls:
                    content = self.extract_text_from_content(response.content)
                    if self._should_retry_after_no_tools(response, content):
                        if self.report_continue_attempts >= self.MAX_REPORT_CONTINUE_ATTEMPTS:
                            logging.warning("[Client] Agent loop hit max retry attempts")
                            return self._finalize_user_response(content)
                        self.report_continue_attempts += 1
                        logging.info(
                            f"[Client] Incomplete/empty LLM response — auto-retrying "
                            f"(attempt {self.report_continue_attempts}, "
                            f"finish_reason={self._get_finish_reason(response)!r})"
                        )
                        self.conversation_history.append(
                            HumanMessage(content=self._retry_message(response, content))
                        )
                        continue
                    return self._finalize_user_response(content)
                
                # Process tool calls
                truncated_call_id = self._truncated_tool_call_id(response)
                for tool_call in response.tool_calls:
                    function_name = tool_call["name"]
                    function_args = self._normalize_tool_args(
                        function_name, tool_call["args"]
                    )
                    tool_call_id = tool_call["id"]
                    
                    if tool_call_id == truncated_call_id:
                        tool_result = self._truncated_tool_call_error(function_name)
                        logging.warning(f"[Client] {tool_result}")
                        self.conversation_history.append(ToolMessage(
                            content=tool_result,
                            tool_call_id=tool_call_id,
                            name=function_name
                        ))
                        continue
                    
                    logging.info(f"[Client] Calling function: {function_name} with args: {function_args}")
                    
                    if self.pending_report_request and function_name in self.REPORT_DATA_TOOLS:
                        self.report_data_fetched = True
                    
                    try:
                        # Call the tool on MCP server
                        result = await self.session.call_tool(function_name, function_args)
                        tool_result = result.content[0].text if result.content else "No result"
                        json_result, tool_result = self._parse_tool_json(tool_result)
                        clean_result = tool_result.replace("\n", "").replace("\r", "").replace("\t", " ").replace("   ", " ")
                        logging.info(f"[Client] Function result: {clean_result}")
                        
                        tool_result, json_result = self._handle_report_tool_result(
                            function_name, json_result, tool_result
                        )
                        
                        # Handle side effects
                        if function_name == "show_line_chart":
                            if json_result and json_result.get('status') == "success":
                                if json_result.get('file_path'):
                                    await self.utils.display_line_chart(function_args.get("data"), json_result['file_path'])
                                else:
                                    self.line_chart_data = function_args.get("data")
                            else:
                                logging.warning(f"[Client] [process_query] {json_result.get('message') if json_result else tool_result}")

                        if function_name == "show_pie_chart":
                            if json_result and json_result.get('status') == "success":
                                if json_result.get('file_path'):
                                    await self.utils.display_pie_chart(function_args.get("data"), json_result['file_path'])
                                else:
                                    self.pie_chart_data = function_args.get("data")
                            else:
                                logging.warning(f"[Client] [process_query] {json_result.get('message') if json_result else tool_result}")

                        if function_name == "show_table":
                            if json_result and json_result.get('status') == "success":
                                self.table_data = function_args.get("data")
                            else:
                                logging.warning(f"[Client] [process_query] {json_result.get('message') if json_result else tool_result}")

                        if function_name == "generate_trisul_report":
                            if not (json_result and json_result.get('status') == "success"):
                                logging.warning(f"[Client] [process_query] {json_result.get('message') if json_result else tool_result}")

                        if function_name == "generate_dashboard_json":
                            self._record_dashboard_file(json_result, tool_result)

                        if function_name == "configure_llm_model":
                            print("\033[F\033[K", end="")
                            new_model = self.set_llm_model()
                            tool_result = f'The LLM model version has been changed to {new_model}.'

                        if function_name == "configure_custom_llm":
                            print("\033[F\033[K", end="")
                            new_model = self.set_custom_llm()
                            tool_result = f'The custom LLM has been configured to use {new_model}.'

                        if function_name == "configure_embedding_model":
                            print("\033[F\033[K", end="")
                            new_model = self.set_embedding_model()
                            tool_result = f'The Embedding model version has been changed to {new_model}.'

                        if function_name == "configure_llm_api_key":
                            print("\033[F\033[K", end="")
                            self.set_api_key(provider_type="llm")
                            tool_result = "LLM API Key updated."

                        if function_name == "configure_embedding_api_key":
                            print("\033[F\033[K", end="")
                            self.set_api_key(provider_type="embedding")
                            tool_result = "Embedding API Key updated."

                        if function_name == "get_current_model_status":
                            tool_result = self.get_current_model_status()
                        
                        # Add tool output to history
                        self.conversation_history.append(ToolMessage(
                            content=tool_result,
                            tool_call_id=tool_call_id,
                            name=function_name
                        ))
                        
                    except Exception as e:
                        logging.error(f"[Client] Error calling function {function_name}: {e}")
                        self.conversation_history.append(ToolMessage(
                            content=f"Error: {str(e)}",
                            tool_call_id=tool_call_id,
                            name=function_name
                        ))
                
                # Loop continues to send tool outputs back to LLM
            
            return "Reached max iterations without final response"
        finally:
            self._maybe_open_generated_report()


    async def process_query_api(
        self, query: str, system_prompt: str = None, session_id: str = None, context_id: str = None
    ) -> dict:
        """Process a query for the REST API. Returns structured JSON data.
        
        Does NOT call any interactive/terminal methods.
        When context_id is provided (Web UI), all tool calls are locked to that context.
        """
        import time
        start_time = time.time()
        self.verified_report_path = None
        self.report_path = None
        self.dashboard_path = None
        self.dashboard_json = None
        self.dashboard_filename = None
        self.pending_report_request = self._is_report_request(query)
        self.report_data_fetched = False
        self.report_continue_attempts = 0
        prev_auto_open = self.auto_open_reports
        self.auto_open_reports = False
        try:
            return await self._process_query_api_body(
                query, system_prompt, session_id, start_time, context_id,
            )
        finally:
            self.auto_open_reports = prev_auto_open

    async def _process_query_api_body(
        self, query: str, system_prompt: str, session_id: str, start_time: float,
        context_id: str = None,
    ) -> dict:
        locked_context = self._resolve_locked_context(context_id)
        if locked_context:
            logging.info(f"[Client][API] [Session: {session_id}] Locked to context '{locked_context}'")

        if query.strip().lower() in ["exit", "quit"]:
            if session_id and session_id in self.sessions:
                logging.info(f"[Client][API] [Session: {session_id}] Exiting and cleaning up session history.")
                history = self.sessions.pop(session_id)
            else:
                logging.info(f"[Client][API] Exiting; using default history.")
                history = self.conversation_history
            
            await self.update_user_memory(history=history)
            return {
                "status": "success",
                "answer": "👋 Exiting gracefully... Session closed and memory updated.",
                "tool_calls": [],
                "chart_data": None,
                "table_data": None,
                "dashboard_json": None,
                "dashboard_filename": None,
            }

        if session_id and session_id in self.sessions:
            logging.info(f"[Client][API] [Session: {session_id}] Reusing existing session history.")
            history = self.sessions[session_id]
        else:
            logging.info(f"[Client][API] [Session: {session_id}] Starting new session.")
            if system_prompt:
                base_prompt = system_prompt
            else:
                base_prompt = self.conversation_history[0].content
            
            logging.info(f"[Client][API] [Session: {session_id}] System Prompt: {base_prompt[:100]}...")
            
            # Append API-specific instructions to omit tables and pop-up mentions in Web UI mode
            api_instructions = (
                "\n\n### 🌐 WEB UI / API MODE INSTRUCTIONS\n"
                "You are currently responding to a Web UI via API. In this mode:\n"
                "1. **STRICTLY OMIT ALL MARKDOWN/ASCII TABLES** from your final response text. The UI renders structured data natively via tools. Never display a table using text/pipes.\n"
                "2. **NO POP-UP MENTIONS**: Never mention 'pop-up windows', 'new windows', or 'checking a pop-up'. The visualizations are rendered directly inside the chat interface.\n"
                "3. **Tool Usage**: If the user asks for a 'table', 'toppers table', or similar tabular display, you **MUST** call the `show_table` tool. For charts, use `show_line_chart` or `show_pie_chart`.\n"
                "4. **Conciseness**: Provide a brief, friendly textual summary and let the tools handle the data visualization. Do not repeat data that is already shown in the table/chart unless for highlighting a specific point.\n"
                "5. **Ambiguous Matches**: For queries with multiple potential matches (e.g., 'Google', 'Shell', or ambiguous interface names), you **MUST** follow the **AUTO-SELECT AND SHOW TOP MATCH** workflow. Use the topper list to identify and display the most active candidate immediately, then list the other candidates as options. **Never ask for clarification as your first response if a topper query can resolve the ambiguity.**\n"
                "6. **STRICT GROUNDING**: You are FORBIDDEN from suggesting matches or options based on your internal knowledge. Every option you present MUST have been returned by a `search_keys` call or in existing traffic data. If a tool returns no matches, do not invent any.\n"
                "7. Ensure you still perform all necessary data calculations and grounding based on tool results.\n"
                "8. **Dashboard packages**: When `generate_dashboard_json` succeeds, NEVER mention any server file path, `/tmp/...`, or absolute filename path. "
                "Tell the user the dashboard was generated and list the modules. "
                "Do NOT mention Download or Install dashboard buttons, how to install, or Admin → Packages → Install dashboard. "
                "Do not print the JSON contents."
            )
            if locked_context:
                api_instructions += self._locked_context_instructions(locked_context)
            
            history = [SystemMessage(content=base_prompt + api_instructions)]
            
            if session_id:
                self.sessions[session_id] = history

        if locked_context:
            self._ensure_locked_context_prompt(history, locked_context)
        
        logging.info(f"[Client][API] [Session: {session_id}] User Query: {query}")
        user_content = self._augment_query_with_hints(query)
        other_contexts = self._mentioned_other_contexts(query, locked_context) if locked_context else []
        if other_contexts:
            mentioned = ", ".join(other_contexts)
            lock_note = (
                f"[SYSTEM: The user mentioned context(s) {mentioned}. "
                f"This session is locked to {locked_context}. Do not use the mentioned "
                f"context(s). Tell the user they must switch to that context in the "
                f"WebTrisul UI and open the Trisul AI chat window from there.]"
            )
            if user_content == query:
                user_content = f"{query}\n\n{lock_note}"
            else:
                user_content = f"{user_content}\n{lock_note}"
            logging.info(
                f"[Client][API] [Session: {session_id}] Ignored mentioned context(s): {mentioned}"
            )
        history.append(HumanMessage(content=user_content))
        if not locked_context:
            self._update_connection_from_text(query)

        llm = self.llm_factory.get_llm()
        if not llm:
            return {
                "status": "error",
                "message": "LLM not initialized. Please set API key via CLI first.",
                "answer": None,
                "tool_calls": [],
                "chart_data": None,
                "dashboard_json": None,
                "dashboard_filename": None,
            }

        tools = await self.get_mcp_tools()
        llm_with_tools = llm.bind_tools(tools)

        iteration = 0
        collected_tool_calls = []
        chart_data = None
        table_data = None

        while iteration < self.max_iterations:
            iteration += 1
            logging.info(f"[Client][API] [Session: {session_id}] LLM iteration {iteration}...")

            try:
                response = await llm_with_tools.ainvoke(history)
            except Exception as e:
                logging.error(f"[Client][API] LLM Error: {e}")
                return {
                    "status": "error",
                    "message": self.extract_message(str(e)),
                    "answer": None,
                    "tool_calls": collected_tool_calls,
                    "chart_data": chart_data,
                    "table_data": table_data,
                    "dashboard_json": self.dashboard_json,
                    "dashboard_filename": self.dashboard_filename,
                }

            history.append(response)
            self._log_llm_response_stats(response, f"[Client][API] [Session: {session_id}]")

            if not response.tool_calls:
                answer = self.extract_text_from_content(response.content)
                if self._should_retry_after_no_tools(response, answer):
                    if self.report_continue_attempts >= self.MAX_REPORT_CONTINUE_ATTEMPTS:
                        logging.warning("[Client][API] Agent loop hit max retry attempts")
                        answer = self._finalize_user_response(answer, api_mode=True)
                    else:
                        self.report_continue_attempts += 1
                        logging.info(
                            f"[Client][API] Incomplete/empty LLM response — auto-retrying "
                            f"(attempt {self.report_continue_attempts})"
                        )
                        history.append(HumanMessage(content=self._retry_message(response, answer)))
                        continue
                else:
                    answer = self._finalize_user_response(answer, api_mode=True)
                if other_contexts and "webtrisul" not in (answer or "").lower():
                    switch_msg = self._locked_context_switch_message(locked_context, other_contexts[0])
                    answer = f"{switch_msg}\n\n{answer}" if answer else switch_msg
                elapsed = time.time() - start_time
                logging.info(f"[Client][API] [Session: {session_id}] Final AI Response (took {elapsed:.2f}s): {answer}")
                return {
                    "status": "success",
                    "answer": answer,
                    "tool_calls": collected_tool_calls,
                    "chart_data": chart_data,
                    "table_data": table_data,
                    "dashboard_json": self.dashboard_json,
                    "dashboard_filename": self.dashboard_filename,
                }

            # Process tool calls
            truncated_call_id = self._truncated_tool_call_id(response)
            for tool_call in response.tool_calls:
                function_name = tool_call["name"]
                original_args = tool_call["args"]
                lock_notice = self._locked_context_override_notice(
                    function_name, original_args, locked_context
                )
                function_args = self._normalize_tool_args(
                    function_name, original_args, locked_context=locked_context
                )
                tool_call_id = tool_call["id"]

                if tool_call_id == truncated_call_id:
                    tool_result = self._truncated_tool_call_error(function_name)
                    logging.warning(f"[Client][API] [Session: {session_id}] {tool_result}")
                    history.append(ToolMessage(
                        content=tool_result,
                        tool_call_id=tool_call_id,
                        name=function_name
                    ))
                    collected_tool_calls.append({
                        "tool": function_name,
                        "args": function_args,
                        "result": {"error": tool_result},
                    })
                    continue

                # Skip interactive-only tools in API mode
                if function_name in (
                    "configure_llm_model",
                    "configure_custom_llm",
                    "configure_embedding_model",
                    "configure_llm_api_key",
                    "configure_embedding_api_key",
                ):
                    tool_result = "This operation is only available in the CLI interface."
                    logging.info(f"[Client][API] [Session: {session_id}] Tool: {function_name} | Args: {json.dumps(function_args)}")
                    logging.info(f"[Client][API] [Session: {session_id}] Tool: {function_name} | Output: {tool_result}")
                    history.append(ToolMessage(
                        content=tool_result,
                        tool_call_id=tool_call_id,
                        name=function_name
                    ))
                    collected_tool_calls.append({
                        "tool": function_name,
                        "args": function_args,
                        "result": {"message": tool_result},
                    })
                    continue

                logging.info(f"[Client][API] [Session: {session_id}] Tool: {function_name} | Args: {json.dumps(function_args)}")

                if self.pending_report_request and function_name in self.REPORT_DATA_TOOLS:
                    self.report_data_fetched = True

                try:
                    result = await self.session.call_tool(function_name, function_args)
                    tool_result_text = result.content[0].text if result.content else "No result"
                    logging.info(f"[Client][API] [Session: {session_id}] Tool: {function_name} | Output: {tool_result_text}")

                    tool_result_json, tool_result_text = self._parse_tool_json(tool_result_text)
                    tool_result_text, tool_result_json = self._handle_report_tool_result(
                        function_name, tool_result_json, tool_result_text
                    )

                    if function_name == "generate_dashboard_json":
                        self._record_dashboard_file(tool_result_json, tool_result_text)
                        tool_result_text = self._api_tool_result_for_dashboard(
                            tool_result_json, tool_result_text
                        )
                        # Keep a lean result for the API tool_calls payload (no huge JSON)
                        if isinstance(tool_result_json, dict) and tool_result_json.get("status") == "success":
                            tool_result_json = {
                                k: v
                                for k, v in tool_result_json.items()
                                if k != "dashboard_json"
                            }

                    # Capture chart data for API consumers
                    if function_name in ["show_line_chart", "show_pie_chart"]:
                        chart_type = "line" if function_name == "show_line_chart" else "pie"
                        raw_data = function_args.get("data")
                        
                        # If it's a string, try to parse it as JSON
                        if isinstance(raw_data, str):
                            try:
                                parsed_data = json.loads(raw_data)
                            except Exception:
                                parsed_data = raw_data
                        else:
                            parsed_data = raw_data
                            
                        chart_data = {"type": chart_type, "data": parsed_data}
                        logging.info(f"[Client][API] [Session: {session_id}] Captured {chart_type} chart data.")

                    # Capture table data for API consumers
                    if function_name == "show_table":
                        raw_data = function_args.get("data")
                        if isinstance(raw_data, str):
                            try:
                                table_data = json.loads(raw_data)
                            except Exception:
                                table_data = raw_data
                        else:
                            table_data = raw_data
                        logging.info(f"[Client][API] [Session: {session_id}] Captured table data.")

                    # Handle get_current_model_status specially
                    if function_name == "get_current_model_status":
                        tool_result_json = self.get_current_model_status()
                        tool_result_text = json.dumps(tool_result_json)

                    if lock_notice:
                        logging.info(f"[Client][API] [Session: {session_id}] {lock_notice}")
                        tool_result_text = f"{tool_result_text}\n\n{lock_notice}"

                    collected_tool_calls.append({
                        "tool": function_name,
                        "args": function_args,
                        "result": tool_result_json,
                    })

                    history.append(ToolMessage(
                        content=tool_result_text,
                        tool_call_id=tool_call_id,
                        name=function_name
                    ))

                except Exception as e:
                    logging.error(f"[Client][API] Error calling tool {function_name}: {e}")
                    err_msg = str(e)
                    collected_tool_calls.append({
                        "tool": function_name,
                        "args": function_args,
                        "result": {"error": err_msg},
                    })
                    history.append(ToolMessage(
                        content=f"Error: {err_msg}",
                        tool_call_id=tool_call_id,
                        name=function_name
                    ))

        return {
            "status": "error",
            "message": "Reached max iterations without a final response.",
            "answer": None,
            "tool_calls": collected_tool_calls,
            "chart_data": chart_data,
            "table_data": table_data,
            "dashboard_json": self.dashboard_json,
            "dashboard_filename": self.dashboard_filename,
        }





    async def update_user_memory(self, history=None):
        if history is None:
            history = self.conversation_history
        logging.info(f"[Client] [ai_memory] Updating user memory. Existing memory: \n {self.existing_ai_memory}")
        
        filtered_conversation = []

        for msg in history:
            if isinstance(msg, HumanMessage):
                filtered_conversation.append({"user": msg.content})
            elif isinstance(msg, AIMessage):
                # Extract text from AIMessage content
                content = self.extract_text_from_content(msg.content)
                filtered_conversation.append({"model": content})


        # Load update memory system prompt
        system_prompt_path = self.root_dir / "prompts/system_memory_update.txt"
        template = system_prompt_path.read_text(encoding="utf-8")
        update_memory_system_prompt = template.format(
            confidence_threshold=self.confidence_threshold,
            existing_ai_memory=self.existing_ai_memory,
            filtered_conversation=filtered_conversation
        )

        llm = self.llm_factory.get_llm()
        if not llm:
             logging.error("[Client] [ai_memory] LLM not initialized")
             return

        try:
            logging.info("[Client] [ai_memory] Sending update request to LLM.")
            response = await llm.ainvoke([HumanMessage(content=update_memory_system_prompt)])
            
            # Extract text from response content
            new_ai_memory_text = self.extract_text_from_content(response.content)
            
            new_ai_memory = json.loads(re.sub(r'```json|```', '', new_ai_memory_text).strip())
            logging.info("[Client] [ai_memory] Received updated memory from LLM")
            
            
            with open(self.memory_json_path, "w", encoding="utf-8") as file:
                json.dump(new_ai_memory, file, indent=4)
            
            self.existing_ai_memory = new_ai_memory
            logging.info(f"[Client] [ai_memory] New memory updated : \n {new_ai_memory}")
            
        except Exception as e:
            logging.error(f"[Client] [ai_memory] Error updating memory: {e}")



    async def loading_animation(self, task, message):
        spinner = ["⢄", "⢂", "⢁", "⡁", "⡈", "⡐", "⡠"]
        i = 0
        print("")
        
        while not task.done():
            sys.stdout.write(f"\r✨ {message} {f'{spinner[i % len(spinner)]}  '}")
            sys.stdout.flush()
            i += 1
            await asyncio.sleep(0.1)
        sys.stdout.write("\r" + " " * 40 + "\r")
        sys.stdout.write("\033[F")
        sys.stdout.write("\r\033[K")
        
        


    async def cleanup(self):
        try:
            if self.exit_stack:
                await self.exit_stack.aclose()
        except Exception as e:
            logging.error(f"[Client] Error during cleanup: {e}")
        await asyncio.sleep(0.5)



    async def main(self):
        # Connect to server
        await self.connect_to_server("trisul_ai_cli.server")

        print("\033[1;36m" + "╔══════════════════════════════════════════════════════════════╗")
        print("║  🚀  Trisul AI CLI - Because your network should talk back.  ║")
        print("║                                                              ║")
        print("║  💡  Type 'exit' or 'quit' to close the CLI                  ║")
        print("║                                                              ║")
        print(f"║  📦  Version: {version('trisul_ai_cli')}                                          ║")
        print("╚══════════════════════════════════════════════════════════════╝" + "\033[0m")
        
        # verify model and api key
        self.get_api_key()
        
        try:
            while True:
                query = input("👤 (You) : ").strip()
                
                # skip empty inputs
                if not query:
                    continue
                else:
                    logging.info(f"[Client] Query: {query}")
                
                # Exit
                if query.lower() in ["exit", "quit"]:
                    task = asyncio.create_task(self.update_user_memory())
                    spinner = asyncio.create_task(self.loading_animation(task,"Adapting to your world"))
                    await task
                    await spinner
                    
                    logging.info("[Client] Bye!")
                    print("\n🤖 (Bot) : 👋 Bye!")
                    break

                
                # change the llm api key
                if query.lower() == "change_llm_api_key":
                    self.set_api_key(provider_type="llm")
                    continue

                # change the embedding api key
                if query.lower() == "change_embedding_api_key":
                    self.set_api_key(provider_type="embedding")
                    continue
                
                # change llm model
                if query.lower() == "change_llm_model":
                    self.set_llm_model()
                    continue

                # configure local/custom llm
                if query.lower() in ("change_custom_llm", "configure_custom_llm"):
                    self.set_custom_llm()
                    continue

                # change embedding model
                if query.lower() == "change_embedding_model":
                    self.set_embedding_model()
                    continue
                
                try:
                    # process the query                
                    task = asyncio.create_task(self.process_query(query))
                    spinner = asyncio.create_task(self.loading_animation(task,"Thinking"))
                    response = await task
                    await spinner
                    
                    # logging.info(f"[Client] Full Conversation History: \n{self.conversation_history[1:]}")
                    logging.info("[Client] Full Conversation History:\n%s", json.dumps([msg.model_dump() for msg in self.conversation_history[1:]], indent=2, default=str))
                    logging.info(f"[Client] Response: \n{response}")
                    print(f"\n🤖 (Bot) : {response.strip()}\n")
                    
                    # If a chart data was prepared, display it and reset the chart data
                    if(self.line_chart_data):
                        await self.utils.display_line_chart(self.line_chart_data)
                        self.line_chart_data = {}
                    
                    if(self.pie_chart_data):
                        await self.utils.display_pie_chart(self.pie_chart_data)
                        self.pie_chart_data = {}
                    
                    if(self.table_data):
                        self.table_data = {}
                    
                        
                        
                except Exception as e:
                    logging.error(f"[Client] Error: {e}")
                    print(f"\n🤖 (Bot) : {self.extract_message(str(e))}")
                    print("\n👋 Exiting gracefully...")
                    return

        except KeyboardInterrupt:
            logging.info("[Client] Exiting gracefully...")
            print("\n👋 Exiting gracefully...")
            return

        finally:
            # Always clean up async resources
            await self.cleanup()
            # Give ZeroMQ sockets time to close cleanly
            await asyncio.sleep(0.1)
            return

if __name__ == "__main__":
    try:
        asyncio.run(TrisulAIClient().main())
    except KeyboardInterrupt:
        logging.info("[Client] Exiting gracefully ...")
        print("\n👋 Exiting gracefully ...")
    
    

