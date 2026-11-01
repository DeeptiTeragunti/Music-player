"""Drive Claude Code from Discord. Only the owner's messages are accepted.

Plain messages are sent to Claude Code (headless, in PROJECT_DIR), and follow-ups
continue the same conversation. Special commands:
  !status  - git status of the project
  !new     - start a fresh Claude Code conversation
  !stop    - stop the running Claude Code job

Env vars: DISCORD_TOKEN, DISCORD_OWNER_ID (your Discord user ID).
"""
import asyncio
import os
import shutil
import subprocess
from pathlib import Path

import discord

TOKEN = os.environ["DISCORD_TOKEN"]
OWNER_ID = int(os.environ["DISCORD_OWNER_ID"])
PROJECT_DIR = Path(os.environ.get("PROJECT_DIR", Path(__file__).resolve().parent.parent))
CLAUDE = shutil.which("claude") or "claude"
# ponytail: fixed tool allowlist keeps headless runs from stalling on permission prompts.
ALLOWED_TOOLS = ["Read", "Write", "Edit", "Glob", "Grep", "Bash(git *)", "Bash(npm *)", "Bash(node *)", "Bash(python *)"]
DISCORD_LIMIT = 1900

lock = asyncio.Lock()
has_session = False
current_proc = None


def chunks(text):
    text = text.strip() or "(no output)"
    for i in range(0, len(text), DISCORD_LIMIT):
        yield f"```\n{text[i:i + DISCORD_LIMIT]}\n```"


async def run_claude(prompt):
    global has_session, current_proc
    args = [CLAUDE, "-p", prompt, "--allowedTools", *ALLOWED_TOOLS]
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


async def run_git_status():
    proc = await asyncio.create_subprocess_exec(
        "git", "status", "--short", cwd=PROJECT_DIR,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    return out.decode(errors="replace")


intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)


@client.event
async def on_message(message):
    global has_session
    if message.author.bot or message.author.id != OWNER_ID:
        return
    text = message.content.strip()
    if not text:
        return

    if text == "!stop":
        if current_proc:
            current_proc.kill()
            await message.channel.send("Stopped the running job.")
        else:
            await message.channel.send("Nothing is running.")
        return
    if text == "!new":
        has_session = False
        await message.channel.send("Next message starts a fresh Claude Code conversation.")
        return
    if text == "!status":
        for piece in chunks(await run_git_status()):
            await message.channel.send(piece)
        return

    if lock.locked():
        await message.channel.send("Still working on the previous message. Use !stop to cancel it.")
        return
    async with lock:
        await message.channel.send("Working on it...")
        output = await run_claude(text)
        for piece in chunks(output):
            await message.channel.send(piece)


client.run(TOKEN)
