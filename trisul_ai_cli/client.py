import warnings
warnings.filterwarnings("ignore",category=FutureWarning,module="google.api_core")

import asyncio
import json
from contextlib import AsyncExitStack
from typing import Any, Dict, List
import nest_asyncio
import sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import logging
from dotenv import set_key, dotenv_values
from pathlib import Path
import os
import readline
from importlib.metadata import version
import re
import subprocess
from trisul_ai_cli.tools.utils import TrisulAIUtils
from trisul_ai_cli.llm_factory import LLMFactory
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage
import json
import stdiomask




class TrisulAIClient:
    def __init__(self):
        # Initialize asyncio
        nest_asyncio.apply()
        os.environ["QT_QPA_PLATFORM"] = "xcb"
        
        # Initialize logging
        logging.basicConfig(
            filename= Path(os.getcwd()) / "trisul_ai_cli.log",
            level=logging.INFO,
            format="%(asctime)s - %(levelname)s - %(message)s"
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
        with open(self.memory_json_path, "r") as file:
            self.existing_ai_memory = json.load(file)
        self.confidence_threshold = 90
        self.line_chart_data = {}
        self.pie_chart_data = {}
        self.report_path = None
        self.max_iterations = 15
                
        
        # Load main system prompt
        system_prompt_path = self.root_dir / "prompts/system_main.txt"
        template = system_prompt_path.read_text()
        main_system_prompt = template.format(
            existing_ai_memory=self.existing_ai_memory
        )
    
        self.conversation_history = [
            SystemMessage(content=main_system_prompt)
        ]



    # Set your API key here
    def set_api_key(self, provider_type: str = "llm"):
        try:
            print("\033[F\033[K", end="")
            
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
                
            print("")
            logging.info(f"[Client] API Key set successfully for {provider} ({provider_type}).")
            
        except KeyboardInterrupt:
            print("\n\n🤖 (Bot) : API Key entry cancelled by user.\n")
            logging.info("[Client] API Key entry cancelled by user.")
            sys.exit(0)


    # Get your API key here
    def get_api_key(self) -> str:
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

            selected_index = None
            while True:
                choice = input(f"\n🤖 (Bot) : Enter your choice (1-{len(flat_list)}): ").strip()
                if not choice.isdigit():
                    print("\n🤖 (Bot) : ❌ Invalid choice. Please enter a number.")
                    continue
                idx = int(choice)
                if 1 <= idx <= len(flat_list):
                    selected_index = idx - 1
                    break
                else:
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

            return selected_model
        except KeyboardInterrupt:
            print("\n\n🤖 (Bot) : Model Selection cancelled by user.")
            logging.info("[Client] Model Selection cancelled by user.")
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
            print(f"🤖 (Bot) : Embedding model set to {emb_model} ({emb_prov})")

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

        with env_file.open("r") as f:
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
            command="python3",
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
            # Convert to OpenAI function format which is widely supported by LangChain bind_tools
            tool_list.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.inputSchema,
                }
            })
        return tool_list


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
            text_parts = []
            for item in content:
                if isinstance(item, dict) and 'text' in item:
                    text_parts.append(item['text'])
                else:
                    text_parts.append(str(item))
            return '\n'.join(text_parts)
        
        return str(content)


    async def process_query(self, query: str) -> str:
        """Process a query using LangChain and MCP tools."""
        
        self.conversation_history.append(HumanMessage(content=query))

        llm = self.llm_factory.get_llm()
        if not llm:
             return "Error: API Key not set or LLM not initialized."

        tools = await self.get_mcp_tools()
        llm_with_tools = llm.bind_tools(tools)

        iteration = 0
        
        while iteration < self.max_iterations:
            iteration += 1
            
            try:
                response = await llm_with_tools.ainvoke(self.conversation_history)
            except Exception as e:
                logging.error(f"[Client] LLM Error: {e}")
                msg = self.extract_message(str(e))
                return f"Error communicating with LLM: {msg}"
            
            self.conversation_history.append(response)
            
            if not response.tool_calls:
                # Handle both string and list responses
                content = self.extract_text_from_content(response.content)
                return content
            
            # Process tool calls
            for tool_call in response.tool_calls:
                function_name = tool_call["name"]
                function_args = tool_call["args"]
                tool_call_id = tool_call["id"]
                
                logging.info(f"[Client] Calling function: {function_name} with args: {function_args}")
                
                try:
                    # Call the tool on MCP server
                    result = await self.session.call_tool(function_name, function_args)
                    tool_result = result.content[0].text if result.content else "No result"
                    clean_result = tool_result.replace("\n", "").replace("\r", "").replace("\t", " ").replace("   ", "")
                    logging.info(f"[Client] Function result: {clean_result}")
                    
                    # Parse JSON if possible for side effects
                    json_result = None
                    try:
                        json_result = json.loads(clean_result)
                    except Exception:
                        pass
                    
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

                    if function_name == "generate_trisul_report":
                        if json_result and json_result.get('status') == "success":
                            self.report_path = json_result.get('file_path')
                        else:
                            logging.warning(f"[Client] [process_query] {json_result.get('message') if json_result else tool_result}")

                    if function_name == "configure_llm_model":
                        print("\033[F\033[K", end="")
                        new_model = self.set_llm_model()
                        tool_result = f'The LLM model version has been changed to {new_model}.'

                    if function_name == "configure_embedding_model":
                        print("\033[F\033[K", end="")
                        new_model = self.set_embedding_model()
                        tool_result = f'The Embedding model version has been changed to {new_model}.'

                    if function_name == "configure_llm_api_key":
                        self.set_api_key(provider_type="llm")
                        tool_result = "LLM API Key updated."

                    if function_name == "configure_embedding_api_key":
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





    async def update_user_memory(self):
        logging.info(f"[Client] [ai_memory] Updating user memory. Existing memory: \n {self.existing_ai_memory}")
        
        filtered_conversation = []

        for msg in self.conversation_history:
            if isinstance(msg, HumanMessage):
                filtered_conversation.append({"user": msg.content})
            elif isinstance(msg, AIMessage):
                # Extract text from AIMessage content
                content = self.extract_text_from_content(msg.content)
                filtered_conversation.append({"model": content})


        # Load update memory system prompt
        system_prompt_path = self.root_dir / "prompts/system_memory_update.txt"
        template = system_prompt_path.read_text()
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
            
            
            with open(self.memory_json_path, "w") as file:
                json.dump(new_ai_memory, file, indent=4)
            
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
        await self.exit_stack.aclose()



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
                    new_model = self.set_llm_model()
                    print(f"🤖 (Bot) : LLM Model changed to {new_model}\n")
                    continue

                # change embedding model
                if query.lower() == "change_embedding_model":
                    new_model = self.set_embedding_model()
                    print(f"🤖 (Bot) : Embedding Model changed to {new_model}\n")
                    continue
                
                try:
                    # process the query                
                    task = asyncio.create_task(self.process_query(query))
                    spinner = asyncio.create_task(self.loading_animation(task,"Thinking"))
                    response = await task
                    await spinner
                    
                    logging.info(f"[Client] Full Conversation History: \n{self.conversation_history[1:]}")
                    logging.info(f"[Client] Response: \n{response}")
                    print(f"\n🤖 (Bot) : {response.strip()}\n")
                    
                    # If a chart data was prepared, display it and reset the chart data
                    if(self.line_chart_data):
                        await self.utils.display_line_chart(self.line_chart_data)
                        self.line_chart_data = {}
                    
                    if(self.pie_chart_data):
                        await self.utils.display_pie_chart(self.pie_chart_data)
                        self.pie_chart_data = {}
                    
                    # If a report was prepared, display it and reset the report path
                    if(self.report_path):                    
                        if os.name == "nt":
                            os.startfile(self.report_path)
                        elif sys.platform == "darwin":
                            subprocess.Popen(["open", self.report_path])
                        else:
                            subprocess.Popen(["xdg-open", self.report_path])
                        self.report_path = None

                        
                        
                except Exception as e:
                    logging.error(f"[Client] Error: {e}")
                    logging.info("[Client] Exiting gracefully...")
                    await asyncio.sleep(0.5)
                    print()
                    print(f"\n🤖 (Bot) : {self.extract_message(str(e))}")
                    print("\n👋 Exiting gracefully...")
                    sys.exit(0)

        except KeyboardInterrupt:
            logging.info("[Client] Exiting gracefully...")
            print("\n👋 Exiting gracefully...")
            sys.exit(0)

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
    
    

