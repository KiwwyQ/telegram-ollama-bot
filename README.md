# 🤖 Telegram Bot with Ollama Cloud (Free Tier)

A production-ready, fully asynchronous Telegram bot that connects each user to
their **own free Ollama Cloud API key**, supports **groups**, **vision**,
**web search**, **GIFs**, **file generation**, **per-chat memory** with
auto-summarization, and a **customizable personality** — all deployable on
**Render.com free tier**.

Built with [`python-telegram-bot` v21+](https://github.com/python-telegram-bot/python-telegram-bot)
(async), `httpx` for Ollama Cloud calls, and a remote database (SQLAlchemy async:
MySQL/Postgres) for persistent storage.

---

## ✨ Features

| Area | Details |
|------|---------|
| **Ollama Cloud** | Uses each user's personal API key. `POST /api/chat` with `Authorization: Bearer <key>`. |
| **Vision** | Send a photo → the bot analyzes it with a vision-capable model. |
| **Web search** | The model can call Ollama's web search when it needs fresh info. |
| **GIFs** | The model can send GIFs (Klipy; optional key, disabled if unset). |
| **Files** | The bot generates text files on the fly and sends them as documents. |
| **Workspace** | Each user gets a private workspace. List, read, write, and delete files via the model. |
| **Python eval** | Execute Python code in your workspace (optional, gated by SANDBOX_ALLOW). |
| **Trigger rules** | In groups it replies **only** when mentioned (`@BotUsername`) or when you reply to its messages. Normal group chatter is ignored. |
| **Memory** | Per-chat history (per-user in DMs, shared in groups) with automatic summarization near the token limit. |
| **Personality** | Default friendly persona; customizable per-user in DMs, per-group by admins. |
| **Languages** | Per-user language preference (`/lang`). |
| **Formatting** | Plain / HTML / MarkdownV2 reply formatting (`/format`). |
| **UX** | Sends `⏳` immediately, then edits with the answer (streaming-like progressive edits). Per-chat queue so messages are processed in order. Rate-limit throttle per user. Friendly errors, no stack traces. |
| **Robustness** | Restart-resilient remote database (MySQL/Postgres), graceful shutdown, structured logging with secret redaction. |

---

## 🗂 Architecture

```
.
├── bot.py              # Entry point: wires everything, runs polling
├── config.py           # Env-driven settings (no hardcoded secrets)
├── logging_config.py   # Structured logging, redacts secrets
├── storage.py          # Remote DB (SQLAlchemy async: MySQL/Postgres): users, groups, memory, usage
├── ollama_client.py    # Ollama Cloud REST client (chat, search, streaming)
├── memory.py           # Conversation history + auto-summarization
├── tools.py            # Web search, GIF, file, vision image helpers
├── personality.py      # Default persona + tool instruction + languages
├── handlers.py         # Triggers, commands, generation orchestration
├── keep_alive.py       # Optional /health endpoint (Web Service mode)
├── requirements.txt
├── .env.example
├── render.yaml         # Render.com deploy manifest (Background Worker)
├── Dockerfile
├── Procfile
└── README.md
```

**Data flow for a message**
1. `handlers.handle_message` checks trigger rules and that the user has a key.
2. Sends `⏳`, acquires a per-chat `asyncio.Lock` (the queue).
3. Builds the prompt: `system = personality + tool instruction + language + participants`, plus chat memory and the new user message.
4. Calls Ollama; if the model asks for `[SEARCH: ...]`, the bot fetches results and re-asks.
5. Parses `[GIF: ...]` / `[FILE: ...]` markers → sends those separately, strips them from the visible reply.
6. Edits `⏳` with the final text, saves to memory, and summarizes if needed.

---

## 🤖 1. Create the bot with BotFather

1. Open Telegram and message [@BotFather](https://t.me/BotFather).
2. Send `/newbot`, choose a name and a username (must end in `bot`, e.g. `MyOllamaBot`).
3. Copy the **HTTP API token** it gives you. This is your `TELEGRAM_BOT_TOKEN`.
4. (Optional) `/setdescription`, `/setabouttext`, `/setuserpic`.
5. **Disable privacy** if you want the bot to see messages in groups without being mentioned
   (not required — the bot works via mentions/replies regardless): `/setprivacy` → *Disabled*.
   Leaving privacy *Enabled* is fine and more private; users just need to `@mention` or reply.

---

## 🔑 2. Get a free Ollama Cloud key

1. Go to **https://ollama.com** and sign in (or create a free account).
2. Open **https://ollama.com/settings/keys**.
3. Click **Create key** and copy it.
4. In a **private chat** with your bot, send:
   ```
   /setkey YOUR_KEY_HERE
   ```
   Keys are stored per Telegram user ID in the bot's database and are **never** shown or logged.

> In groups, **every participant must set their own key** (via DM). The bot always
> uses the key of the person who triggered the message.

---

## 🗄 2.5 Set up the remote database (required)

This bot does **not** use SQLite. On Render the filesystem is ephemeral, so all
data lives in a remote MySQL or PostgreSQL database.

1. Create a free database:
   - **MySQL:** [Aiven free tier](https://aiven.io), FreeSQLDatabase, or db4free.
   - **Postgres:** [Neon](https://neon.tech) or [Supabase](https://supabase.com)
     free tier (more reliable long-term than Render's 90-day free Postgres).
2. Build the `DATABASE_URL`:
   - MySQL:    `mysql+aiomysql://user:pass@host:3306/dbname`
   - Postgres: `postgresql+asyncpg://user:pass@host:5432/dbname`
3. Set `DATABASE_URL` as an environment variable. Tables are created
   automatically on first launch.

> Aiven MySQL requires SSL — append `?ssl=true` (or the provider's SSL query
> params) to the URL. Neon/Supabase Postgres work out of the box with the
> `postgresql+asyncpg://` scheme.

## 🚀 3. Deploy on Render.com (free tier)

### Option A — Background Worker (recommended)
Free workers run continuously and are not subject to the 15-minute spin-down of
free Web Services.

1. Push this folder to a GitHub repo (or use Render's "Deploy from existing repo").
2. In Render, **New → Background Worker**.
3. Connect the repo, set:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python bot.py`
   - **Plan:** Free
4. Add the environment variables:
   - **`TELEGRAM_BOT_TOKEN`** (paste your BotFather token) — required.
   - **`DATABASE_URL`** — required. A remote MySQL/Postgres URL (see "Database" below).
   - **`KLIPY_API_KEY`** — optional (enables the GIF tool).
   You can leave everything else at defaults, or copy `render.yaml` and deploy via
   "Blueprint" for a one-click setup.
5. Deploy. Logs should show `Logged in as @YourBot` and `Bot is running`.

### Option B — Web Service (needs a pinger)
1. Same as above but choose **Web Service**, set `ENABLE_WEB_SERVER=true`.
2. Render free web services sleep after 15 min idle → add a free uptime monitor
   (e.g. [UptimeRobot](https://uptimerobot.com)) pinging `https://<your-app>.onrender.com/health`
   every 5–10 minutes to keep it awake.
3. The bot still uses long-polling (not webhooks) in this setup.

> **Database:** Render's filesystem is ephemeral, so this bot uses a **remote
> database** (not SQLite). Set `DATABASE_URL` to a MySQL or PostgreSQL connection
> string (see "Database setup" below). All keys, memory and settings live there.

---

## 💬 Commands

| Command | Where | Description |
|---------|-------|-------------|
| `/start` | anywhere | Welcome & setup instructions. |
| `/help` | anywhere | Full command & usage help. |
| `/setkey <key>` | **DM only** | Save your personal Ollama Cloud key. |
| `/delkey` | **DM only** | Remove your stored key. |
| `/model` | anywhere | List free models and pick one (group: admin-only change). |
| `/personality [text]` | DM / group* | View or set the personality. In groups only admins can set the group one. |
| `/lang [code]` | anywhere | Set your preferred reply language. |
| `/format [none\|html\|md]` | anywhere | Reply formatting (plain / HTML / MarkdownV2). |
| `/clear` or `/new` | anywhere | Clear this chat's memory. |
| `/id` | anywhere | Show your Telegram user ID and the current chat ID. |
| `/stats` | anywhere | Show current model, memory size estimate, key status. |

> **Context-aware command menu:** the bot calls `setMyCommands` at startup with
Telegram scopes. In **private chats** the menu includes `/setkey` and `/delkey`;
in **groups** those are hidden (keys are personal and DM-only) and admins see
the same reduced set. The `/` button therefore shows the right commands automatically.

---

## 🧰 Tools the bot uses

The model triggers tools through a simple, reliable text protocol (it never needs
to reveal these to the user):

- **Web search** — model includes `[SEARCH: query]`; the bot fetches results and
  re-asks the model so it can answer with fresh information.
- **GIF** — model includes `[GIF: term]`; the bot sends an animation as a separate
  message via the **Klipy** API. Set `KLIPY_API_KEY` to enable it (free production
  key at https://klipy.com/docs); without a key the GIF tool is disabled gracefully.
- **File** — model wraps content in `[FILE:name.txt] ... [/FILE]`; the bot sends it
  as a downloadable document and removes the marker from the visible chat.

### Workspace
Each user gets a private workspace directory. The model can list, read, write,
and delete files using markers like `[WS_LIST]`, `[WS_READ:path]`,
`[WS_WRITE:path]content[/WS_WRITE]`, and `[WS_DELETE:path]`.
Paths are always relative and resolved inside the user's own workspace.

### Python eval
If `SANDBOX_ALLOW` permits, the model can execute Python code with `[EVAL]code[/EVAL]`.
Use `# REQUIRE: package` comments to request pip installs. Execution runs in a
subprocess with the user's workspace as cwd, with a configurable timeout and
output caps.

### Vision
Send a **photo** and mention/reply to the bot. The image is downloaded and passed
to a vision-capable model (`DEFAULT_VISION_MODEL`, or any model whose name contains
"vision"/"llava"/etc.). If your selected model isn't vision-capable, the bot
automatically switches to the vision default for that image.

## 🔒 Security notes

- **API keys are only accepted in private chats** (`/setkey` rejects group usage).
- Keys are stored in the database **keyed by Telegram user ID** and used only for
  that user's requests.
- Logs **never** print tokens (a secret-redaction filter scrubs them).
- All user-facing errors are friendly and **never leak stack traces, paths, or keys**.
- Rate limiting (`RATE_LIMIT_PER_USER_SECONDS`) throttles per-user abuse.
- The `ADMIN_IDS` env var grants extra admin powers (e.g. forcing group defaults).
- Workspace paths are resolved safely; `..` traversal, absolute paths, and symlinks
  are rejected.
- **Python eval is NOT a secure sandbox.** It runs with the bot's privileges and
  has no memory/CPU quota. Use `SANDBOX_ALLOW` to restrict it to trusted users only.
  When disabled, the model never sees workspace/eval tool instructions.

---

## ⚙️ Environment variables

See `.env.example`. Highlights:

| Variable | Default | Purpose |
|----------|---------|---------|
| `TELEGRAM_BOT_TOKEN` | — | **Required.** BotFather token. |
| `ADMIN_IDS` | — | Comma-separated extra-admin user IDs. |
| `DEFAULT_MODEL` | `gpt-oss:20b` | Model when none chosen. |
| `DEFAULT_VISION_MODEL` | `llama3.2-vision` | Model for image understanding. |
| `OLLAMA_BASE_URL` | `https://ollama.com` | Ollama Cloud base URL. |
| `FREE_MODELS` | built-in list | Models shown by `/model`. |
| `DATABASE_URL` | — | **Required.** Remote MySQL/Postgres URL (e.g. `mysql+aiomysql://...`). |
| `KLIPY_API_KEY` | — | Optional. Enables the GIF tool (free key at https://klipy.com/docs). |
| `ENABLE_WEB_SERVER` | `false` | Start `/health` endpoint. |
| `PORT` | `8080` | Health server port. |
| `MAX_MEMORY_MESSAGES` | `40` | Max retained messages before trimming. |
| `SUMMARY_TRIGGER_TOKENS` | `6000` | Token estimate to trigger summarization. |
| `RATE_LIMIT_PER_USER_SECONDS` | `3` | Min seconds between a user's requests. |
| `STREAM_RESPONSES` | `true` | Progressive edits while generating. |
| `LOG_LEVEL` | `INFO` | Logging verbosity. |
| `SANDBOX_ALLOW` | — | Optional. Comma-separated user/group IDs allowed to use workspace & eval. `true` / `1` / `all` = everyone. Empty = disabled. |

---

## 🖥 Local development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill TELEGRAM_BOT_TOKEN
python bot.py
```

The bot connects with long-polling. Tables are created automatically in your
remote database on first launch (set `DATABASE_URL`).

---

## 📌 Notes & limitations (free tier)

- Ollama Cloud free usage is **rate-limited / daily-capped**. When a user hits the
  limit the bot shows a clear message and explains the typical ~24h reset.
- If the live model list can't be fetched, `/model` falls back to the built-in
  `FREE_MODELS` catalogue (override with `FREE_MODELS`).
- The Klipy GIF endpoint is free and key-less but best-effort; if it's unavailable
  the bot tells the user the GIF couldn't be fetched rather than failing.
- Summarization and web search consume the user's own Ollama quota.

---

## 🛠 Extending

- **Add a tool:** implement a method in `tools.py`, add a marker in
  `personality.TOOL_INSTRUCTION`, and handle it in `handlers._generate`.
- **New command:** add a `cmd_*` coroutine in `handlers.py` and register it in
  `register_handlers`.
- **Swap storage:** implement the same interface as `storage.py` (e.g. Postgres).

Enjoy your bot! 🎉
