# Trisul AI CLI

> Conversational AI for Next-Generation Network Monitoring 

[Python 3.10+](https://www.python.org/downloads/)

## Overview

Trisul AI CLI is a conversational AI interface for [Trisul Network Analytics](https://www.trisul.org/) that transforms network monitoring from complex dashboards and CLI commands into natural language conversations. Simply ask questions about your network in plain English, and get actionable insights instantly.

**Think ChatGPT, but for your network.**

Instead of navigating through menus, logs, and reports, just ask:

- *"What's the traffic trend on this interface in the last 24 hours?"*
- *"Which ASNs pushed the most traffic this week?"*
- *"Show me top IPs on the Airtel WAN"*

Trisul AI handles data retrieval, analysis, and visualization—returning clear answers, tables, charts, and insights directly in your terminal.

## Key Features

- **Natural Language Queries**: Ask questions in plain English, no need to remember commands or menu paths
- **Intelligent Context**: Remembers conversation history and user preferences
- **Multi-Source Data**: Connects to local or remote Trisul servers via IPC or TCP/ZMQ
- **Rich Visualizations**: Generates tables, charts, and traffic graphs with interactive tooltips
- **RAG-Powered Knowledge**: Uses Retrieval-Augmented Generation with Trisul's complete documentation
- **MCP Server Architecture**: Leverages Model Context Protocol for structured tool execution
- **Real-Time Analytics**: Query live network data, toppers, traffic trends, and anomalies

## Architecture

Trisul AI CLI combines several cutting-edge technologies:

```
┌─────────────────────────────────────────────────────────┐
│                    User Query (NL)                      │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│              Gemini AI (Google LLM)                     │
│        • Query Understanding                            │
│        • Function Calling                               │
│        • Response Generation                            │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│          MCP Server (FastMCP)                           │
│        • Tool Execution Layer                           │
│        • ZMQ Communication                              │
│        • Data Transformation                            │
└────────────────────┬────────────────────────────────────┘
                     │
        ┌────────────┼────────────┐
        │            │            │
        ▼            ▼            ▼
┌──────────┐  ┌──────────┐  ┌──────────┐
│ Trisul   │  │   RAG    │  │  SQLite  │
│ TRP API  │  │ ChromaDB │  │  Config  │
│ (ZMQ)    │  │ Gemini   │  │   DB     │
└──────────┘  └──────────┘  └──────────┘
```

### Components

- **Gemini AI**: Google's large language model for natural language understanding
- **MCP Server**: Model Context Protocol server with specialized Trisul tools
- **Trisul TRP API**: Low-level network analytics API via ZeroMQ
- **RAG System**: ChromaDB + Gemini embeddings for documentation retrieval
- **Visualization**: Matplotlib for interactive traffic charts

## Installation

### Prerequisites

- Python 3.10 or higher
- Trisul Network Analytics installed and running
- Gemini API key ([Get one here](https://aistudio.google.com/app/api-keys))

### Setup

1. **Update system and install dependencies** (Debian/Ubuntu):
  ```bash
   sudo apt update && sudo apt install python3-pip python3.12-venv -y
  ```
2. **Create and activate virtual environment**:
  ```bash
   python3 -m venv .venv
   source .venv/bin/activate
  ```
3. **Install Trisul AI CLI**:
  ```bash
   pip install trisul_ai_cli
  ```
4. **Launch the CLI**:
  ```bash
   trisul_ai_cli
  ```
5. **Enter your Gemini API key** when prompted (stored securely in `.env`)

Trisul AI can run in two ways:

1. **CLI mode** — interactive chat in the terminal (`trisul_ai_cli`)
2. **API mode** — REST server that the WebTrisul UI chat window talks to (`trisul_ai_cli api`)

## Using Trisul AI in the WebTrisul UI (API mode)

The WebTrisul chat page does not start the AI engine itself. You start the API server on the Trisul hub (or another host that can reach Trisul), then tell WebTrisul the IP, port, and HTTP/HTTPS.

### Step 1: Start the API server

Use the same virtualenv where `trisul_ai_cli` is installed. The process must stay running.

```bash
source .venv/bin/activate
trisul_ai_cli api --host 0.0.0.0 --port 8200
```

You should see something like:

```
Trisul AI REST API starting in HTTP mode on http://0.0.0.0:8200
   Interactive docs: http://0.0.0.0:8200/docs
   Health check:     http://0.0.0.0:8200/api/health
```

Useful options:


| Flag                               | Meaning                                                              |
| ---------------------------------- | -------------------------------------------------------------------- |
| `--host`                           | Bind address. `0.0.0.0` lets the WebTrisul browser reach the server. |
| `--port`                           | Listen port (default `8200`). Must match the WebTrisul setting.      |
| `--log-level`                      | `debug`, `info`, `warning`, `error`                                  |
| `--ssl-certfile` / `--ssl-keyfile` | Enable HTTPS. Check **AI SSL Mode** in WebTrisul if you use these.   |


HTTPS example:

```bash
trisul_ai_cli api --host 0.0.0.0 --port 8200 \
  --ssl-certfile /path/to/cert.pem \
  --ssl-keyfile /path/to/key.pem
```

Confirm the server is up:

```bash
curl http://127.0.0.1:8200/api/health
```

A healthy response looks like `{"status":"ok","mcp_connected":true,...}`.

The first time you run the CLI or API on a machine, complete LLM setup (API key / model) the same way as CLI mode. The API server uses the same `.env` config.

### Step 2: Point WebTrisul at the API server

1. Log in to WebTrisul as an administrator.
2. Open **WebTrisul Options** (`/webtrisul_options/edit`).
3. Select the **Trisul AI** tab (left sidebar).
4. Fill in **Trisul AI API Endpoint Configuration**:

  | Field                | What to enter                                                                                                          |
  | -------------------- | ---------------------------------------------------------------------------------------------------------------------- |
  | **AI SSL Mode**      | Unchecked for HTTP. Checked only if the API was started with `--ssl-certfile` / `--ssl-keyfile`.                       |
  | **AI Endpoint IP**   | Host the **browser** can reach (the hub IP, or `127.0.0.1` if the UI and API are on the same machine you browse from). |
  | **AI Endpoint Port** | Same port as `--port` (for example `8200`).                                                                            |

5. Click **Save**.

The chat page calls `{http or https}://{AI Endpoint IP}:{AI Endpoint Port}/api/query`. If SSL mode and the server protocol do not match, or the IP/port is wrong, the chat shows **Connection Failed**.

You can reopen the same settings from a failed chat via **Configure Server Settings**, or go directly to `/webtrisul_options/edit?active_tab=tab_trisulai`.

### Step 3: Open the chat from the current context

1. In WebTrisul, switch to the Trisul **context** you want to query.
2. Open the **Trisul AI** page (`/trisul_ai/index`). The header shows that context (for example `default`).
3. Type a question and send it. Every request includes that context; the API locks all Trisul tools to it.

Do not ask the chat to switch context. Change context in WebTrisul, then open Trisul AI again.

### Step 4: What you can ask

Examples:

- *Show top 10 hosts by traffic in the last hour*
- *How much HTTPS traffic did we see today?*
- *Show a pie chart of top applications*
- *Create a host dashboard* (preview first, then confirm)

The reply can include:

- A written answer
- A **table**
- A **line or pie chart**
- A **dashboard package** (after you confirm the layout)

### Step 5: Install a dashboard from chat

When the assistant generates a dashboard:

1. Read the **layout preview** in chat and confirm if it looks right.
2. After generation, use the buttons on that message:
  - **Preview dashboard** — opens a preview without installing
  - **Download dashboard JSON** — saves the package file
  - **Install dashboard** — installs it into WebTrisul, then **View dashboard** opens it

The chat is bound to the context you opened it from, so dashboards and queries apply to that context only.

### If the chat cannot connect

1. Confirm `trisul_ai_cli api` is still running.
2. Hit `/api/health` on the same IP and port you configured.
3. Recheck SSL, IP, and port on the **Trisul AI** options tab.
4. If the browser is on another machine, do not use `127.0.0.1` as **AI Endpoint IP** — use the hub’s reachable address.
5. Check `trisul_ai_cli.log` in the directory where you started the API.

## Usage (CLI mode)

### Basic Queries

```bash
👤 (You): Show top 10 hosts by traffic in the last hour

🤖 (Bot): Here are the top 10 hosts by traffic:
+---------------------+----------------+
| Host IP             | Traffic (GB)   |
+---------------------+----------------+
| 10.25.30.151        | 2.42 GB        |
| 10.26.12.104        | 1.89 GB        |
| 192.168.1.50        | 1.23 GB        |
...
+---------------------+----------------+
```

```bash
👤 (You): Top 5 IPs in last 10 minutes

🤖 (Bot): Here are the top 5 hosts by traffic:
[Returns formatted table with IPs and traffic volumes]
```

```bash
👤 (You): How much HTTPS traffic did we see today?

🤖 (Bot): Total HTTPS traffic today: 127.45 GB
Upload: 45.23 GB, Download: 82.22 GB
```

### Traffic Charts

```bash
👤 (You): Show HTTPS traffic trend for 192.168.10.25 over last 6 hours with a chart

🤖 (Bot): [Generates interactive matplotlib chart in popup window]

+---------------------+---------------------+----------------------+
| Time (IST)          | HTTPS Total Traffic | HTTPS Upload Traffic |
|---------------------+---------------------+----------------------|
| 2025-01-27 15:00:00 | 5.51 MB             | 4.51 MB              |
| 2025-01-27 15:01:00 | 3.46 MB             | 3.22 MB              |
...
+---------------------+---------------------+----------------------+

Peak traffic occurred at 15:23:00 with 8.91 MB total.
```

### Connecting to Remote Servers

```bash
👤 (You): Connect to the remote server with IP address 10.16.8.44 and port 5008.

🤖 (Bot): OK. I will use the ZMQ endpoint 'tcp://10.16.8.44:5008' for all subsequent queries.

👤 (You): Show top apps by traffic

🤖 (Bot): [Retrieves data from remote server...]
```

### Knowledge Queries

```bash
👤 (You): What is a crosskey counter group?

🤖 (Bot): Crosskey is a feature in Trisul that allows you to combine 
multiple counter groups to create a new composite counter group. For 
example, you can create a crosskey counter group that combines 'Source IP' 
and 'Destination IP' counter groups to track traffic between specific IP pairs...
```

## Available Commands

### MCP Tools (Automatically Called)


| Tool                                | Purpose                                                                                                         |
| ----------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| `list_all_available_counter_groups` | List all available counter groups                                                                               |
| `get_cginfo_from_countergroup_name` | Get counter group details by name                                                                               |
| `get_counter_group_topper`          | Fetch top N items by traffic/metrics                                                                            |
| `get_key_traffic_data`              | Get time-series traffic for specific keys                                                                       |
| `create_crosskey_counter_group`     | Propose, confirm, then create custom multi-dimensional counter groups                                           |
| `create_filter_counter_group`       | Propose, confirm, then create filtered counter groups (parent + filter keys)                                    |
| `create_keyset_counter_group`       | Propose, confirm, then create keyset counter groups (parent + named key buckets)                                |
| `list_derived_counter_group_types`  | Catalog of crosskey / filter / keyset: when to use, how to create, scenarios, which dashboard module shows them |
| `list_dashboard_module_types`       | Browse dashboard module templates and their accepted options                                                    |
| `generate_dashboard_json`           | Validate against live Trisul, preview, then write an importable dashboard JSON under `/tmp`                     |
| `rag_query`                         | Search Trisul documentation and knowledge base                                                                  |
| `generate_and_show_chart`           | Generate interactive traffic visualizations                                                                     |


#### Filtered counter groups (`create_filter_counter_group`)

Creates a filtered counter group by writing to the Trisul config SQLite DB (same pattern as `create_crosskey_counter_group`). Filter keys are stored in **DB key format** (e.g. `p-0035` for DNS port 53, or `p-0050,p-01BB,p-0019` for multiple ports).


| User provides            | Tool behavior                                                                                         |
| ------------------------ | ----------------------------------------------------------------------------------------------------- |
| Explicit or partial keys | With `confirm=False`, returns `pending_confirmation` with the complete resolved proposal; no DB write |
| Proposal                 | Includes arguments/rules, `creation_reason`, and the exact `dashboard_usage`                          |
| User confirms            | Call again with `confirm=True` and identical parameters; returns the created GUID                     |


Example: Parent=FlowIntfs, Filter=Apps, name=`DNSPorts`, keys=`Port-53` → saved as `FilterKeyList=p-0035`.

#### Keyset counter groups (`create_keyset_counter_group`)

Groups keys from a parent counter group into named buckets (KeysetKey → KeyFrom). Keys in KeyFrom are stored in **DB key format** (e.g. `p-0050,p-01BB,p-1F90`).


| User provides                                 | Tool behavior                                                                                |
| --------------------------------------------- | -------------------------------------------------------------------------------------------- |
| Name + parent, explicit keys, or partial keys | With `confirm=False`, returns the complete proposal; no DB write                             |
| Proposal                                      | Includes resolved KeysetKey/KeyFrom rules, why it is needed, and where the dashboard uses it |
| User confirms                                 | Call again with `confirm=True` and identical parameters; returns the created GUID            |


Example: Parent=Apps, name=`P2P Traffic`, keyset_key=`p2ptraffic`, keys_from=`Port-6890,Port-6891,...` → saved as comma-separated `p-XXXX` keys.

#### Derived counter groups catalog (`list_derived_counter_group_types`)

Static catalog (not live inventory) covering **crosskey**, **filter**, and **keyset**: what each is, create-tool arguments and confirm workflow, example scenarios, and which dashboard template receives the created GUID (109/110 for crosskey; ordinary toppers/charts for filter and keyset). Always resolve GUIDs from live `list_all_available_counter_groups` — never copy examples from the catalog.

#### Dashboard JSON generation

Dashboard generation never trusts a static counter-group GUID map. It always sends a
live `COUNTER_GROUP_INFO` request and validates every key with `SEARCH_KEYS`. If the
requested data requires a new crosskey, filter, or keyset group, the assistant first
shows the proposed type, name, arguments/rules, reason, and dashboard module usage.
Creation begins only after explicit confirmation.

Each module also carries an `intent` holding the user's own words for that one panel.
Every catalog template declares the `presentation` it renders (table, time-series chart,
single-value badge, tree, sankey, embedded page, alert feed), and
validation rejects a module whose template contradicts the presentation those words ask
for — so "https traffic chart" cannot be generated as a toppers list. An option the
template does not accept is an error too, and the hint names the templates that do
accept it.

`generate_dashboard_json(confirm=False)` returns a layout preview that states the shape
each panel renders and the words it was built from. After approval,
`generate_dashboard_json(confirm=True)` writes the package to `/tmp` and returns the
complete absolute path including the JSON filename.

### User Commands

- `**exit`** or `**quit**`: Exit the CLI
- `**change_api_key**`: Update your Gemini API key

## Configuration

### Default Context

By default, Trisul AI connects to `context0` (local IPC socket). You can specify:

- **Context name**: `context_XYZ`, `default`, `context0`
- **ZMQ endpoint**: `tcp://<ip>:<port>` for remote servers

### Environment Variables

The CLI stores your API key in `.env`:

```bash
TRISUL_GEMINI_API_KEY=your_api_key_here
```

## Logging

Detailed logs are written to `trisul_ai_cli.log` in the installation directory, including:

- Query history
- Function calls and responses
- Error messages and debugging information

## Troubleshooting

### Connection Issues

```bash
Error: ZMQ timeout - no response from ipc://...
```

**Solution**: Verify Trisul Network Analytics is running and the context exists.

### API Key Issues

```bash
Error: Invalid API key
```

**Solution**: Run `change_api_key` command and enter a valid Gemini API key.

### Empty Responses

If the bot returns empty responses, check:

1. Query clarity (be specific about timeframes and entities)
2. Counter group availability (`list_all_available_counter_groups`)
3. Log file for detailed error messages

### WebTrisul chat: Connection Failed

The UI cannot reach `{protocol}://{AI Endpoint IP}:{AI Endpoint Port}/api/query`.

1. Start the API: `trisul_ai_cli api --port 8200`
2. Confirm `/api/health` returns `"mcp_connected": true`
3. Match **AI SSL Mode**, **AI Endpoint IP**, and **AI Endpoint Port** on the WebTrisul **Trisul AI** options tab

## Roadmap

- Multi-user conversation history
- Advanced filtering and correlation queries
- Integration with alerting systems

## Support

- **Documentation**: [https://www.trisul.org/docs](https://www.trisul.org/docs)
- **Issues**: [GitHub Issues](https://github.com/trisulnsm/trisul-ai-cli/issues)

---

**Trisul AI CLI** - Because your network should talk back.

*Built with care and precision 🔬 by [Unleash Networks](https://www.unleashnetworks.com/)*