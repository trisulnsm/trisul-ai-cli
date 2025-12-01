import asyncio
import json
from contextlib import AsyncExitStack
from typing import Any, Dict, List
import nest_asyncio
import sys
import httpx
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
        self.utils = TrisulAIUtils(logging=logging)
        
        # Initialize MCP session
        self.session: ClientSession = None
        self.exit_stack = AsyncExitStack()
        self.stdio = None
        self.write = None
        
        # Initialize Global variables
        self.GEMINI_MODEL = "gemini-2.5-flash"
        self.GEMINI_API_KEY = None
        self.GEMINI_URL = None
        self.existing_ai_memory = []
        self.root_dir = Path(__file__).resolve().parent
        self.env_path = self.root_dir / ".env"
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
            {
                "role": "user",
                "parts": [
                    {
                        "text": main_system_prompt
                    }
                ]
            }
        ]



    # Set your API key here
    def set_api_key(self):
        try:
            print("\033[F\033[K", end="")
            while True:
                api_key = input("\n🤖 (Bot) : Enter your Gemini API Key : ").strip()
                if api_key:
                    break
                print("\n🤖 (Bot) : API key cannot be empty. Please try again.")
            if not self.env_path.exists():
                self.env_path.touch()
            set_key(self.env_path, "TRISUL_GEMINI_API_KEY", api_key)
            self.get_api_key()
            print("")
            logging.info("[Client] API Key set successfully.")
            
        except KeyboardInterrupt:
            print("\n\n🤖 (Bot) : API Key entry cancelled by user.\n")
            logging.info("[Client] API Key entry cancelled by user.")
            sys.exit(0)


    # Get your API key here
    def get_api_key(self) -> str:
        config = dotenv_values(self.env_path)
        self.GEMINI_API_KEY = config.get("TRISUL_GEMINI_API_KEY")
        self.GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{self.GEMINI_MODEL}:generateContent?key={self.GEMINI_API_KEY}"
        
        if not self.GEMINI_API_KEY:
            self.set_api_key()


    # Get your model version here
    def get_model_version(self):
        config = dotenv_values(self.env_path)
        gemini_version = config.get("TRISUL_GEMINI_MODEL")
        if gemini_version:
            self.GEMINI_MODEL = gemini_version
            self.GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{self.GEMINI_MODEL}:generateContent?key={self.GEMINI_API_KEY}"



    # Change the Gemini model version
    def set_model_version(self):        
        try:
            model_versions = ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash", "gemini-2.0-flash-lite"]
            
            print("\033[F\033[K", end="")
            print("\n🤖 (Bot) : Select a Gemini model version from the list below: \n")

            print("Available Gemini Models 📜\n---------------------------")
            for idx, version in enumerate(model_versions, start=1):
                print(f"{idx}) {version} {' (current version)' if version == self.GEMINI_MODEL else ''}")

            selected_model = self.GEMINI_MODEL
            while True:
                choice = input("\n🤖 (Bot) : Enter your choice (1-5): ").strip()

                if not choice.isdigit():
                    print("\n🤖 (Bot) : ❌ Invalid choice. Try again.")
                    continue

                idx = int(choice)
                if 1 <= idx <= len(model_versions):
                    selected_model = model_versions[idx - 1]
                    self.GEMINI_MODEL = selected_model
                    
                    set_key(self.env_path, "TRISUL_GEMINI_MODEL", self.GEMINI_MODEL)
                    self.get_model_version()
                    logging.info(f"[Client] Model version changed to: {self.GEMINI_MODEL}")
                    break

                print("\n🤖 (Bot) : ❌ Invalid choice. Try again.")
            
            print("")
            return selected_model
        
        except KeyboardInterrupt:
            print("\n\n🤖 (Bot) : Model Selection cancelled by user.\n")
            logging.info("[Client] Model Selection cancelled by user.")
            sys.exit(0)



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
            # Convert MCP tool schema to Gemini function declaration format
            tool_list.append({
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.inputSchema,
            })

        return tool_list



    async def call_gemini_rest(self) -> Dict:
        """Send a query to Gemini REST API with tools."""
        tools = await self.get_mcp_tools()
        
        # Build conversation contents

        # Convert MCP tools to Gemini function declarations
        function_declarations = []
        for tool in tools:
            function_declarations.append({
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["parameters"]
            })

        payload = {
            "contents": self.conversation_history,
            "tools": [{"functionDeclarations": function_declarations}] if function_declarations else [],
            "toolConfig": {
                "functionCallingConfig": {
                    "mode": "AUTO"
                }
            }
        }

        # Use only URL parameter authentication (remove Authorization header)
        headers = {
            "Content-Type": "application/json",
        }

        logging.info(f"[Client] Sending request to Gemini at {self.GEMINI_URL.split('?')[0]}.")

        max_retries = 5
        retry_count = 0
        base_delay = 2  # Start with 2 seconds
        
        async with httpx.AsyncClient(timeout=60.0) as client:
            while retry_count < max_retries:
                resp = await client.post(self.GEMINI_URL, headers=headers, json=payload)
                
                # Handle model overload (503) - retry with exponential backoff
                if resp.status_code == 503:
                    retry_count += 1
                    if retry_count < max_retries:
                        delay = base_delay * (2 ** (retry_count - 1))  # Exponential backoff: 2, 4, 8, 16 seconds
                        logging.warning(f"[Client] Model overloaded (503). Retry {retry_count}/{max_retries} after {delay}s...")
                        await asyncio.sleep(delay)
                        continue
                    else:
                        # Max retries reached
                        logging.error(f"[Client] Model overloaded after {max_retries} attempts. Giving up.")
                        logging.error(resp.json())
                        sys.stdout.write("\033[F")
                        print(f"\n🤖 (Bot) : Model is overloaded. Please try again later.\n\n")
                        sys.exit()
                
                # Handle other errors
                if resp.status_code != 200:
                    logging.error(resp.json())
                    logging.info(f"[Client] Full Conversation History: \n{json.dumps(self.conversation_history[2:], indent=2)}")
                    
                    # Remove the last loading line from console 
                    sys.stdout.write("\033[F")
                    
                    # print the error message from Gemini
                    print(f"\n🤖 (Bot) : {resp.json()['error']['message']}\n\n")
                    sys.exit()
                
                # Success - break out of retry loop
                break
                
            return resp.json()



    async def process_query(self, query: str) -> str:
        """Process a query using Gemini REST API and MCP tools."""
        
        self.conversation_history.append({
            "role": "user", 
            "parts": [{"text": query}]
        })

        # Keep calling Gemini until no more function calls are needed
        iteration = 0
        
        while iteration < self.max_iterations:
            iteration += 1
            
            # Call Gemini
            gemini_response = await self.call_gemini_rest()
            
            if not gemini_response.get("candidates"):
                return "No response from Gemini"
                        
            candidate = gemini_response["candidates"][0]
            content = candidate.get("content", {})
            parts = content.get("parts", [])
            
            
            if not parts:
                logging.warning("[Client] Empty model response detected")
                # Force Gemini to continue
                self.conversation_history.append({
                    "role": "user",
                    "parts": [{
                        "text": "Please provide a summary of the data and complete the chart generation or call the next tool to continue if requested."
                    }]
                })
                continue 
            
            
            # Add Gemini's response to conversation history
            self.conversation_history.append({
                "role": "model",
                "parts": parts
            })
            
            # Check if Gemini wants to call any functions
            function_calls = [part for part in parts if "functionCall" in part]
            
            if not function_calls:
                # No more function calls - return the text response
                text_parts = [part["text"] for part in parts if "text" in part]
                final_text = " ".join(text_parts) if text_parts else "No text response\n"
                logging.info(f"[Client] Final response after {iteration} iterations:")
                return final_text

            
            self.conversation_history.append({
                "role": "function",
                "parts": []
            })
            
            
            # Process each function call in this response
            for func_call_part in function_calls:
                func_call = func_call_part["functionCall"]
                function_name = func_call["name"]
                function_args = func_call.get("args", {})
                
                logging.info(f"[Client] Calling function: {function_name} with args: {function_args}")
                
                try:
                    # Call the tool on MCP server
                    result = await self.session.call_tool(function_name, function_args)
                    tool_result = result.content[0].text if result.content else "No result"
                    clean_result = tool_result.replace("\n", "").replace("\r", "").replace("\t", " ").replace("   ", "")
                    logging.info(f"[Client] Function result: {clean_result}")
                    
                    
                    if isinstance(clean_result, str):
                        try:
                            clean_result = json.loads(clean_result)
                        except Exception:
                            pass
                    
                    # Handle line chart display if needed
                    if(function_name == "show_line_chart"):
                        if(clean_result['status'] == "success"):
                            if(clean_result['file_path']):
                                await self.utils.display_line_chart(function_args["data"], clean_result['file_path'])
                            else:
                                self.line_chart_data = function_args["data"]
                        else:
                            logging.warning(f"[Client] [process_query] {clean_result['message']}")
                    
                    
                    # Handle pie chart display if needed
                    if(function_name == "show_pie_chart"):
                        if(clean_result['status'] == "success"):
                            if(clean_result['file_path']):
                                await self.utils.display_pie_chart(function_args["data"], clean_result['file_path'])
                            else:
                                self.pie_chart_data = function_args["data"]
                        else:
                            logging.warning(f"[Client] [process_query] {clean_result['message']}")
                            
                    
                    # Handle report path if needed
                    if(function_name == "generate_trisul_report"):
                        if(clean_result['status'] == "success"):
                            self.report_path = clean_result['file_path']
                        else:
                            logging.warning(f"[Client] [process_query] {clean_result['message']}")
                        

                    # Handle model version change
                    if(function_name == "manage_ai_model_version"):
                        new_model = self.set_model_version()
                        tool_result = {'status': 'success', 'message': f'The AI model version has been changed to {new_model}.'}
                    

                    # Handle API key change
                    if(function_name == "change_api_key"):
                        self.set_api_key()



                    # Add function result to conversation
                    self.conversation_history[-1]["parts"].append({
                        "functionResponse": {
                            "name": function_name,
                            "response": {"result": tool_result}
                        }
                    })
                    
                except Exception as e:
                    logging.error(f"[Client] Error calling function {function_name}: {e}")
                    # Add error result to conversation
                    self.conversation_history.append({
                        "role": "function",
                        "parts": [{
                            "functionResponse": {
                                "name": function_name,
                                "response": {"error": str(e)}
                            }
                        }]
                    })
            
            # Continue the loop to call Gemini again with the function results
        
        # If we hit max iterations, return what we have
        logging.info(f"[Client] Reached maximum iterations ({self.max_iterations}). Returning last response.")
        if self.conversation_history and self.conversation_history[-1]["role"] == "model":
            last_parts = self.conversation_history[-1]["parts"]
            text_parts = [part["text"] for part in last_parts if "text" in part]
            return " ".join(text_parts) if text_parts else "Reached max iterations without final text response"
        
        return "Reached max iterations without response"





    async def update_user_memory(self):
        logging.info(f"[Client] [ai_memory] Updating user memory. Existing memory: \n {self.existing_ai_memory}")
        
        filtered_conversation = []

        for item in self.conversation_history:
            role = item.get("role")
            parts = item.get("parts", [])
            for part in parts:
                text = part.get("text")
                if text and role in ["user", "model"]:
                    filtered_conversation.append({role: text})


        # Load update memory system prompt
        system_prompt_path = self.root_dir / "prompts/system_memory_update.txt"
        template = system_prompt_path.read_text()
        update_memory_system_prompt = template.format(
            confidence_threshold=self.confidence_threshold,
            existing_ai_memory=self.existing_ai_memory,
            filtered_conversation=filtered_conversation
        )

        headers = {"Content-Type": "application/json"}
        chat_history = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": update_memory_system_prompt}
                    ]
                }
            ]
        }

        timeout = httpx.Timeout(120.0, connect=10.0)
        
        async with httpx.AsyncClient(timeout=timeout) as client:
            logging.info("[Client] [ai_memory] Sending update request to Gemini.")
            resp = await client.post(self.GEMINI_URL, headers=headers, json=chat_history)
            resp = resp.json()
            new_ai_memory = resp["candidates"][0]["content"]["parts"][0]["text"]
            new_ai_memory = json.loads(re.sub(r'```json|```', '', new_ai_memory).strip())
            logging.info("[Client] [ai_memory] Received updated memory from Gemini")
            
            
            with open(self.memory_json_path, "w") as file:
                json.dump(new_ai_memory, file, indent=4)
            
            logging.info(f"[Client] [ai_memory] New memory updated : \n {new_ai_memory}")



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
        await self.connect_to_server("trisul_ai_cli.server")
        self.get_api_key()
        self.get_model_version()

        print("\033[1;36m" + "╔══════════════════════════════════════════════════════════════╗")
        print("║  🚀  Trisul AI CLI - Because your network should talk back.  ║")
        print("║                                                              ║")
        print("║  💡  Type 'exit' or 'quit' to close the CLI                  ║")
        print("║                                                              ║")
        print(f"║  📦  Version: {version('trisul_ai_cli')}                                          ║")
        print("╚══════════════════════════════════════════════════════════════╝" + "\033[0m")
        
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

                
                # change the api key
                if query.lower() == "change_api_key":
                    self.set_api_key()
                    continue
                
                # change model version
                if query.lower() == "change_model":
                    new_model = self.set_model_version()
                    print(f"🤖 (Bot) : Model version changed to {new_model}\n")
                    continue
                
                try:
                    # process the query                
                    task = asyncio.create_task(self.process_query(query))
                    spinner = asyncio.create_task(self.loading_animation(task,"Thinking"))
                    response = await task
                    await spinner
                    
                    logging.info(f"[Client] Full Conversation History: \n{json.dumps(self.conversation_history[2:], indent=2)}")
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
                    print(f"\n🤖 (Bot) : {e}")
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
    
    

