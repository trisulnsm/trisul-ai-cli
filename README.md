# Trisul AI CLI

> Conversational AI for Next-Generation Network Monitoring

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)


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

- Python 3.8 or higher
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
   pip install trisul_ai_cli-0.1.0-py3-none-any.whl
   ```

4. **Launch the CLI**:
   ```bash
   trisul_ai_cli
   ```

5. **Enter your Gemini API key** when prompted (stored securely in `.env`)

## Usage

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
👤 (You): Connect to tcp://10.16.8.44:5008

🤖 (Bot): OK

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

| Tool | Purpose |
|------|---------|
| `list_all_available_counter_groups` | List all available counter groups |
| `get_cginfo_from_countergroup_name` | Get counter group details by name |
| `get_counter_group_topper` | Fetch top N items by traffic/metrics |
| `get_key_traffic_data` | Get time-series traffic for specific keys |
| `create_crosskey_counter_group` | Create custom multi-dimensional counter groups |
| `rag_query` | Search Trisul documentation and knowledge base |
| `generate_and_show_chart` | Generate interactive traffic visualizations |

### User Commands

- **`exit`** or **`quit`**: Exit the CLI
- **`change_api_key`**: Update your Gemini API key

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

## Roadmap

- Support for additional LLMs (Claude, GPT-4, local models)
- PDF report generation
- Multi-user conversation history
- Advanced filtering and correlation queries
- Integration with alerting systems
- Web-based UI alongside CLI



## Acknowledgments

- **Trisul Network Analytics** team for the robust TRP API
- **Google Gemini** for powering the conversational AI
- **Anthropic** for MCP inspiration

## Support

- **Documentation**: [https://www.trisul.org/docs](https://www.trisul.org/docs)
- **Issues**: [GitHub Issues](https://github.com/trisulnsm/trisul-ai-cli/issues)

---

**Trisul AI CLI** - Because your network should talk back.

*Built with care and precision 🔬 by [Unleash Networks](https://www.unleashnetworks.com/)*