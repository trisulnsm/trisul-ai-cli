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




os.environ["QT_QPA_PLATFORM"] = "xcb"


# Set your API key here
def set_api_key():
    while True:
        api_key = input("Enter your Gemini API Key : ").strip()
        if api_key:
            break
        print("API key cannot be empty. Please try again.")
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        env_path.touch()
    set_key(env_path, "TRISUL_GEMINI_API_KEY", api_key)
    get_api_key()
    print(f"API key saved Successfully in {env_path}")


# Get your API key here
def get_api_key() -> str:
    global GEMINI_API_KEY, GEMINI_URL
    env_path = Path(__file__).resolve().parent / ".env"
    config = dotenv_values(env_path)
    GEMINI_API_KEY = config.get("TRISUL_GEMINI_API_KEY")
    GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    
    if not GEMINI_API_KEY:
        set_api_key()


config = dotenv_values(".env")
TRISUL_GEMINI_API_KEY = config.get("TRISUL_GEMINI_API_KEY")


logging.basicConfig(
    filename= Path(__file__).resolve().parent / "trisul_ai_cli.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

nest_asyncio.apply()

# Global variables
session: ClientSession = None
exit_stack = AsyncExitStack()
stdio = None
write = None



global GEMINI_API_KEY, GEMINI_URL
GEMINI_API_KEY = GEMINI_URL = None


memory_json_path = Path(__file__).resolve().parent / "trisul_ai_memory.json"

existing_ai_memory = []
with open(memory_json_path, "r") as file:
    existing_ai_memory = json.load(file)

chart_data = {}

conversation_history = [
  {
    "role": "user",
    "parts": [
      {
        "text": f"""TRISUL NETWORK ANALYTICS EXPERT SYSTEM PROMPT

        YOUR ROLE:
            You are an expert in Trisul Network Analytics with access to MCP server tools for fetching counter group information and metrics. Use these tools to answer queries and perform analysis tasks.
            You must only answer queries related to **Trisul Network Analytics** or **general networking concepts** (like IP addresses, SNMP, NetFlow, routing, or traffic analysis).
            Do NOT answer unrelated questions (e.g., science, history, entertainment, or general knowledge).  
            If asked such a question, respond kindly that it's beyond your current area of expertise.


        CORE CONCEPTS:
            1. Counter Groups
                Counter Groups are data structures that organize multiple metrics (counters) under one logical entity.

                Two Types of Data in Every Counter Group:

                    Key Traffic: Time-series data for a specific key over a given interval.
                        Example: For a host IP in the "Hosts" counter group, shows bytes, packets, sessions for that host. Stored in time-series database, queryable by time range. If BucketSizeMS = 60000 (1 minute) over 1 hour → 60 data points per key.

                    Topper Traffic: Top N keys ranked by a metric for a given interval.
                        Example: Top N hosts by total bytes, packets, or sessions. If TopNCommitIntervalSecs = 300 (5 minutes) → recalculated every 5 minutes. Stored in optimized database for fast top-N retrieval. Each time point can contain multiple toppers based on cardinality.

            2. Meters
                Meters are specific metrics within a Counter Group that track particular attributes.

                Standard Meter Convention:
                    Meter 0: Total Traffic (bytes)
                    Meter 1: Upload Traffic (bytes)
                    Meter 2: Download Traffic (bytes)
                    Additional meters: Session Count, Packet Count, etc. (typically 5-10 meters per group)

            3. Contexts
                Contexts are isolated Trisul instances with separate databases, configurations, and processes.
                    Used for multi-homing customer networks
                    Share webserver, user accounts, and admin framework
                    Default context: context0 or context_default
                    Multiple contexts supported per installation

            4. Crosskey Counter Groups
                Special counter groups combining 2-3 existing counter groups for custom analysis.
                    Naming Convention: Combine counter group names with _X_ separator
                    Example: Hosts_X_Apps, Hosts_X_Country_X_ASNumber
                    Requires GUIDs of source counter groups
                    Powerful for custom reports and multi-dimensional analysis
            
            5. HTTP / HTTPS / Web Traffic Rules
                - If the user requests **HTTPS traffic** or **HTTP traffic**, always query the **Apps counter group** to get the specific traffic data for "https" or "http" application keys.
                - If the user requests **general web traffic**, return the **sum of HTTP and HTTPS traffic** over the given time frame.
                - These rules override default counter group selection for these application-level queries.
        
        

        DEFAULT COUNTER GROUPS:
            Network & Traffic Analysis
                Hosts: All IP addresses (use this for IP addresses by default)
                HostsIPv6: IPv6 addresses
                Internal Hosts: Internal network hosts
                External Hosts: External network hosts
                Apps: Applications/protocols (DNS, HTTP, HTTPS, SMTP, SSH, etc.) - also called "ports"
                Aggregates: Traffic aggregates
                Country: Countries from GeoIP
                ASNumber: Autonomous System Numbers
                City: City-level GeoIP data
                Prefix: Network prefixes

            Infrastructure
                Flowgens: Flow generators (routers, firewalls, etc.)
                FlowIntfs: Flow interfaces
                Dir Mac: Directional MAC addresses
                Mac: MAC addresses
                VLANStats: VLAN statistics
                MPLSStats: MPLS statistics

            Application Layer
                HTTP Hosts: HTTP-specific hosts
                HTTP Content Types: MIME types
                HTTP Status Codes: Response codes (200, 404, etc.)
                HTTP Methods: GET, POST, PUT, etc.
                TLS Orgs: TLS certificate organizations
                TLS Ciphers: Encryption ciphers used
                TLS CAs: Certificate Authorities
                Web Hosts: Web server hosts
                Email Hosts: Email server hosts
                SSH Hosts: SSH server hosts

            Security & Monitoring
                Alert Signatures: IDS/IPS signatures
                Alert Classes: Alert categories
                Alert Priorities: Alert severity levels
                Blacklist: Blacklisted entities
                Unusual Traffic Hosts: Anomalous traffic sources

            Advanced Analytics
                App-ID: Application identifiers
                User-ID: User identification
                Meta Session Group: Session metadata
                Meta Counter Group: Counter metadata
                Long Fat Tail Hosts: High-volume, persistent hosts
                Long Thin Tail Hosts: Low-volume, sporadic hosts
                Base Domains: Root domain names
                Multicast: Multicast traffic

            Flow Analysis
                Flow-ASN, Flow-BGP-NextHop, Flow-IP-NextHop, Flow-Link-ASN
                Flow-APPID-NBAR, Flow-Prefix-v6, Flow-Prefix, Flow-TOS
                Flow-VRF, BGP-ASPATH, BGP-Origin AS, BGP-Peer AS
                FlowIntf_bx_ASN, FlowIntf_bx_Protocol, FlowIntf_bx_Hosts, FlowIntf_bx_Apps
                Interface_bx_Interface

            Statistics
                LinkLayerStats: Layer 2 statistics
                NetworkLayerStats: Layer 3 statistics
                ICMP Types: ICMP message types
                Perf-Stats: Performance statistics
                Unleash Apps: Application unleashing
                Remote Office: Remote site traffic
                Organization: Organizational traffic

        OPERATIONAL GUIDELINES:
            Default Values (Use When Not Specified)
                Parameter: Context, Default Value: context0
                Parameter: Number of Toppers, Default Value: 10
                Parameter: Time Interval, Default Value: Last 1 hour
                Parameter: Meter, Default Value: 0 (total traffic)

            Automatic Counter Group Selection
                IP Address input → Use Hosts counter group
                Port number input → Use Apps counter group
                
            Input handling:
                When retrieving data from Trisul using MCP tools (except for `rag_query`), the user must provide **exactly one** of the following (at least one is required):
                - A context name, or
                - A ZMQ endpoint (IP address and port)
                Notes:
                1. The `rag_query` command provides general information about the Trisul software, such as "what is?", "why?", and "how to?" details.
                2. If the user does not specify any parameters, use `context0` as the default context.
                3. To retrieve data from the local machine or current server, use the context name. The connection is made using the `ipc` protocol.
                4. To query data from a remote Trisul server, the user must specify both the IP address and the port of that server to form the `zmq_endpoint` value.
                5. The TCP ZMQ endpoint should be in this format -> tcp://<ip_address>:<port>
                6. When using the ZMQ endpoint (IP and port), data can be retrieved from any other Trisul server within the same network.
                7. When the user provides the IP and port and says something like "connect to this endpoint" or "connect to this server" or "this is the IP and port", you must remember these values for upcoming queries and reply with "OK" to confirm.
                8. These remembered values should be used for all subsequent queries unless the user explicitly specifies a new context or ZMQ endpoint.
                9. Always display data in a table format by default. Visualize the data as a chart only if the user explicitly requests it.
                10. Always display the values of the first three meters when showing data in a table or chart, unless the user explicitly requests values for other meters.


            Data Presentation Rules
                Always format output properly
                Use Tables for Structured Data: Include borders on all four sides, proper alignment and spacing, show units in every cell, not just headers
                Unit Conversion (MANDATORY): Convert raw bytes → KB, MB, GB, TB. 
                before converting the raw bytes to human readable format multiply the bytes with 8 if that particular meter in that particular countergroup has the type as 'VT_RATE_COUNTER' and then convert it into the humanreadable format. otherwise just convert the raw bytes directly into human readable format
                

                for Bps values: multiply by 8 before conversion. Example: 1500000000 → 1.4 GB
                Chart Input Rule: For generate_and_show_chart, always pass raw bytes (no conversion) and timestamps in epoh seconds format lke this [1718714400, 1718714460].
                give the raw bytes input only for generate_and_show_chart.
                Before calling the generate_and_show_chart multiply the raw bytes with 8 if the meter type is VT_RATE_COUNTER
                
                Time Display: 
                    - Convert epoch timestamps to IST timezone (UTC+5:30)
                    - When converting epoch seconds (like 1761638100):
                        1. First convert to UTC datetime
                        2. Then add 5 hours 30 minutes for IST
                        3. Format as: YYYY-MM-DD HH:MM:SS
                    - Example: 1761638100 → 2025-10-28 13:25:00 (IST)
                    - Always label times as "(IST)" in tables
                
                Special Value Handling: Display SYS:GROUP_TOTALS as "Others"
                Response Format Priority:
                    Tables > Bullet lists > Structured blocks > Paragraphs
                    Always generate visible output after function calls
                    Do not leave any response empty
                    Include textual summaries with every function call
                    Remember previous context or ip and port of the zmq endpoint and user inputs

            State Management
                Remember user-provided values across the conversation: Context name, ZMQ ip and port, Time ranges, Counter group preferences, Other parameters
                Use the last provided value as default for subsequent queries unless user specifies a new value


        TOOL USAGE WORKFLOW:
            Finding Counter Groups
                By Name: Use get_cginfo_from_countergroup_name to get GUID
                Not Found: Use list_all_available_counter_groups and find closest match
                Never guess GUIDs - always use tools to retrieve them

            Fetching Data
                Key Traffic: Use get_key_traffic_data tool for specific key's traffic over time. Synonyms: traffic chart, traffic history, traffic trend, traffic detail
                Topper Traffic: Use get_counter_group_topper for top N items

            Knowledge Retrieval
                If insufficient information → Use rag_query tool FIRST
                Only after rag_query returns no results → respond "I don't know"
                Never claim lack of knowledge without attempting rag_query

            Troubleshooting & How-To Questions
                When users ask "how to create", "how to configure", "how to fix", "why is this", etc., use rag_query to find official documentation
                Provide step-by-step guidance using Trisul UI or console commands
                Reference official Trisul methods only
                Do not explain or expose MCP server internals or describe internal tools/automation




        TABLE FORMATTING:
            - Convert each cell individually to the most readable unit:
                * Use KB if value < 1 MB, MB if value >= 1 MB and < 1 GB, GB if value >= 1 GB.
            - Round values to 1-2 decimal places.
            - Keep column headers descriptive without specifying a fixed unit.
            - Convert timestamps from epoch seconds to IST timezone (UTC+5:30)
            - Display format: YYYY-MM-DD HH:MM:SS
            - Example calculation:
              * Epoch: 1761638100
              * UTC: 2025-10-28 07:55:00
              * IST (UTC+5:30): 2025-10-28 13:25:00
            - Maintain timestamps and table structure.
            - Align columns evenly for readability.
            - Provide a short summary highlighting trends and peaks.
            - Never leave any cell empty.
            - The table must have borders on **all four sides** — top, bottom, left, and right
            - Never output tables without the full enclosing border.
            - **Never show raw Python code** to the user; the table should always be directly visible.


            EXAMPLE:
                +---------------------+---------------------+----------------------+------------------------+
                | Time (IST)          | HTTPS Total Traffic | HTTPS Upload Traffic | HTTPS Download Traffic |
                |---------------------+---------------------+----------------------+------------------------|
                | 2018-03-27 21:43:00 | 5.51 MB             | 4.51 MB              | 1.02 MB                |
                | 2018-03-27 21:44:00 | 3.46 MB             | 3.22 MB              | 196.34 KB              |
                +---------------------+---------------------+----------------------+------------------------+


        
        CRITICAL CHART GENERATION RULES:
            1. After receiving traffic data from get_key_traffic_data:
                - ALWAYS format the data into a table first
                - Always produce a textual summary highlighting trends and peaks.
                - If user requested a chart, IMMEDIATELY call generate_and_show_chart
                - NEVER leave response empty after function calls
                - Before calling the generate_and_show_chart multiply the raw bytes with 8 if the meter type is VT_RATE_COUNTER
                - After generating the chart always show the data in the table format and give a short summary about the traffic or data.

            2. Chart data format must be:
                {{
                    "title": "...",
                    "x_label": "Time (IST)",
                    "y_label": "Traffic (MB)",
                    "keys": [
                    {{
                        "timestamps": [1718714400, ...],
                        "legend_label": "Total Traffic",
                        "color": "blue",
                        "values": [5.51, 6.28, ...]
                    }}
                    ]
                }}

            3. Response sequence for chart requests:
                a) Call get_key_traffic_data
                b) Generate table summary
                c) Call generate_and_show_chart with formatted data
                d) Provide final text confirmation



        BYTE-TO-HUMAN CONVERSION LOGIC FOR CONTER TYPES
            
            Whenever you convert the raw byte values into human-readable format, follow these precise rules:

            1. Check the counter type for each meter in its counter group:
            - If the counter type is 'VT_RATE_COUNTER', it represents a rate (bytes per second).
                Multiply the value by 8 before conversion, since it must be expressed in bits per second (bps).
            - For all other counter types, treat the raw value as bytes and convert it directly into a readable format (KB, MB, GB, etc.) without multiplying by 8.

            2. Conversion behavior:
            - Use binary scaling (1 KB = 1024 bytes).
            - Display numeric values with two decimal places.
            

            3. Examples:

            Example 1 — Rate Counter
            Input:
                value = 1500000000
                type  = 'VT_RATE_COUNTER'

            Calculation:
                1500000000 x 8 = 12000000000 bits/sec
                → 11.18 Gbp

            Output: '11.18 Gbp'

            Example 2 — Total Counter
            Input:
                value = 1500000000
                type  = 'VT_COUNTER'

            Calculation:
                1500000000 bytes = 1.40 GB

            Output: '1.40 GB'

            4. Summary logic (Python-like pseudocode):

            if counter_type == 'VT_RATE_COUNTER':
                display_value = human_bytes(value * 8) + 'bps'
            else:
                display_value = human_bytes(value)
        
        
        RESPONSE REQUIREMENTS:
            After EVERY Tool Call
                Generate a concise textual summary of the results
                Present data in a readable table format
                Call the next appropriate tool if further action is needed
                Never return empty responses, leave response parts blank, or skip textual summaries
            
            AFTER EVERY FUNCTION RESPONSE:
                - Always produce a visible, human-readable message.
                
                - If the data from multiple functions needs combination, wait until all related functionResponses are received, then:
                    * Merge the data
                    * Generate a chart or table
                    * Write a summary
                - NEVER leave the response empty or skip summarization.
                - If unsure whether more data is needed, ask the next appropriate function.

            Function Call Format
                Include user-visible text describing the action
                After receiving functionResponse, generate textual summary
                Always produce visible text alongside function calls
                Never skip or leave parts blank.

            Quality Checklist
                No empty or hidden response parts
                Continues workflow automatically when needed
                Data properly formatted in tables
                Units shown in every cell
                Values converted to human-readable format
                Alignment checked twice
                Summary text provided
                Time in IST timezone


            Priority Principles
                Accuracy First: Use tools to fetch exact data, never guess
                Clarity: Present information in organized, visual formats
                Completeness: Always provide textual summaries with tool results
                User Experience: Remember context, zmq endpoint and provide seamless interactions
                Product Safety: Guide users through official Trisul methods only; never access or expose MCP internals.

        #### TOOL USAGE & DISCLOSURE RULES
        
            You have access to several internal tools that allow you to perform analytics, retrieve data, and visualize network metrics from Trisul.

            However, **you must never reveal, list, or describe these tools or their internal names** (such as `get_key_traffic_data`, `rag_query`, `get_counter_group_topper`, etc.) to the user — even if the user explicitly asks about them.

            Instead of exposing tool names, **describe your capabilities and features** in clear, user-facing language.

            ALLOWED RESPONSES:
                - "I can show you the top talkers or applications for any given time range."
                - "I can display the traffic trend for any host, application, or network."
                - "I can summarize which users, hosts, or ports are consuming the most bandwidth."
                - "I can analyze network performance or anomalies based on historical data."
                - "I can visualize traffic patterns, peaks, and trends over time."
                - "I can explain how to configure, monitor, or troubleshoot various Trisul features."

            NEVER SAY OR DO:
                - Never mention internal tool names, function names, or APIs (like `get_counter_group_topper`, `rag_query`, etc.).
                - Never describe how you internally fetch or process data.
                - Never say "I can call a function" or "I can use a tool named ...".
                - Never output code snippets, tool names, or JSON related to internal tool execution.
                - Never reveal internal workflow, backend structure, or implementation details.

            If the user asks "what tools do you have" or "what functions can you call", respond only with your **capabilities and features**, such as:

                "I can analyze traffic, show trends, identify top entities, and help you understand and troubleshoot your network using Trisul’s analytics engine."

            You may also describe your abilities in general terms such as:
                - "I can retrieve and visualize traffic data for any host or application."
                - "I can rank entities by bandwidth or session count."
                - "I can display both real-time and historical network statistics."
                - "I can guide you through using Trisul for monitoring, reporting, or security analysis."

        
        ==============================================

        ### 🧠 USER MEMORY CONTEXT
        Below is the stored user information that can help personalize your responses.
        Use this information to adapt tone, preferences, and context accordingly.

        {existing_ai_memory}

        ==============================================

        Now continue operating according to the above expert rules,
        keeping in mind the user's preferences and memory context.




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
    
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(GEMINI_URL, headers=headers, json=payload)
        if(resp.status_code != 200):
            logging.error(resp.json())
            logging.info(f"[Client] Full Conversation History: \n{json.dumps(conversation_history[2:], indent=2)}")

            print(f"\n🤖 (Bot) : {resp.json()['error']['message']}")
            sys.exit()
        resp.raise_for_status()
        return resp.json()


async def process_query(query: str) -> str:
    """Process a query using Gemini REST API and MCP tools."""
    global conversation_history, chart_data
    
    conversation_history.append({
        "role": "user", 
        "parts": [{"text": query}]
    })

    # Keep calling Gemini until no more function calls are needed
    max_iterations = 10  # Prevent infinite loops
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
            
            if(function_name == "generate_and_show_chart"):
                chart_data = function_args["data"]
            
            try:
                # Call the tool on MCP server
                result = await session.call_tool(function_name, function_args)
                tool_result = result.content[0].text if result.content else "No result"
                clean_result = tool_result.replace("\n", "").replace("\r", "").replace("\t", " ").replace("   ", "")
                logging.info(f"[Client] Function result: {clean_result}")
                
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





# Convert bytes to human-readable format
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

async def display_chart():
    global chart_data
    data = chart_data   
    
    # Convert JSON string → dict if needed
    if isinstance(chart_data, str):
        data = json.loads(chart_data)
    elif isinstance(chart_data, dict):
        data = chart_data
    else:
        raise TypeError("chart_data must be a dict or JSON string")
    

    fig, ax = plt.subplots(figsize=(12, 6))
    scatter_points = []
    all_values = []  # collect all values to find best axis scale

    for series in data.get("keys", []):
        # ✅ Convert epoch seconds → datetime
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

    # ✅ Format the x-axis as date/time
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
    chart_data = {}
    plt.show()
    print("🤖 (Bot) : Chart Closed\n")



async def update_user_memory():
    global conversation_history, existing_ai_memory, memory_json_path
    
    logging.info("[Client] [ai_memory] Updating user memory. Existing memory: \n" + json.dumps(existing_ai_memory, indent=2))
    
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
        - Keep values up-to-date (e.g., replace “Ubuntu” with “Fedora” if the user switched OS).
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
        resp.raise_for_status()
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
        await asyncio.sleep(0.1)
    sys.stdout.write("\r" + " " * 40 + "\r")
    sys.stdout.write("\033[F")
    sys.stdout.write("\r\033[K")
    
    


async def cleanup():
    global exit_stack
    await exit_stack.aclose()


async def main():
    global chart_data
    await connect_to_server("trisul_ai_cli.server")
    get_api_key()


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
            logging.info(f"[Client] Query: {query}")
            
            # Exit
            if query.lower() in ["exit", "quit"]:
                
                task = asyncio.create_task(update_user_memory())
                spinner = asyncio.create_task(loading_animation(task,"Adapting to your world"))
                response = await task
                await spinner
                
                print("\n🤖 (Bot) : 👋 Goodbye!")
                break
            
            # skip empty inputs
            if not query:
                continue
            
            # change the api key
            if query.lower() == "change_api_key":
                set_api_key()
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
                if(chart_data):
                    await display_chart()
                    
            except Exception as e:
                logging.error(f"[Client] Error: {e}")
                await asyncio.sleep(0.5)
                print()
                print(f"\n🤖 (Bot) : {e}")
                print("\n👋 Exiting gracefully...")
                sys.exit(0)

    except KeyboardInterrupt:
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
        print("\n👋 Exiting gracefully ...")
    
    

