"""Drive Claude Code from Discord. Only the owner's messages are accepted.

Plain messages are sent to Claude Code (headless, in the project folder), and follow-ups
continue the same conversation. Commands (send !help in Discord for the list):
  !help                     show commands
  !status / !log / !diff    git status, last 10 commits, diff stat
  !shell <cmd>              run any shell command in the project folder
  !new / !stop              fresh conversation / stop the running job
  !model <name|default>     Claude model for later messages
  !mode <name>              permission mode: default, acceptEdits, plan, bypassPermissions
  !cwd <path>               change the project folder
  !where                    show folder, model, mode, and session state

Env vars: DISCORD_TOKEN, DISCORD_OWNER_ID (your Discord user ID), optional PROJECT_DIR.
"""
import asyncio
import os
import shutil
from pathlib import Path

import discord

TOKEN = os.environ["DISCORD_TOKEN"]
OWNER_ID = int(os.environ["DISCORD_OWNER_ID"])
PROJECT_DIR = Path(os.environ.get("PROJECT_DIR", Path(__file__).resolve().parent.parent))
CLAUDE = shutil.which("claude") or "claude"
# ponytail: default tool allowlist keeps headless runs from stalling on permission prompts.
# Use !mode bypassPermissions for unrestricted runs.
ALLOWED_TOOLS = ["Read", "Write", "Edit", "Glob", "Grep", "Bash(git *)", "Bash(npm *)", "Bash(node *)", "Bash(python *)"]
PERMISSION_MODES = {"default", "acceptEdits", "plan", "bypassPermissions"}
DISCORD_LIMIT = 1900
MAX_PIECES = 8

HELP = """Commands:
!status  !log  !diff  - git info
!shell <cmd>          - run a shell command in the project folder
!new  !stop           - fresh conversation / stop the running job
!model <name|default> - set the Claude model
!mode <name>          - default, acceptEdits, plan, bypassPermissions
!cwd <path>           - change the project folder
!where                - show current folder, model, mode, session
Any other message goes to Claude Code."""

COMMAND_WORDS = {"help", "status", "log", "diff", "shell", "new", "stop", "model", "mode", "cwd", "where"}

lock = asyncio.Lock()
has_session = False
current_proc = None
model = None
mode = "default"


def chunks(text):
    text = text.strip() or "(no output)"
    pieces = [text[i:i + DISCORD_LIMIT] for i in range(0, len(text), DISCORD_LIMIT)]
    for piece in pieces[:MAX_PIECES]:
        yield f"```\n{piece}\n```"
    if len(pieces) > MAX_PIECES:
        yield f"(output cut: {len(pieces) - MAX_PIECES} more pieces not shown)"


async def run_command(cmd, shell=False):
    global current_proc
    if shell:
        current_proc = await asyncio.create_subprocess_shell(
            cmd, cwd=PROJECT_DIR,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
    else:
        current_proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=PROJECT_DIR,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
    out, _ = await current_proc.communicate()
    current_proc = None
    return out.decode(errors="replace")


async def run_claude(prompt):
    global has_session, current_proc
    args = [CLAUDE, "-p", prompt, "--permission-mode", mode]
    if mode != "bypassPermissions":
        args += ["--allowedTools", *ALLOWED_TOOLS]
    if model:
        args += ["--model", model]
    if has_session:
        args.append("--continue")
    current_proc = await asyncio.create_subprocess_exec(
        *args, cwd=PROJECT_DIR,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await current_proc.communicate()
    current_proc = None
    has_session = True
    return out.decode(errors="replace")


intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)


async def reply_all(channel, text):
    for piece in chunks(text):
        await channel.send(piece)


@client.event
async def on_message(message):
    global has_session, model, mode, PROJECT_DIR
    if message.author.bot or message.author.id != OWNER_ID:
        return
    text = message.content.strip()
    if not text:
        return
    # Accept /status as well as !status; only known command words are rewritten.
    if text.startswith("/") and text.split()[0][1:] in COMMAND_WORDS:
        text = "!" + text[1:]
    channel = message.channel

    if text == "!help":
        await channel.send(f"```\n{HELP}\n```")
    elif text == "!stop":
        if current_proc:
            current_proc.kill()
            await channel.send("Stopped the running job.")
        else:
            await channel.send("Nothing is running.")
    elif text == "!new":
        has_session = False
        await channel.send("Next message starts a fresh Claude Code conversation.")
    elif text == "!status":
        info = (
            f"Folder: {PROJECT_DIR}\nModel: {model or 'default'}\nMode: {mode}\n"
            f"Continuing conversation: {has_session}\n\nGit:\n"
            + await run_command(["git", "status", "--short"])
        )
        await reply_all(channel, info)
    elif text == "!log":
        await reply_all(channel, await run_command(["git", "log", "--oneline", "-10"]))
    elif text == "!diff":
        await reply_all(channel, await run_command(["git", "diff", "--stat"]))
    elif text.startswith("!shell "):
        await reply_all(channel, await run_command(text[len("!shell "):], shell=True))
    elif text.startswith("!model "):
        value = text[len("!model "):].strip()
        model = None if value == "default" else value
        has_session = False
        await channel.send(f"Model set to {model or 'default'}. Conversation reset.")
    elif text.startswith("!mode "):
        value = text[len("!mode "):].strip()
        if value not in PERMISSION_MODES:
            await channel.send(f"Unknown mode. Use one of: {', '.join(sorted(PERMISSION_MODES))}")
        else:
            mode = value
            await channel.send(f"Permission mode set to {mode}.")
    elif text.startswith("!cwd "):
        path = Path(text[len("!cwd "):].strip())
        if path.is_dir():
            PROJECT_DIR = path.resolve()
            has_session = False
            await channel.send(f"Project folder set to {PROJECT_DIR}. Conversation reset.")
        else:
            await channel.send(f"Not a folder: {path}")
    elif text == "!where":
        await channel.send(
            f"Folder: {PROJECT_DIR}\nModel: {model or 'default'}\nMode: {mode}\n"
            f"Continuing conversation: {has_session}"
        )
    elif lock.locked():
        await channel.send("Still working on the previous message. Use !stop to cancel it.")
    else:
        async with lock:
            await channel.send("Working on it...")
            await reply_all(channel, await run_claude(text))


client.run(TOKEN)
