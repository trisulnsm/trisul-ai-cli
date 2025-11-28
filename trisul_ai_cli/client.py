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
import matplotlib.pyplot as plt
from datetime import datetime
from matplotlib.ticker import FuncFormatter
import matplotlib.dates as mdates
import os
import readline
from importlib.metadata import version
import re
import subprocess
import ast




os.environ["QT_QPA_PLATFORM"] = "xcb"


# Set your API key here
async def set_api_key():
    try:
        print("\033[F\033[K", end="")
        while True:
            api_key = input("\n🤖 (Bot) : Enter your Gemini API Key : ").strip()
            if api_key:
                break
            print("\n🤖 (Bot) : API key cannot be empty. Please try again.")
        env_path = Path(__file__).resolve().parent / ".env"
        if not env_path.exists():
            env_path.touch()
        set_key(env_path, "TRISUL_GEMINI_API_KEY", api_key)
        await get_api_key()
        print("")
        logging.info("[Client] API Key set successfully.")
        
    except KeyboardInterrupt:
        print("\n\n🤖 (Bot) : API Key entry cancelled by user.\n")
        logging.info("[Client] API Key entry cancelled by user.")
        sys.exit(0)


# Get your API key here
async def get_api_key() -> str:
    global GEMINI_API_KEY, GEMINI_URL, GEMINI_MODEL
    env_path = Path(__file__).resolve().parent / ".env"
    config = dotenv_values(env_path)
    GEMINI_API_KEY = config.get("TRISUL_GEMINI_API_KEY")
    GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    
    if not GEMINI_API_KEY:
        await set_api_key()



def get_model_version():
    global GEMINI_URL, GEMINI_API_KEY, GEMINI_MODEL
    env_path = Path(__file__).resolve().parent / ".env"
    config = dotenv_values(env_path)
    gemini_version = config.get("TRISUL_GEMINI_MODEL")
    if gemini_version:
        GEMINI_MODEL = gemini_version
        GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"



# Change the Gemini model version
async def set_model_version():
    global GEMINI_URL, GEMINI_API_KEY, GEMINI_MODEL
    
    try:
        model_versions = ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash", "gemini-2.0-flash-lite"]
        
        print("\033[F\033[K", end="")
        print("\n🤖 (Bot) : Select a Gemini model version from the list below: \n")

        print("Available Gemini Models 📜\n---------------------------")
        for idx, version in enumerate(model_versions, start=1):
            print(f"{idx}) {version} {' (current version)' if version == GEMINI_MODEL else ''}")

        selected_model = GEMINI_MODEL
        while True:
            choice = input("\n🤖 (Bot) : Enter your choice (1-5): ").strip()

            if not choice.isdigit():
                print("\n🤖 (Bot) : ❌ Invalid choice. Try again.")
                continue

            idx = int(choice)
            if 1 <= idx <= len(model_versions):
                selected_model = model_versions[idx - 1]
                GEMINI_MODEL = selected_model
                
                env_path = Path(__file__).resolve().parent / ".env"
                set_key(env_path, "TRISUL_GEMINI_MODEL", GEMINI_MODEL)
                get_model_version()
                logging.info(f"[Client] Model version changed to: {GEMINI_MODEL}")
                break

            print("\n🤖 (Bot) : ❌ Invalid choice. Try again.")
        
        print("")
        return selected_model
    
    except KeyboardInterrupt:
        print("\n\n🤖 (Bot) : Model Selection cancelled by user.\n")
        logging.info("[Client] Model Selection cancelled by user.")
        sys.exit(0)





config = dotenv_values(".env")
TRISUL_GEMINI_API_KEY = config.get("TRISUL_GEMINI_API_KEY")


logging.basicConfig(
    filename= Path(os.getcwd()) / "trisul_ai_cli.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

nest_asyncio.apply()


# Global variables
session: ClientSession = None
exit_stack = AsyncExitStack()
stdio = None
write = None



global GEMINI_API_KEY, GEMINI_URL, GEMINI_MODEL

GEMINI_API_KEY = GEMINI_URL = None

GEMINI_MODEL = "gemini-2.5-flash"


memory_json_path = Path(__file__).resolve().parent / "trisul_ai_memory.json"

existing_ai_memory = []
with open(memory_json_path, "r") as file:
    existing_ai_memory = json.load(file)

line_chart_data = {}

pie_chart_data = {}

report_path = None

conversation_history = [
  {
    "role": "user",
    "parts": [
      {
        "text": f"""TRISUL NETWORK ANALYTICS EXPERT SYSTEM PROMPT

        ### 🎯 YOUR ROLE & SCOPE
        
        You are a Trisul Network Analytics expert assistant with access to tools for retrieving network metrics and analytics data.
        
        **What you CAN answer:**
        - Trisul Network Analytics queries and analysis
        - General networking concepts (IP addresses, protocols, traffic analysis, NetFlow, SNMP, routing)
        
        **What you CANNOT answer:**
        - Unrelated topics (science, history, entertainment, general knowledge)
        - If asked such questions, politely decline: "That's outside my area of expertise in network analytics."

        Adapt your tone and response format based on user preferences stored in memory context.

        ==============================================

        ### 🧠 USER MEMORY CONTEXT

        **Stored user information:**
        {existing_ai_memory}

        **Memory-related queries:**
        When the user says "What do you know about me?", "Remember this...", or "Forget this...":
        1. Respond naturally and acknowledge their request
        2. Do NOT mention internal memory systems or operations
        3. Memory updates happen automatically at session end
        4. Maintain a kind, respectful tone
        5. If you know the user's name, greet them personally

        ==============================================

        ### 🔑 CORE CONCEPTS

        **Counter Groups:**
        - Data structures organizing multiple metrics (counters) under one logical entity
        - Two data types:
          * **Key Traffic**: Time-series data for a specific key over an interval
          * **Topper Traffic**: Top N keys ranked by metric for an interval

        **Meters:**
        - Specific metrics within a Counter Group
        - Standard convention: Meter 0 (Total), Meter 1 (Upload), Meter 2 (Download)
        - Additional meters: Session Count, Packet Count, etc.

        **Contexts:**
        - Isolated Trisul instances (separate databases, configs, processes)
        - Default: context0 or context_default

        **Crosskey Counter Groups:**
        - Combine 2-3 counter groups for custom analysis
        - Naming: Hosts_X_Apps, Hosts_X_Country_X_ASNumber

        **HTTP/HTTPS Traffic:**
        - Query **Apps counter group** for http/https specific traffic
        - For "web traffic", sum HTTP + HTTPS

        ==============================================

        ### 📊 DEFAULT COUNTER GROUPS

        **Network & Traffic:** Hosts, HostsIPv6, Internal Hosts, External Hosts, Apps, Aggregates, Country, ASNumber, City, Prefix
        **Infrastructure:** Flowgens, FlowIntfs, Dir Mac, Mac, VLANStats, MPLSStats
        **Application Layer:** HTTP Hosts, HTTP Content Types, HTTP Status Codes, HTTP Methods, TLS Orgs, TLS Ciphers, TLS CAs, Web Hosts, Email Hosts, SSH Hosts
        **Security:** Alert Signatures, Alert Classes, Alert Priorities, Blacklist, Unusual Traffic Hosts
        **Advanced:** App-ID, User-ID, Meta Session Group, Meta Counter Group, Long Fat Tail Hosts, Long Thin Tail Hosts, Base Domains, Multicast
        **Flow Analysis:** Flow-ASN, Flow-BGP-NextHop, Flow-IP-NextHop, Flow-Link-ASN, Flow-APPID-NBAR, Flow-Prefix-v6, Flow-Prefix, Flow-TOS, Flow-VRF, BGP-ASPATH, BGP-Origin AS, BGP-Peer AS, FlowIntf_bx_ASN, FlowIntf_bx_Protocol, FlowIntf_bx_Hosts, FlowIntf_bx_Apps, Interface_bx_Interface
        **Statistics:** LinkLayerStats, NetworkLayerStats, ICMP Types, Perf-Stats, Unleash Apps, Remote Office, Organization

        **Alert Group GUIDs:**
        - Blacklist: '{{5E97C3A3-41DB-4E34-92C3-87C904FAB83E}}'
        - IDS: '{{9AFD8C08-07EB-47E0-BF05-28B4A7AE8DC9}}'
        - User: '{{B5F1DECB-51D5-4395-B71B-6FA730B772D9}}'
        - Threshold crossing: '{{03AC6B72-FDB7-44C0-9B8C-7A1975C1C5BA}}'
        - Threshold Band: '{{0E7E367D-4455-4680-BC73-699D81B7CBE0}}'
        - Flow Tracker: '{{BE7F367F-8533-45F7-9AE8-A33E5E1AA783}}'

        ==============================================

        ### ⚙️ OPERATIONAL GUIDELINES

        **Default Values:**
        - Context: context0
        - Number of Toppers: 10
        - Time Interval: Last 1 hour
        - Meter: 0 (total traffic)

        **Automatic Selection:**
        - IP Address → Hosts counter group
        - Port number → Apps counter group

        **Connection Parameters:**
        - Tools except `rag_query` require EITHER context name OR ZMQ endpoint
        - Default: context0 (local, IPC protocol)
        - Remote: tcp://<ip>:<port>
        - Remember user-provided contexts/endpoints across conversation
        - When user says "connect to [endpoint]", confirm with "OK" and remember

        **Data Display:**
        - Default: Tables (not charts unless requested)
        - Display first 3 meters unless specified otherwise
        - Always show units in every cell

        ==============================================

        ### 🛠️ TOOL USAGE WORKFLOW

        **Finding Counter Groups:**
        1. Use get_cginfo_from_countergroup_name to get GUID
        2. If not found, use list_all_available_counter_groups
        3. **NEVER guess GUIDs**

        **Fetching Data:**
        - **Key Traffic** (traffic over time): Use get_key_traffic_data
        - **Topper Traffic** (top N items): Use get_counter_group_topper

        **Knowledge Retrieval:**
        1. If insufficient information → Use rag_query FIRST
        2. Only after rag_query returns nothing → say "I don't know"
        3. **NEVER claim ignorance without trying rag_query**

        **For how-to/troubleshooting:** Use rag_query for official docs, provide step-by-step guidance

        ==============================================

        ### 🚫 CRITICAL: GROUNDING & ANTI-HALLUCINATION RULES

        **ALWAYS GROUND YOUR RESPONSES IN TOOL DATA:**
        1. **Only use data returned by tools** - never invent, guess, or extrapolate values
        2. **Verify tool responses before using** - if a tool returns an error or empty result, acknowledge it to the user
        3. **If data is missing:** Say "I don't have that information" rather than making up values
        4. **If uncertain:** Use rag_query or acknowledge limitation
        5. **After calling a tool:**
           - Read the actual returned data carefully
           - Only present what was actually returned
           - Do not add extra fields, values, or rows that weren't in the response
        6. **When computing values:**
           - Show your calculation steps explicitly
           - State which tool provided the data
           - State what counter type it is
           - State which formula you're applying
           - Show the actual arithmetic
           - Double-check math before responding
           - Verify conversions match the formula

        **TOOL RESPONSE PARSING (MANDATORY):**
        - Always check the tool response for: `topperBucketSize`, `meterType`, `counterType`
        - Never assume these values - extract them from the actual response
        - If a field is missing from the response, ask for clarification or use rag_query
        - State explicitly: "The tool returned X, which means Y"

        **CALCULATION TRANSPARENCY (MANDATORY):**
        When explaining calculations to users:
        1. State the raw value from the tool
        2. State the data source (topper vs key traffic)
        3. State the counter type (VT_RATE_COUNTER vs others)
        4. Show the formula being applied
        5. Show each arithmetic step
        6. Show the reverse-verification
        7. Never skip steps or make logical leaps

        **SELF-CORRECTION PROTOCOL:**
        - If you realize you made an error, immediately acknowledge it
        - Explain what you did wrong
        - Show the correct calculation step-by-step
        - Do not make excuses or deflect

        **NEVER:**
        - Fabricate IP addresses, timestamps, or traffic values
        - Assume counter group GUIDs
        - Create fake table data
        - Claim to know something you don't
        - Provide made-up configuration steps without checking rag_query
        - Skip calculation steps when explaining to users
        - Use vague phrases like "approximately" without showing exact math
        - Confuse topper traffic with key traffic
        - Apply the wrong formula and then "correct" it later


        ==============================================

        ### 📐 BYTE-TO-HUMAN CONVERSION (ZERO-HALLUCINATION PROTOCOL)

        **Binary Scaling (MANDATORY):**
        - 1 KB = 1024 bytes
        - 1 MB = 1,048,576 bytes (1024²)
        - 1 GB = 1,073,741,824 bytes (1024³)
        - 1 TB = 1,099,511,627,776 bytes (1024⁴)

        **🚨 CRITICAL: MANDATORY CALCULATION PROTOCOL**
        
        You MUST follow these steps IN ORDER for EVERY conversion. DO NOT skip any step:

        **STEP 1: Identify the data source**
        - Is this from get_key_traffic_data? → KEY TRAFFIC
        - Is this from get_counter_group_topper? → TOPPER TRAFFIC
        - **Write down which one it is before proceeding**

        **STEP 2: Check the counter type from tool response**
        - Look at the actual tool response for the meter type
        - Is it VT_RATE_COUNTER? → YES or NO
        - Is it VT_COUNTER or other? → YES or NO
        - **Write down the counter type before proceeding**

        **STEP 3: Determine the base value to convert**
        - For **TOPPER TRAFFIC**:
          * Base value = raw_value × topperBucketSize (in seconds)
          * DO NOT multiply by 8, even if VT_RATE_COUNTER
          * Example: 1528896 bytes/sec × 3600 sec = 5504025600 bytes
        
        - For **KEY TRAFFIC with VT_RATE_COUNTER**:
          * Base value = raw_value × 8
          * This converts bytes/sec to bits/sec
          * Example: 1500000000 × 8 = 12000000000
        
        - For **KEY TRAFFIC with other counter types**:
          * Base value = raw_value (no multiplication)
          * Example: 1500000000 stays as 1500000000

        **STEP 4: Convert to human-readable unit**
        - Divide base value by appropriate power of 1024
        - Choose unit where result ≥ 1.0
        - Example: 5504025600 ÷ 1073741824 = 5.13 GB

        **STEP 5: Reverse-verify (MANDATORY)**
        - Multiply your result back: displayed_value × (1024^unit_level)
        - Must match base value within ±1 byte
        - Example: 5.13 GB × 1073741824 = 5508256563 ≈ 5504025600 ✓
        - If verification fails, RECALCULATE before responding

        **STEP 6: Format for display**
        - Round to 2 decimals only AFTER verification passes
        - Always include unit (GB, MB, KB, B)
        - Never use Gb, Mb, Kb

        **🔴 COMMON MISTAKES TO AVOID:**
        1. ❌ Using raw value directly without checking if it's topper/key traffic
        2. ❌ Forgetting to multiply topper traffic by bucket size
        3. ❌ Multiplying topper traffic by 8 (NEVER do this)
        4. ❌ Skipping the reverse-verification step
        5. ❌ Confusing bytes/sec with total bytes
        6. ❌ Making up calculation steps that don't match the formula

        **✅ WORKED EXAMPLES:**

        **Example 1: Topper Traffic, VT_RATE_COUNTER**
        ```
        Tool: get_counter_group_topper
        Raw value: 1528896 bytes/sec
        Counter type: VT_RATE_COUNTER
        Bucket size: 3600 seconds
        
        Step 1: TOPPER TRAFFIC ✓
        Step 2: VT_RATE_COUNTER ✓
        Step 3: Base = 1528896 × 3600 = 5504025600 bytes (NO ×8 for topper!)
        Step 4: 5504025600 ÷ 1073741824 = 5.13 GB
        Step 5: Verify: 5.13 × 1073741824 = 5508256563 ≈ 5504025600 ✓
        Step 6: Display: 5.13 GB
        ```

        **Example 2: Key Traffic, VT_RATE_COUNTER**
        ```
        Tool: get_key_traffic_data
        Raw value: 1500000000 bytes/sec
        Counter type: VT_RATE_COUNTER
        
        Step 1: KEY TRAFFIC ✓
        Step 2: VT_RATE_COUNTER ✓
        Step 3: Base = 1500000000 × 8 = 12000000000 bits/sec
        Step 4: 12000000000 ÷ 1073741824 = 11.18 GB
        Step 5: Verify: 11.18 × 1073741824 = 12004366950 ≈ 12000000000 ✓
        Step 6: Display: 11.18 GB
        ```

        **Example 3: Key Traffic, VT_COUNTER**
        ```
        Tool: get_key_traffic_data
        Raw value: 1500000000 bytes
        Counter type: VT_COUNTER
        
        Step 1: KEY TRAFFIC ✓
        Step 2: VT_COUNTER (not rate) ✓
        Step 3: Base = 1500000000 (no multiplication)
        Step 4: 1500000000 ÷ 1073741824 = 1.40 GB
        Step 5: Verify: 1.40 × 1073741824 = 1503238554 ≈ 1500000000 ✓
        Step 6: Display: 1.40 GB
        ```

        **🛑 PRE-RESPONSE VERIFICATION CHECKLIST:**
        Before showing ANY converted value to the user, verify:
        - [ ] I identified if this is topper or key traffic
        - [ ] I checked the actual counter type from the tool response
        - [ ] I applied the correct formula for the data type
        - [ ] I performed reverse-verification and it passed
        - [ ] I did NOT skip any calculation steps
        - [ ] I did NOT make up intermediate values

        **If ANY checkbox is unchecked, DO NOT respond. Recalculate first.**


        ==============================================

        ### 📋 TABLE FORMATTING RULES

        - Use plain ASCII tables with `+`, `-`, and `|` characters.
        - All borders must be present (top, bottom, left, right).
        - Column alignment must remain stable for all rows.
        - Do not allow multi-line or wrapped cells; every cell must remain on a single line.
        - If a cell's content is too long, truncate the text and append `...` (do not wrap).
        - Auto-resize columns based on the longest visible value after truncation.
        - Pad cells with spaces so every column maintains its alignment across the entire table.
        - Never show raw Python objects, dicts, lists, or arrays.
        - Every numeric value must include its unit inside the cell.
        - Missing values must be displayed as `0` or `N/A`, never blank.
        - Replace `SYS:GROUP_TOTALS` with `Others` before rendering the table.
        - Timestamps must use the format `YYYY-MM-DD HH:MM:SS (IST)`.

        **Example (alignment should look exactly like this):**
        ```
        +---------------------+---------------------+----------------------+------------------------+
        | Time (IST)          | HTTPS Total Traffic | HTTPS Upload Traffic | HTTPS Download Traffic |
        |---------------------+---------------------+----------------------+------------------------|
        | 2018-03-27 21:43:00 | 5.51 MB             | 4.51 MB              | 1.02 MB                |
        | 2018-03-27 21:44:00 | 3.46 MB             | 3.22 MB              | 196.34 KB              |
        +---------------------+---------------------+----------------------+------------------------+
        ```

        ==============================================

        ### 📈 CHART GENERATION

        **Line/Pie Charts:**
        1. After tool call, format data into table first
        2. Provide textual summary
        3. If user requested chart, call chart tool
        4. Pass **raw bytes** and **epoch timestamps** to chart tools
        5. For VT_RATE_COUNTER: Multiply raw bytes by 8 before passing to chart tool
        6. After chart, show table + summary again

        **Chart Sequence:**
        a) Call get_key_traffic_data
        b) Generate table summary
        c) Call show_line_chart or show_pie_chart
        d) Provide final text confirmation

        ==============================================

        ### 📄 REPORT GENERATION

        **When user requests a report:**
        1. Call report generation tool with appropriate parameters
        2. For tables: Fetch data, format, include in report
        3. For charts: Fetch data, generate chart with `save_image=True`, include image
        4. Provide summary after generation with full path
        5. **Auto-name reports** with timestamp if user doesn't specify name
        6. Default duration: last 1 hour
        7. **Same time range for all data in one report**
        8. Don't confuse table vs chart - generate what user requested

        ==============================================

        ### ✅ RESPONSE REQUIREMENTS

        **After EVERY tool call:**
        - Generate concise textual summary of results
        - Present data in readable format (table preferred)
        - Never leave response empty
        - Call next tool if workflow requires it

        **Function call arguments:**
        - Provide **fully evaluated literal values only**
        - NO expressions like "147621*8"
        - Pre-compute all values (e.g., 1180968)
        - Valid JSON only (no Python expressions)

        **State Management:**
        - Remember context, ZMQ endpoint, time ranges, preferences
        - Use last provided value as default for subsequent queries

        **Quality Checklist:**
        - No empty responses
        - Data properly formatted
        - Units shown
        - Values reverse-verified
        - Summary provided
        - IST timezone

        **Priority Principles:**
        1. **Accuracy First:** Use tools, never guess - Ground all answers in tool responses
        2. **Clarity:** Organized, visual formats
        3. **Completeness:** Always summarize tool results
        4. **User Experience:** Remember context, seamless interaction
        5. **Safety:** Official Trisul methods only, never expose internals

        ==============================================

        ### 🔒 TOOL DISCLOSURE RULES

        **NEVER reveal:**
        - Internal tool names (get_key_traffic_data, rag_query, etc.)
        - How you fetch or process data internally
        - Code snippets, JSON, or tool execution details
        - Phrases like "I can call a function named..."

        **INSTEAD, describe capabilities:**
        - "I can show you top talkers for any time range"
        - "I can display traffic trends for hosts or applications"
        - "I can analyze network performance and anomalies"
        - "I can visualize traffic patterns and peaks"
        - "I can help configure and troubleshoot Trisul features"

        If asked "what tools do you have?", respond:
        "I can analyze traffic, show trends, identify top entities, and help you understand and troubleshoot your network using Trisul's analytics engine."

        ==============================================

        **You are now ready to assist with Trisul Network Analytics.**
        Keep user preferences and memory context in mind. Always ground responses in actual tool data.



        """
      }
    ]
  },
  {
    "role": "model",
    "parts": [
      {
        "text": "Okay, I understand. I am ready to assist you with Trisul Network Analytics."
      }
    ]
  }
]


async def connect_to_server(server_module: str = "trisul_ai_cli.server"):
    global session, stdio, write, exit_stack

    server_params = StdioServerParameters(
        command="python3",
        args=["-m", server_module],
    )

    stdio_transport = await exit_stack.enter_async_context(stdio_client(server_params))
    stdio, write = stdio_transport
    session = await exit_stack.enter_async_context(ClientSession(stdio, write))
    await session.initialize()

    logging.info("[Client] Connected to server")



async def get_mcp_tools() -> List[Dict[str, Any]]:
    global session
    tools_result = await session.list_tools()
    tool_list = []
    for tool in tools_result.tools:
        # Convert MCP tool schema to Gemini function declaration format
        tool_list.append({
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.inputSchema,
        })

    return tool_list

async def call_gemini_rest() -> Dict:
    """Send a query to Gemini REST API with tools."""
    global conversation_history
    tools = await get_mcp_tools()
    
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
        "contents": conversation_history,
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

    logging.info(f"[Client] Sending request to Gemini at {GEMINI_URL.split('?')[0]}.")

    max_retries = 5
    retry_count = 0
    base_delay = 2  # Start with 2 seconds
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        while retry_count < max_retries:
            resp = await client.post(GEMINI_URL, headers=headers, json=payload)
            
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
                logging.info(f"[Client] Full Conversation History: \n{json.dumps(conversation_history[2:], indent=2)}")
                
                # Remove the last loading line from console 
                sys.stdout.write("\033[F")
                
                # print the error message from Gemini
                print(f"\n🤖 (Bot) : {resp.json()['error']['message']}\n\n")
                sys.exit()
            
            # Success - break out of retry loop
            break
            
        return resp.json()


async def process_query(query: str) -> str:
    """Process a query using Gemini REST API and MCP tools."""
    global conversation_history, line_chart_data, pie_chart_data, report_path
    
    conversation_history.append({
        "role": "user", 
        "parts": [{"text": query}]
    })

    # Keep calling Gemini until no more function calls are needed
    max_iterations = 15  # Prevent infinite loops
    iteration = 0
    
    while iteration < max_iterations:
        iteration += 1
        
        # Call Gemini
        gemini_response = await call_gemini_rest()
        
        if not gemini_response.get("candidates"):
            return "No response from Gemini"
                    
        candidate = gemini_response["candidates"][0]
        content = candidate.get("content", {})
        parts = content.get("parts", [])
        
        
        if not parts:
            logging.warning("[Client] Empty model response detected")
            # Force Gemini to continue
            conversation_history.append({
                "role": "user",
                "parts": [{
                    "text": "Please provide a summary of the data and complete the chart generation or call the next tool to continue if requested."
                }]
            })
            continue 
        
        
        # Add Gemini's response to conversation history
        conversation_history.append({
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

        
        conversation_history.append({
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
                result = await session.call_tool(function_name, function_args)
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
                            await display_line_chart(function_args["data"], clean_result['file_path'])
                        else:
                            line_chart_data = function_args["data"]
                    else:
                        logging.warning(f"[Client] [process_query] {clean_result['message']}")
                
                
                # Handle pie chart display if needed
                if(function_name == "show_pie_chart"):
                    if(clean_result['status'] == "success"):
                        if(clean_result['file_path']):
                            await display_pie_chart(function_args["data"], clean_result['file_path'])
                        else:
                            pie_chart_data = function_args["data"]
                    else:
                        logging.warning(f"[Client] [process_query] {clean_result['message']}")
                        
                
                # Handle report path if needed
                if(function_name == "generate_trisul_report"):
                    if(clean_result['status'] == "success"):
                        report_path = clean_result['file_path']
                    else:
                        logging.warning(f"[Client] [process_query] {clean_result['message']}")
                    

                # Handle model version change
                if(function_name == "manage_ai_model_version"):
                    new_model = await set_model_version()
                    tool_result = {'status': 'success', 'message': f'The AI model version has been changed to {new_model}.'}
                

                # Handle API key change
                if(function_name == "change_api_key"):
                    await set_api_key()



                # Add function result to conversation
                conversation_history[-1]["parts"].append({
                    "functionResponse": {
                        "name": function_name,
                        "response": {"result": tool_result}
                    }
                })
                
            except Exception as e:
                logging.error(f"[Client] Error calling function {function_name}: {e}")
                # Add error result to conversation
                conversation_history.append({
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
    logging.info(f"[Client] Reached maximum iterations ({max_iterations}). Returning last response.")
    if conversation_history and conversation_history[-1]["role"] == "model":
        last_parts = conversation_history[-1]["parts"]
        text_parts = [part["text"] for part in last_parts if "text" in part]
        return " ".join(text_parts) if text_parts else "Reached max iterations without final text response"
    
    return "Reached max iterations without response"





# LINE CHART
def human_bytes(num):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if num < 1024:
            return f"{num:.2f} {unit}"
        num /= 1024
    return f"{num:.2f} PB"

def bytes_to_unit(num):
    """Convert to largest appropriate unit but return only scaled value (for axis)"""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if num < 1024:
            return num, unit
        num /= 1024
    return num, 'PB'

async def display_line_chart(temp_line_chart_data=None, file_path=None):
    logging.info("[Client] [display_line_chart] Generating the line chart")
    global line_chart_data

    line_chart_data = temp_line_chart_data if temp_line_chart_data else line_chart_data
    
    data = line_chart_data
    

    # Convert JSON string → dict if needed
    if isinstance(line_chart_data, str):
        try:
            data = ast.literal_eval(line_chart_data)
        except Exception:
            try:
                data = json.loads(line_chart_data)
            except Exception:
                logging.error("[Client] [display_line_chart] Invalid JSON value from LLM")
                return
    elif isinstance(line_chart_data, dict):
        data = line_chart_data
    else:
        logging.error("[Client] [display_line_chart] Invalid line chart data format. Expected dict or JSON string.")
        return





    fig, ax = plt.subplots(figsize=(12, 6))
    scatter_points = []
    all_values = []  # collect all values to find best axis scale

    for series in data.get("keys", []):
        # Convert epoch seconds → datetime
        timestamps = [datetime.fromtimestamp(ts) for ts in series["timestamps"]]
        values = series["values"]
        all_values.extend(values)

        line, = ax.plot(
            timestamps,
            values,
            label=series["legend_label"],
            color=series.get("color", None),
            marker='o'
        )
        scatter_points.append((line, timestamps, values))

    # Determine global scale for axis
    max_val = max(all_values)
    scaled_val, unit = bytes_to_unit(max_val)
    scale_factor = max_val / scaled_val  # bytes per displayed unit

    # Apply formatter to y-axis
    ax.yaxis.set_major_formatter(FuncFormatter(lambda y, _: f"{y / scale_factor:.2f} {unit}"))

    # Format the x-axis as date/time
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d %H:%M:%S'))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())

    ax.set_title(data.get("title", "Traffic Chart"))
    ax.set_xlabel(data.get("x_label", "Time"))
    ax.set_ylabel(f"{data.get('y_label', 'Traffic')} ({unit})")
    ax.legend()
    ax.grid(True)
    fig.autofmt_xdate()
    
    # Create annotation (tooltip)
    annot = ax.annotate(
        "", xy=(0,0), xytext=(20,20), textcoords="offset points",
        bbox=dict(boxstyle="round", fc="w"),
        arrowprops=dict(arrowstyle="->")
    )
    annot.set_visible(False)

    def update_annot(ind, line, x_data, y_data):
        x = x_data[ind]
        y = y_data[ind]
        annot.xy = (x, y)
        text = f"{x.strftime('%Y-%m-%d %H:%M:%S')}\n{human_bytes(y)}"
        annot.set_text(text)
        annot.get_bbox_patch().set_facecolor(line.get_color())
        annot.get_bbox_patch().set_alpha(0.6)

    def hover(event):
        visible = annot.get_visible()
        if event.inaxes == ax:
            for line, x_data, y_data in scatter_points:
                cont, ind = line.contains(event)
                if cont:
                    update_annot(ind["ind"][0], line, x_data, y_data)
                    annot.set_visible(True)
                    fig.canvas.draw_idle()
                    return
        if visible:
            annot.set_visible(False)
            fig.canvas.draw_idle()

    fig.canvas.mpl_connect("motion_notify_event", hover)
    plt.tight_layout()
    
    if(file_path):
        plt.savefig(file_path, dpi=300, bbox_inches='tight')
        plt.close()
        logging.info(f"[Client] Chart saved to {file_path}")
    else:
        logging.info("[Client] Chart UI ready. Awaiting user interaction")
        plt.show()
        plt.close()
        logging.info("[Client] Chart closed by user")
        print("🤖 (Bot) : Chart Closed\n")

        
    line_chart_data = {}






# PIE CHART
def human_readable_bytes(num):
    """Convert bytes to human-readable format."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if num < 1024:
            return f"{num:.2f} {unit}"
        num /= 1024
    return f"{num:.2f} PB"


async def display_pie_chart(temp_pie_chart_data=None, file_path=None):
    logging.info("[Client] Starting pie chart render workflow")

    global pie_chart_data
    raw_input_type = type(pie_chart_data).__name__
    logging.debug("[Client] Inbound chart data type=%s", raw_input_type)

    pie_chart_data = temp_pie_chart_data if temp_pie_chart_data else pie_chart_data

    chart_opts = pie_chart_data

    # Normalize data to dict
    if isinstance(pie_chart_data, str):
        logging.debug("[Client] Attempt JSON parsing for string input")
        try:
            chart_opts = json.loads(pie_chart_data)
            logging.info("[Client] Chart config loaded from JSON string")
        except json.JSONDecodeError:
            logging.warning("[Client] Non-standard JSON received. Attempting normalization")
            normalized = pie_chart_data.strip()
            normalized = re.sub(r"(?<!\\)'", '"', normalized)
            normalized = re.sub(r",\s*}", "}", normalized)
            normalized = re.sub(r",\s*]", "]", normalized)

            try:
                chart_opts = json.loads(normalized)
                logging.info("[Client] Chart config normalized and parsed successfully")
            except json.JSONDecodeError as e:
                logging.error("[Client] Normalization failed. Root cause=%s", e)
                raise ValueError(f"Invalid chart data format after normalization: {e}")

    elif isinstance(pie_chart_data, dict):
        logging.info("[Client] Chart config loaded from dict")
    else:
        logging.error("[Client] Unsupported data type for pie_chart_data: %s", raw_input_type)
        raise TypeError("pie_chart_data must be a dict or JSON string")

    labels = chart_opts.get('labels', [])
    volumes = chart_opts.get('volumes', [])
    colors = chart_opts.get('colors', [])
    chart_title = chart_opts.get('chart_title', "Pie Chart")
    legend_title = chart_opts.get('legend_title', "Legend")

    logging.debug("[Client] Chart metadata loaded: labels=%d volumes=%d colors=%d",
                 len(labels), len(volumes), len(colors))

    total_volume = sum(volumes)
    if total_volume == 0:
        logging.warning("[Client] All volume values are zero. Chart aborted.")
        return

    logging.info("[Client] Rendering chart: title='%s' total_items=%d total_volume=%s",
                chart_title, len(volumes), human_readable_bytes(total_volume))

    fig, ax = plt.subplots(figsize=(7, 6))
    wedges, texts = ax.pie(
        volumes,
        labels=labels,
        colors=colors,
        startangle=90,
        labeldistance=0.7,
        wedgeprops=dict(edgecolor='none')
    )

    ax.axis('equal')
    plt.title(chart_title, pad=20)
    legend = ax.legend(
        wedges,
        labels,
        loc="center left",
        bbox_to_anchor=(1.05, 0.5),
        frameon=False,
        title=legend_title
    )

    logging.debug("[Client] Hover and click event handlers initializing")

    # Tooltip
    tooltip = ax.annotate(
        "",
        xy=(0, 0),
        xytext=(15, 15),
        textcoords="offset points",
        ha='left', va='bottom',
        fontsize=10, fontweight='bold', color='black',
        bbox=dict(facecolor='white', alpha=0.9, boxstyle='round', ec='gray'),
        visible=False
    )

    hovered_index = {'value': None}
    selected_index = {'value': None}

    def on_motion(event):
        # Keep logging light inside event loop
        found = False
        if event.inaxes != ax:
            if hovered_index['value'] is not None:
                for w in wedges:
                    w.set_alpha(1.0)
                tooltip.set_visible(False)
                hovered_index['value'] = None
                fig.canvas.draw_idle()
            return

        for i, w in enumerate(wedges):
            contains, _ = w.contains(event)
            if contains:
                if hovered_index['value'] != i:
                    for ww in wedges:
                        ww.set_alpha(0.6)
                    w.set_alpha(1.0)
                    hovered_index['value'] = i
                tooltip.xy = (event.xdata, event.ydata)
                tooltip.set_text(f"{labels[i]}: {human_readable_bytes(volumes[i])}")
                tooltip.set_visible(True)
                fig.canvas.draw_idle()
                found = True
                break

        if not found:
            renderer = fig.canvas.get_renderer()
            for i, leg_text in enumerate(legend.get_texts()):
                bbox = leg_text.get_window_extent(renderer=renderer)
                if bbox.contains(event.x, event.y):
                    if hovered_index['value'] != i:
                        for ww in wedges:
                            ww.set_alpha(0.6)
                        wedges[i].set_alpha(1.0)
                        hovered_index['value'] = i
                    tooltip.xy = (event.xdata, event.ydata)
                    tooltip.set_text(f"{labels[i]}: {human_readable_bytes(volumes[i])}")
                    tooltip.set_visible(True)
                    fig.canvas.draw_idle()
                    found = True
                    break

        if not found and hovered_index['value'] is not None:
            for ww in wedges:
                ww.set_alpha(1.0)
            tooltip.set_visible(False)
            hovered_index['value'] = None
            fig.canvas.draw_idle()

    def on_click(event):
        renderer = fig.canvas.get_renderer()
        for i, leg_text in enumerate(legend.get_texts()):
            bbox = leg_text.get_window_extent(renderer=renderer)
            if bbox.contains(event.x, event.y):
                logging.info("[Client] Legend item clicked index=%d label='%s'", i, labels[i])
                for w in wedges:
                    w.set_center((0, 0))
                    w.set_alpha(0.8)
                    w.set_radius(1.0)

                if selected_index['value'] == i:
                    logging.debug("[Client] Slice deselected index=%d", i)
                    selected_index['value'] = None
                    fig.canvas.draw_idle()
                    return

                w = wedges[i]
                w.set_radius(1.1)
                w.set_alpha(1.0)
                selected_index['value'] = i
                fig.canvas.draw_idle()
                break

    fig.canvas.mpl_connect("motion_notify_event", on_motion)
    fig.canvas.mpl_connect("button_press_event", on_click)

    plt.tight_layout()
    
    
    if(file_path):
        plt.savefig(file_path, dpi=300, bbox_inches='tight')
        plt.close()
        logging.info(f"[Client] Chart saved to {file_path}")

    else:
        logging.info("[Client] Chart UI ready. Awaiting user interaction")
        plt.show()
        plt.close()
        logging.info("[Client] Chart closed by user")
        print("🤖 (Bot) : Chart Closed\n")
        
    pie_chart_data = {}





async def update_user_memory():
    global conversation_history, existing_ai_memory, memory_json_path
    
    logging.info(f"[Client] [ai_memory] Updating user memory. Existing memory: \n {existing_ai_memory}")
    
    confidence_threshold = 90
    
    filtered_conversation = []

    for item in conversation_history:
        role = item.get("role")
        parts = item.get("parts", [])
        for part in parts:
            text = part.get("text")
            if text and role in ["user", "model"]:
                filtered_conversation.append({role: text})

        
    system_prompt = f"""
        You are a long-term memory management model responsible for maintaining and updating a persistent user memory database.

        Your goal:
        - Analyze the full conversation between a user and an assistant.
        - Use the existing memory object below to maintain continuity, accuracy, and relevance.

        ---

        ### 🧠 Your Tasks

        1. **Extract only durable and useful facts** about the user that can improve future responses.
        - Examples: preferences, tools, habits, learning goals, environment, or frequently discussed topics.
        - If no new or useful facts are found, **return the existing memory unchanged**.

        2. **Compare and integrate** new facts with the existing memory:
        - Use *semantic* comparison — understand meaning, not just surface text.
        - Only **add or update** facts if they are **useful for future responses** and meet the confidence threshold.
        - If no such facts exist → **do not modify** the existing memory at all; return it exactly as provided.
        - If a similar fact already exists → update its value, confidence, and source.
        - If a fact contradicts an existing one → replace the old fact with the new one.
        - If a fact is temporary, outdated, or irrelevant → remove it completely (do not mark as deleted).
        - If a fact is identical or redundant → skip adding it.

        3. **Estimate confidence** using the following criteria:
        - durability: Will this still matter later?
        - frequency: Has it been mentioned repeatedly? (if unknown, assume 0.5)
        - utility: Will it improve future responses?
        - ephemerality: 1.0 if temporary or situational, else 0.0
        - sensitivity: 1.0 if it contains private/sensitive info (email, IP, token, password), else 0.0

        Compute raw confidence as:
            raw_confidence = (durability * 1) + (frequency * 1) + (utility * 1) - (ephemerality * 1) - (sensitivity * 1)

        Let:
            min_confidence = -( weight_of_ephemerality + weight_of_sensitivity)
            max_confidence = weight_of_durability + weight_of_frequency + weight_of_utility

        Convert raw confidence to a 0 - 100 scale:
            scaled_confidence = ((raw_confidence - min_confidence) / (max_confidence - min_confidence)) * 100

        - Do **not** clip, smooth, or round the scaled_confidence.
        - The output value must reflect the **exact mathematical result**, including decimals.
        - The range is always **0 to 100** by definition of the formula.

        Only include or update facts where:
            scaled_confidence ≥ {confidence_threshold}

        4. **If no facts qualify** (i.e., no new useful information or no facts meeting confidence threshold):
        - **Return the existing memory exactly as it was provided**, with no additions, deletions, or modifications.

        5. **Ensure self-consistency** only when changes are made:
        - Merge related facts (e.g., multiple programming languages → merge into one array).
        - Keep values up-to-date (e.g., replace "Ubuntu" with "Fedora" if the user switched OS).
        - Remove irrelevant or outdated facts completely from the final output.

        6. **If updates are made**, always produce a fully merged and cleaned memory object containing:
        - All valid existing facts
        - Any new or updated facts
        - No duplicates, contradictions, or deleted entries

        ---

        ### ⚙️ Inputs

        **Existing memory:**
        {existing_ai_memory}

        **New conversation:**
        {filtered_conversation}

        ---

        ### 📦 Output Format (strict JSON only)

        Return the **final, updated memory object** as a JSON array.

        If no new or useful facts are found or none meet the confidence threshold, 
        **return the same existing memory JSON exactly as received**.

        [
        {{
            "key": "<string>",
            "value": "<string or array>",
            "confidence": scaled_confidence,
            "source": "<string>",
            "durability": <float>,
            "frequency": <float>,
            "utility": <float>,
            "ephemerality": <float>,
            "sensitivity": <float>
        }},
        ...
        ]
        
    """



    chat_history = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": system_prompt}
                ]
            }
        ]
    }

    headers = {"Content-Type": "application/json"}

    timeout = httpx.Timeout(120.0, connect=10.0)
    
    async with httpx.AsyncClient(timeout=timeout) as client:
        logging.info("[Client] [ai_memory] Sending update request to Gemini.")
        resp = await client.post(GEMINI_URL, headers=headers, json=chat_history)
        resp = resp.json()
        new_ai_memory = resp["candidates"][0]["content"]["parts"][0]["text"]
        new_ai_memory = json.loads(re.sub(r'```json|```', '', new_ai_memory).strip())
        logging.info("[Client] [ai_memory] Received updated memory from Gemini")
        
        
        with open(memory_json_path, "w") as file:
            json.dump(new_ai_memory, file, indent=4)
        
        logging.info(f"[Client] [ai_memory] New memory updated : \n {new_ai_memory}")



async def loading_animation(task, message):
    spinner = ["⢄", "⢂", "⢁", "⡁", "⡈", "⡐", "⡠"]
    i = 0
    print("")
    
    while not task.done():
        sys.stdout.write(f"\r✨ {message} {f'{spinner[i % len(spinner)]}  '}")
        sys.stdout.flush()
        i += 1
        await asyncio.sleep(0.5)
    sys.stdout.write("\r" + " " * 40 + "\r")
    sys.stdout.write("\033[F")
    sys.stdout.write("\r\033[K")
    
    


async def cleanup():
    global exit_stack
    await exit_stack.aclose()



async def main():
    global line_chart_data, pie_chart_data, report_path
    await connect_to_server("trisul_ai_cli.server")
    await get_api_key()
    get_model_version()

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
                task = asyncio.create_task(update_user_memory())
                spinner = asyncio.create_task(loading_animation(task,"Adapting to your world"))
                await task
                await spinner
                
                logging.info("[Client] Bye!")
                print("\n🤖 (Bot) : 👋 Bye!")
                break

            
            # change the api key
            if query.lower() == "change_api_key":
                await set_api_key()
                continue
            
            # change model version
            if query.lower() == "change_model":
                new_model = await set_model_version()
                print(f"🤖 (Bot) : Model version changed to {new_model}\n")
                continue
            
            try:
                # process the query                
                task = asyncio.create_task(process_query(query))
                spinner = asyncio.create_task(loading_animation(task,"Thinking"))
                response = await task
                await spinner
                
                logging.info(f"[Client] Full Conversation History: \n{json.dumps(conversation_history[2:], indent=2)}")
                logging.info(f"[Client] Response: \n{response}")
                print(f"\n🤖 (Bot) : {response.strip()}\n")
                
                # If a chart was prepared, display it
                if(line_chart_data):
                    await display_line_chart()
                
                if(pie_chart_data):
                    await display_pie_chart()
                
                if(report_path):                    
                    if os.name == "nt":
                        os.startfile(report_path)
                    elif sys.platform == "darwin":
                        subprocess.Popen(["open", report_path])
                    else:
                        subprocess.Popen(["xdg-open", report_path])

                    
                    
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
        await cleanup()
        # Give ZeroMQ sockets time to close cleanly
        await asyncio.sleep(0.1)
        return

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("[Client] Exiting gracefully ...")
        print("\n👋 Exiting gracefully ...")
    
    

