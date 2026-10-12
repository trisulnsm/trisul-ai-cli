# Trisul AI CLI

> Conversational AI for Next-Generation Network Monitoring

[Python 3.10+](https://www.python.org/downloads/)

Trisul AI turns [Trisul Network Analytics](https://www.trisul.org/) into a conversation. Ask a question in plain English. The assistant reads live traffic from the Trisul hub and answers with text, tables, and charts.

- *"What's the traffic trend on this interface in the last 24 hours?"*
- *"Which ASNs pushed the most traffic this week?"*
- *"Show me top IPs on the Airtel WAN"*

## How Trisul AI is used

There are two places to ask questions, and two ways to install the engine behind them.

**Where you talk to it**


| Surface          | What you run                    | When to use it                                   |
| ---------------- | ------------------------------- | ------------------------------------------------ |
| **CLI**          | `trisul_ai_cli`                 | Interactive chat in a terminal                   |
| **WebTrisul UI** | The Trisul AI page in WebTrisul | Chat in the browser. The page calls the REST API |


**How you install the engine**


| Install                         | Data path to the Trisul hub                                     | API process                                             |
| ------------------------------- | --------------------------------------------------------------- | ------------------------------------------------------- |
| **pip package** on the hub host | Local IPC socket. Context names such as `default` work directly | You start `trisul_ai_cli api` when you want the UI      |
| **Docker container**            | TCP endpoint. The container cannot see the hub IPC socket       | The container starts the API on port **8200** by itself |


```
                         ┌─────────────────────┐
                         │  You ask a question │
                         └──────────┬──────────┘
                                    │
                 ┌──────────────────┴──────────────────┐
                 │                                     │
                 ▼                                     ▼
        trisul_ai_cli                          WebTrisul UI
        (terminal chat)                        Trisul AI page
                 │                                     │
                 │                          POST /api/query
                 │                                     │
                 └──────────────────┬──────────────────┘
                                    ▼
                         Trisul AI engine
                    LLM  +  Trisul TRP tools
                                    │
                 ┌──────────────────┴──────────────────┐
                 │                                     │
                 ▼                                     ▼
        pip on the hub host                    Docker container
        ipc://…/trp_0                          tcp://<hub-ip>:<port>
        (context name)                         (you name this endpoint
                                                in each new chat)
```

Do the steps for **one** install method, configure the LLM once, then open either the CLI or the WebTrisul page.

---

## 1. Install with pip

Use this when Trisul AI runs on the same machine as the Trisul hub. The process can open the hub IPC socket, so questions about a context name work with no extra endpoint.

Later CLI and API commands must run inside the virtual environment you create here. Activate it again in every new shell.

### 1.1 Install the package

On Debian or Ubuntu:

```bash
sudo apt update && sudo apt install python3-pip python3.12-venv -y
```

Create and activate a virtual environment, then install the package:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install trisul_ai_cli
```

### 1.2 Configure the LLM (first launch only)

```bash
trisul_ai_cli
```

The first launch asks you to pick a model from the list (Gemini, OpenAI, Anthropic, or a custom OpenAI-compatible endpoint) and to enter the API key for that provider. The choice is saved in `.env` next to the package. Later launches reuse it.

Change it later from the chat prompt:


| Command                    | What it does                                           |
| -------------------------- | ------------------------------------------------------ |
| `change_llm_model`         | Pick a different model or a custom endpoint            |
| `change_llm_api_key`       | Replace the key for the current provider               |
| `change_embedding_model`   | Pick the embedding model used for documentation search |
| `change_embedding_api_key` | Replace the embedding provider key                     |
| `exit` or `quit`           | Leave the chat                                         |


### 1.3 Ask questions in the CLI

With the virtual environment active:

```bash
trisul_ai_cli
```

The CLI talks to the local hub over IPC and uses `context0` (the `default` context) unless you name another context. Example:

```text
Show top 10 hosts by traffic in the last hour
```

Leave the chat with `exit`.

### 1.4 Serve the WebTrisul UI

The Trisul AI page does not start the engine. Start the REST API in the same virtual environment and leave it running.

```bash
source .venv/bin/activate
trisul_ai_cli api --host 0.0.0.0 --port 8200
```

The server listens on port **8200** on all interfaces. From this machine that is `http://127.0.0.1:8200`. From another machine, use this host's IP and the same port.

Check it:

```bash
curl http://127.0.0.1:8200/api/health
```

A healthy process returns `"status": "ok"` and `"mcp_connected": true`.

Run it in the background and return to the shell:

```bash
trisul_ai_cli api --host 0.0.0.0 --port 8200 -d
```

The background process writes `trisul_ai_cli.log` and `trisul_ai_api.pid` in the current directory.


| Flag                              | Meaning                                                               |
| --------------------------------- | --------------------------------------------------------------------- |
| `--host`                          | Bind address. Default `0.0.0.0`.                                      |
| `--port`                          | Listen port. Default `8200`. Use the same value in WebTrisul.         |
| `-d`, `--daemon`                  | Detach and keep running after the terminal closes.                    |
| `--log-level`                     | `debug`, `info`, `warning`, `error`, or `critical`.                   |
| `--ssl-certfile`, `--ssl-keyfile` | Serve HTTPS. Turn on **AI SSL Mode** in WebTrisul when you use these. |


HTTPS:

```bash
trisul_ai_cli api --host 0.0.0.0 --port 8200 \
  --ssl-certfile /path/to/cert.pem \
  --ssl-keyfile /path/to/key.pem
```

Then connect WebTrisul once, using the steps in [Connect WebTrisul](#3-connect-webtrisul).

A pip install does **not** need the TCP hub change in the Docker section. Context names resolve to the local IPC socket.

---

## 2. Install with Docker

Use this when you want the API as a container. The image already contains the pip package. Starting the container starts API mode. You do not run `trisul_ai_cli api` yourself.

```bash
docker pull parthasarathy369/trisul-ai-cli:latest
```

The API listens on container port **8200**. Publish it with `-p 8200:8200`.

On startup the container always brings the API up.

- `/api/health` succeeds even when no model is set.
- A question returns **LLM is not configured.** until you set a provider, model, and API key.
- After the model is saved, the running API reads it on the next question. You do not restart the container.

LLM settings are stored in `/var/lib/trisul-ai/.env` inside the container. Mount a volume on that path so the file survives `docker rm` and a later `docker run`.

### 2.1 Configure the LLM with environment variables

Pass the provider, model, and matching API key on the first `docker run`. They are written into the `trisul-ai-config` volume.

```bash
docker run -d \
  --name trisul-ai-cli \
  -p 8200:8200 \
  -v trisul-ai-config:/var/lib/trisul-ai \
  -e TRISUL_AI_PROVIDER=gemini \
  -e TRISUL_AI_MODEL=gemini-3.6-flash \
  -e TRISUL_GEMINI_API_KEY=your-api-key \
  parthasarathy369/trisul-ai-cli:latest
```


| Variable                     | Example                                         |
| ---------------------------- | ----------------------------------------------- |
| `TRISUL_AI_PROVIDER`         | `gemini`, `openai`, `anthropic`, or `custom`    |
| `TRISUL_AI_MODEL`            | `gemini-3.6-flash`                              |
| `TRISUL_GEMINI_API_KEY`      | Gemini key                                      |
| `TRISUL_OPENAI_API_KEY`      | OpenAI key, when the provider is `openai`       |
| `TRISUL_ANTHROPIC_API_KEY`   | Anthropic key, when the provider is `anthropic` |
| `TRISUL_CUSTOM_API_BASE_URL` | Base URL, when the provider is `custom`         |
| `TRISUL_CUSTOM_API_KEY`      | Optional for a local server such as Ollama      |


The next start reuses the volume and omits the keys:

```bash
docker run -d \
  --name trisul-ai-cli \
  -p 8200:8200 \
  -v trisul-ai-config:/var/lib/trisul-ai \
  parthasarathy369/trisul-ai-cli:latest
```

If the name `trisul-ai-cli` is already in use, remove the old container before `docker run` (`docker rm -f trisul-ai-cli`). The named volume keeps the saved model.

Run again with a different provider, model, or key **and the same volume** to replace the saved settings. The new values are what the following start uses.

Omit `-v trisul-ai-config:/var/lib/trisul-ai` and a new container has an empty configuration. WebTrisul then shows **LLM is not configured.**

### 2.2 Configure the LLM from inside the container

Start the container without LLM variables, open a shell, and use the same first-run prompts as the pip CLI.

```bash
docker run -d \
  --name trisul-ai-cli \
  -p 8200:8200 \
  -v trisul-ai-config:/var/lib/trisul-ai \
  parthasarathy369/trisul-ai-cli:latest

docker exec -it trisul-ai-cli bash
trisul_ai_cli
```

Choose a model and enter its API key, then leave the chat and the container:

```text
exit
exit
```

The API process that is already running picks up the saved file. No container restart.

Add the volume in the `docker run` above if you want that choice to survive a removed container. Without the volume, the file lives only in that container's writable layer.

### 2.3 Reach hub data over TCP

A container does not have the hub IPC socket, even when the container and the hub are on the same machine. The pip install uses paths such as:

```text
ipc:///usr/local/var/lib/trisul-hub/domain0/hub0/<context>/run/trp_0
```

Those paths are on the host. Queries from the container cannot open them.

Do both of the following.

**Once, on the Trisul hub.** Switch that context from IPC to TCP.

Open the hub config for the context:

```text
/usr/local/etc/trisul-hub/domain0/hub0/<context_name>/trisulHubConfig.xml
```

For the `default` context (`context0`) that file is:

```text
/usr/local/etc/trisul-hub/domain0/hub0/context0/trisulHubConfig.xml
```

Find `ZmqConnection`. On IPC it looks like this:

```xml
<ZmqConnection>
	ipc:///usr/local/var/lib/trisul-hub/domain0/hub0/<context_name>/run/trp_0
</ZmqConnection>
```

Change it to a TCP endpoint on the hub host. Use the hub machine's IP and the port you want TRP to listen on:

```xml
<ZmqConnection>
	tcp://192.168.10.20:5000
</ZmqConnection>
```

Restart that context so the hub picks up the new endpoint:

```bash
trisulctl_hub restart context <context_name>@hub0
```

For `context0`:

```bash
trisulctl_hub restart context context0@hub0
```

After the restart, the engine can be pointed at that same address, for example `tcp://192.168.10.20:5000`.

**At the start of every new conversation.** In the CLI or on the Trisul AI page, tell the assistant the hub address before you ask for traffic data. Any of these phrases sets the endpoint:

```text
Connect to tcp://192.168.10.20:5000
```

```text
Connect to IP address 192.168.10.20 and port 5000
```

Later questions in that **same** conversation keep using that endpoint. A refresh, or a new conversation, starts without it, so say the address again.

A context name alone still tries the IPC socket. From Docker, name the TCP endpoint.

This TCP step applies only to the container. A pip install on the hub host keeps using IPC and the context you already selected.

### 2.4 Ask questions

The container is already in API mode, so WebTrisul can use it as soon as you [connect the UI](#3-connect-webtrisul).

For a terminal chat against the same container:

```bash
docker exec -it trisul-ai-cli trisul_ai_cli
```

Name the TCP endpoint at the start of the chat, then ask the traffic question.

Logs from the API process:

```bash
docker logs trisul-ai-cli
```

The same lines are written to `/var/log/trisul-ai/trisul_ai_cli.log` inside the container.

---

## 3. Connect WebTrisul

Do this once, after the API is running from either pip or Docker.

1. Open WebTrisul and go to the **Trisul AI** page.
2. Click the **configure** (gear) button in the chat header. It opens **WebTrisul Options** on the **Trisul AI** tab (`/webtrisul_options/edit?active_tab=tab_trisulai`).
3. Fill in **Trisul AI API Endpoint Configuration**.


| Field                | What to enter                                                                                                                      |
| -------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| **AI SSL Mode**      | Leave off for HTTP. Turn on only when the API was started with a certificate and key.                                              |
| **AI Endpoint IP**   | The address the **browser** uses to reach the API. Use the host IP. Use `127.0.0.1` only when the browser is on that same machine. |
| **AI Endpoint Port** | `8200`, unless you published a different host port.                                                                                |


1. Click **Save**.

The page then calls `{http or https}://{AI Endpoint IP}:{AI Endpoint Port}/api/query`. A mismatch of protocol, IP, or port shows **Connection Failed**. The same gear button is available from that error.

Open the chat from the Trisul context you want to query. The header shows that context, and each request carries it.

- **pip API:** tools use that context over the local IPC socket.
- **Docker API:** the context lock stays in effect, and traffic questions still need the TCP endpoint from [Reach hub data over TCP](#23-reach-hub-data-over-tcp) at the start of each new conversation.

---

## What you can ask

- *Show top 10 hosts by traffic in the last hour*
- *How much HTTPS traffic did we see today?*
- *Show a pie chart of top applications*
- *Create a host dashboard* (the assistant previews the layout, then builds it after you confirm)

A reply can include a written answer, a table, a line or pie chart, or a dashboard package.

### Install a dashboard from chat

When the assistant generates a dashboard:

1. Read the layout preview and confirm it.
2. Use the buttons on that message:
  - **Preview dashboard** opens a preview without installing
  - **Download dashboard JSON** saves the package
  - **Install dashboard** installs it into WebTrisul, then **View dashboard** opens it

Dashboards and queries follow the context the chat was opened from.

### CLI examples

```text
Show top 10 hosts by traffic in the last hour
```

```text
How much HTTPS traffic did we see today?
```

```text
Show HTTPS traffic trend for 192.168.10.25 over the last 6 hours with a chart
```

```text
Connect to the remote server with IP address 10.16.8.44 and port 5008.
Show top apps by traffic
```

```text
What is a crosskey counter group?
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    User Query (NL)                      │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│         LLM (Gemini / OpenAI / Anthropic / local)       │
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
│ (ZMQ)    │  │ Embeds   │  │   DB     │
└──────────┘  └──────────┘  └──────────┘
```

- **LLM:** Gemini, OpenAI, Anthropic, or a custom OpenAI-compatible endpoint (Ollama, LM Studio, vLLM, LocalAI).
- **MCP server:** Trisul tools exposed over the Model Context Protocol.
- **Trisul TRP API:** ZeroMQ. IPC on the hub host, or `tcp://<ip>:<port>` from Docker and from remote clients.
- **RAG:** ChromaDB with Gemini, OpenAI, or VoyageAI embeddings for the documentation.
- **Charts:** Matplotlib. The container uses a non-interactive backend; the WebTrisul page draws charts in the browser.

---

## Tools the assistant calls

You do not type these names. The model calls them while answering.


| Tool                                | Purpose                                                              |
| ----------------------------------- | -------------------------------------------------------------------- |
| `list_all_contexts`                 | List contexts (name, running state, time window, optional disk size) |
| `list_all_probes`                   | List probes and optional CPU or memory stats                         |
| `list_all_profiles`                 | List capture profiles                                                |
| `get_trisul_mode`                   | `TAP` or `NETFLOW_TAP` for a context and probe                       |
| `list_capture_interfaces`           | Capture adapters for a profile                                       |
| `list_all_available_counter_groups` | List counter groups                                                  |
| `get_cginfo_from_countergroup_name` | Look up a counter group by name                                      |
| `get_counter_group_topper`          | Top N items by a meter                                               |
| `get_key_traffic_data`              | Time series for specific keys                                        |
| `create_crosskey_counter_group`     | Propose, confirm, then create a crosskey group                       |
| `create_filter_counter_group`       | Propose, confirm, then create a filtered group                       |
| `create_keyset_counter_group`       | Propose, confirm, then create a keyset group                         |
| `list_derived_counter_group_types`  | Catalog of crosskey, filter, and keyset                              |
| `list_dashboard_module_types`       | Dashboard module templates                                           |
| `generate_dashboard_json`           | Validate against live Trisul, preview, then write a dashboard JSON   |
| `rag_query`                         | Search Trisul documentation                                          |
| `generate_and_show_chart`           | Build a traffic chart                                                |


Creating a crosskey, filter, or keyset group is a two-step confirm. The first call (`confirm=False`) returns the proposal and writes nothing. The second call (`confirm=True`) with the same arguments creates it.

Dashboard generation checks live counter groups and keys. `generate_dashboard_json(confirm=False)` returns a layout preview. After you approve, `confirm=True` writes the package under `/tmp`.

---

## Configuration reference

On a pip install the file is `.env` in the package directory. In Docker it is `/var/lib/trisul-ai/.env` (the `trisul-ai-config` volume).

```bash
TRISUL_AI_PROVIDER=gemini          # gemini | openai | anthropic | custom
TRISUL_AI_MODEL=gemini-3.6-flash
TRISUL_GEMINI_API_KEY=...          # or TRISUL_OPENAI_API_KEY / TRISUL_ANTHROPIC_API_KEY
TRISUL_CUSTOM_API_BASE_URL=http://localhost:11434   # custom LLM only
TRISUL_CUSTOM_API_KEY=not-needed                    # optional for local servers
TRISUL_EMBEDDING_PROVIDER=gemini   # gemini | openai | voyageai
TRISUL_EMBEDDING_MODEL=models/gemini-embedding-001
TRISUL_AI_MAX_TOKENS=16384         # optional completion token cap
```

Prefer the chat commands (`change_llm_model`, `change_llm_api_key`, and the embedding commands) over editing the file by hand. In Docker, either those commands via `docker exec` or the environment variables on `docker run` write the same file.

**Connection the tools use**


| Install             | Default                                                 | Override                                                                     |
| ------------------- | ------------------------------------------------------- | ---------------------------------------------------------------------------- |
| pip on the hub host | IPC for `context0` / `default`, or another context name | Say `connect to tcp://<ip>:<port>` for a remote hub                          |
| Docker              | No usable IPC socket                                    | Say `connect to tcp://<hub-ip>:<port>` at the start of each new conversation |


---

## Troubleshooting

### WebTrisul says "Connection Failed"

The browser cannot reach `{protocol}://{AI Endpoint IP}:{AI Endpoint Port}/api/query`.

1. Pip: the virtual environment is active and `trisul_ai_cli api` is still running. Docker: `docker ps` shows `trisul-ai-cli`.
2. `curl http://127.0.0.1:8200/api/health` returns `"mcp_connected": true`. From another machine, curl the same IP you entered in WebTrisul.
3. **AI SSL Mode**, **AI Endpoint IP**, and **AI Endpoint Port** match how the API was started.
4. Use the host address in **AI Endpoint IP** when the browser is on a different machine.

### WebTrisul says "LLM is not configured."

The API is up and the model is not saved.

- Pip: run `trisul_ai_cli` once and complete the model and API key prompts.
- Docker: pass `TRISUL_AI_PROVIDER`, `TRISUL_AI_MODEL`, and the matching key on `docker run`, or run `docker exec -it trisul-ai-cli trisul_ai_cli` and complete the prompts.
- Confirm the container was started with `-v trisul-ai-config:/var/lib/trisul-ai` if you expect a previous choice to still be there.

### Docker answers cannot see traffic

The container is still using the IPC socket. Switch the hub context from IPC to TCP as described in [Reach hub data over TCP](#23-reach-hub-data-over-tcp), then start the conversation with the hub IP and port, for example `Connect to tcp://192.168.10.20:5000`. Repeat that line after a refresh or a new conversation.

### ZMQ timeout on an IPC socket

```text
Error: ZMQ timeout - no response from ipc://...
```

On a pip install, confirm the Trisul hub is running and the context exists. Inside Docker, switch to the TCP endpoint described above.

### Invalid API key

In the CLI, run `change_llm_api_key`. If documentation search fails, run `change_embedding_api_key`. Use `change_llm_model` when the provider itself should change.

### Empty answers

Be specific about the time range and the entity (host, application, interface). Check `trisul_ai_cli.log` in the directory where you started the API. In Docker, use `docker logs trisul-ai-cli`.

---

## Support

- **Documentation:** [https://www.trisul.org/docs](https://www.trisul.org/docs)
- **Issues:** [GitHub Issues](https://github.com/trisulnsm/trisul-ai-cli/issues)

---

**Trisul AI CLI** — Because your network should talk back.

*Built with care and precision by [Unleash Networks](https://www.unleashnetworks.com/)*