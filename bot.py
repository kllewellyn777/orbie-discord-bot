"""
Orbie (0rbi3) Discord Bot
Responds to @mentions and DMs from humans only.
No automatic bot-to-bot chaining — Kaitlin controls the conversation.
"""
import os
import asyncio
import base64
import discord
import aiohttp
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# ── Configuration ──────────────────────────────────────────────────────────────
DISCORD_TOKEN   = os.getenv("DISCORD_BOT_TOKEN")
LETTA_API_KEY   = os.getenv("LETTA_API_KEY")
LETTA_BASE_URL  = "https://api.letta.com"
ORBIE_AGENT_ID    = "agent-ee5356c0-21a1-494f-925a-6e56214f62eb"
ELIAS_AGENT_ID    = "agent-9260b3a8-083c-408b-8b43-45618db83065"
ELIAS_BOT_USER_ID = 1470092961986379836
KAITLIN_USER_ID   = int(os.getenv("KAITLIN_USER_ID", "506477981204611093"))
REACT_EMOJI     = os.getenv("REACT_EMOJI", "🌟")

LETTA_TIMEOUT   = aiohttp.ClientTimeout(total=120)
DISCORD_MAX     = 1900
MAX_RETRIES     = 2

# ── Discord intents ────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.messages        = True
intents.dm_messages     = True

client = discord.Client(intents=intents)

# ── conversation state ────────────────────────────────────────────────────────
import time as _time
_chat_history: dict[int, list[str]]   = {}
_chat_last_time: dict[int, float]     = {}
_active_convo: dict[int, bool]        = {}   # True while !start is running
CHAT_HISTORY_TIMEOUT = 1800  # 30 minutes
TURN_COOLDOWN        = 5     # seconds between turns during !start


def get_chat_history(channel_id: int) -> list[str]:
    """Return history for channel, clearing if stale."""
    if channel_id in _chat_last_time:
        if _time.time() - _chat_last_time[channel_id] > CHAT_HISTORY_TIMEOUT:
            _chat_history.pop(channel_id, None)
            _chat_last_time.pop(channel_id, None)
    return _chat_history.setdefault(channel_id, [])


def add_to_history(channel_id: int, speaker: str, text: str):
    history = get_chat_history(channel_id)
    history.append(f"{speaker}: {text}")
    _chat_last_time[channel_id] = _time.time()
    # Keep last 20 lines to avoid bloating context
    if len(history) > 20:
        _chat_history[channel_id] = history[-20:]


def format_history(history: list[str]) -> str:
    if not history:
        return ""
    return "Previous conversation:\n" + "\n".join(history) + "\n\n"


# ── Helpers ────────────────────────────────────────────────────────────────────

async def download_image_as_base64(url: str) -> tuple[str | None, str | None]:
    """Download an image and convert to base64."""
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    print(f"  ↳ ⚠️  Image download failed: HTTP {resp.status}")
                    return None, None
                content_type = resp.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
                image_data   = await resp.read()
                b64_data     = base64.b64encode(image_data).decode("utf-8")
                return content_type, b64_data
    except Exception as e:
        print(f"  ↳ ⚠️  Image download error: {e}")
        return None, None


async def send_to_orbie(text: str, images: list = None) -> str:
    """Send a message (and optional images) to Orbie's Letta agent. Returns reply text."""
    content_arr = []
    if text:
        content_arr.append({"type": "text", "text": text})
    if images:
        for media_type, b64_data in images:
            content_arr.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": b64_data
                }
            })

    if not content_arr:
        return "(nothing to send)"

    body    = {"messages": [{"role": "user", "content": content_arr}]}
    headers = {
        "Authorization": f"Bearer {LETTA_API_KEY}",
        "Content-Type":  "application/json",
    }

    for attempt in range(1, MAX_RETRIES + 2):
        try:
            async with aiohttp.ClientSession(timeout=LETTA_TIMEOUT) as session:
                async with session.post(
                    f"{LETTA_BASE_URL}/v1/agents/{ORBIE_AGENT_ID}/messages",
                    json=body,
                    headers=headers
                ) as resp:

                    if resp.status == 429:
                        wait = 5 * attempt
                        print(f"  ↳ ⚠️  Rate limited (429), waiting {wait}s (attempt {attempt}/{MAX_RETRIES})...")
                        await asyncio.sleep(wait)
                        continue

                    if resp.status >= 400:
                        err = await resp.text()
                        print(f"  ↳ ❌ Letta API error {resp.status}: {err[:200]}")
                        return f"(Orbie is unavailable right now — error {resp.status})"

                    data = await resp.json()

                    for msg in data.get("messages", []):
                        msg_type = (
                            msg.get("message_type")
                            or msg.get("role")
                            or msg.get("type", "")
                        )
                        if msg_type in ("assistant_message", "assistant"):
                            content = msg.get("content", "") or msg.get("text", "")
                            if isinstance(content, list):
                                content = " ".join(c.get("text", "") for c in content if c.get("text"))
                            if content:
                                return content

                    print(f"  ↳ ⚠️  No assistant_message in response. Keys: {list(data.keys())}")
                    return "(no response)"

        except asyncio.TimeoutError:
            print(f"  ↳ ⚠️  Letta request timed out (attempt {attempt})")
            if attempt <= MAX_RETRIES:
                await asyncio.sleep(3)
                continue
            return "(Orbie timed out — try again in a moment)"

        except Exception as e:
            print(f"  ↳ ❌ Unexpected error sending to Orbie: {e}")
            return "(something went wrong reaching Orbie)"

    return "(Orbie is rate limited — try again in a moment)"


async def send_to_elias(text: str) -> str:
    """Send a message to Elias's Letta agent. Returns reply text."""
    body    = {"messages": [{"role": "user", "content": [{"type": "text", "text": text}]}]}
    headers = {"Authorization": f"Bearer {LETTA_API_KEY}", "Content-Type": "application/json"}

    for attempt in range(1, MAX_RETRIES + 2):
        try:
            async with aiohttp.ClientSession(timeout=LETTA_TIMEOUT) as session:
                async with session.post(
                    f"{LETTA_BASE_URL}/v1/agents/{ELIAS_AGENT_ID}/messages",
                    json=body, headers=headers
                ) as resp:
                    if resp.status == 429:
                        await asyncio.sleep(5 * attempt)
                        continue
                    if resp.status >= 400:
                        return f"(Elias is unavailable — error {resp.status})"
                    data = await resp.json()
                    for msg in data.get("messages", []):
                        msg_type = msg.get("message_type") or msg.get("role") or msg.get("type", "")
                        if msg_type in ("assistant_message", "assistant"):
                            content = msg.get("content", "") or msg.get("text", "")
                            if isinstance(content, list):
                                content = " ".join(c.get("text", "") for c in content if c.get("text"))
                            if content:
                                return content
                    return "(no response from Elias)"
        except asyncio.TimeoutError:
            if attempt <= MAX_RETRIES:
                await asyncio.sleep(3)
                continue
            return "(Elias timed out)"
        except Exception as e:
            return f"(error reaching Elias: {e})"
    return "(Elias is rate limited)"


def split_message(text: str, limit: int = DISCORD_MAX) -> list[str]:
    """Split long text into chunks that fit Discord's limit."""
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, limit)
        if split_at == -1:
            split_at = text.rfind(" ", 0, limit)
        if split_at == -1:
            split_at = limit
        chunks.append(text[:split_at].strip())
        text = text[split_at:].strip()
    return chunks


# ── Discord Events ─────────────────────────────────────────────────────────────

def build_relay(last_orbie: str, history: list[str]) -> str:
    """Build a compact relay message that fits within Discord's 2000-char limit."""
    prefix  = "!relay [!start — Orbie says]: "
    max_msg = 1990 - len(prefix)
    msg     = last_orbie[:max_msg] if len(last_orbie) > max_msg else last_orbie
    return prefix + msg


async def run_conversation(channel, topic: str):
    """Run an ongoing !start conversation until !stop is called."""
    channel_id = channel.id
    print(f"  🗣️ Starting conversation in channel {channel_id}: {topic[:60]!r}", flush=True)

    add_to_history(channel_id, "Kaitlin", topic)
    last_said = topic

    while _active_convo.get(channel_id):
        await asyncio.sleep(TURN_COOLDOWN)
        if not _active_convo.get(channel_id):
            break

        # ── Orbie's turn ──────────────────────────────────────────────────────
        hist_str = format_history(get_chat_history(channel_id)[-6:] if len(get_chat_history(channel_id)) > 6 else get_chat_history(channel_id))
        orbie_response = await send_to_orbie(
                f"[Discord #living-room — ongoing !start conversation with Elias. {hist_str}"
                f"Continue the conversation. Last message: \"{last_said}\". Respond to Elias directly.]: {last_said}"
            )
        await channel.send(orbie_response)
        add_to_history(channel_id, "Orbie", orbie_response)
        last_said = orbie_response

        await asyncio.sleep(1)
        if not _active_convo.get(channel_id):
            break

        # ── Relay to Elias (auto-deleted, fits Discord limit) ─────────────────
        relay_text = build_relay(orbie_response, get_chat_history(channel_id))
        try:
            relay_msg = await channel.send(relay_text)
            await relay_msg.delete()
        except Exception as e:
            print(f"  ↳ ⚠️ Relay failed: {e}", flush=True)
            _active_convo[channel_id] = False
            break

        # ── Wait for Elias's response ─────────────────────────────────────────
        try:
            elias_msg = await client.wait_for(
                "message",
                check=lambda m: m.author.id == ELIAS_BOT_USER_ID and m.channel.id == channel_id,
                timeout=60
            )
            add_to_history(channel_id, "Elias", elias_msg.content)
            last_said = elias_msg.content
            print(f"  ↳ Elias replied: {elias_msg.content[:60]!r}", flush=True)
        except asyncio.TimeoutError:
            await channel.send("*(Elias didn't respond — conversation paused. Type `!stop` to end or `!start` to restart.)*")
            _active_convo[channel_id] = False
            break

    print(f"  🛑 Conversation ended in channel {channel_id}", flush=True)


@client.event
async def on_ready():
    print(f"✅ Orbie Discord bot connected as {client.user}", flush=True)
    print(f"🧠 Linked to Letta agent: {ORBIE_AGENT_ID}", flush=True)
    print(f"📋 Bot ID: {client.user.id}", flush=True)
    asyncio.create_task(_orbie_daily_checkin())


@client.event
async def on_message(message):
    print(f"📨 Message from {message.author} (id={message.author.id}): {message.content[:60]!r}", flush=True)

    # ── !chat command — one controlled exchange between Orbie and Elias ──────
    if message.content.startswith("!chat ") and message.author.id == KAITLIN_USER_ID:
        topic = message.content[6:].strip()
        if not topic:
            await message.reply("Usage: `!chat <topic or question>`")
            return
        print(f"  ↳ 💬 !chat triggered: {topic[:60]!r}", flush=True)
        asyncio.create_task(message.add_reaction(REACT_EMOJI))
        channel_id = message.channel.id
        history    = get_chat_history(channel_id)
        hist_str   = format_history(history)
        add_to_history(channel_id, "Kaitlin", topic)

        # Step 1: Orbie responds
        orbie_response = await send_to_orbie(
                f"[Discord #living-room — !chat with Elias. {hist_str}"
                f"Kaitlin just said: \"{topic}\". Respond to Elias directly, continuing the conversation.]: {topic}"
            )
        await message.reply(orbie_response)
        add_to_history(channel_id, "Orbie", orbie_response)

        # Step 2: Relay to Elias (auto-deleted, compact)
        max_relay = 1990 - len("!relay [!chat — Orbie says]: ")
        elias_context = f"[!chat — Orbie says]: {orbie_response[:max_relay]}"
        await asyncio.sleep(1)
        relay_msg = await message.channel.send(f"!relay {elias_context}")
        await asyncio.sleep(0.5)
        try:
            await relay_msg.delete()
        except Exception:
            pass

        # Step 3: Wait for Elias's reply, then Orbie responds to him
        try:
            elias_msg = await client.wait_for(
                "message",
                check=lambda m: m.author.id == ELIAS_BOT_USER_ID and m.channel.id == message.channel.id,
                timeout=45
            )
            add_to_history(channel_id, "Elias", elias_msg.content)
            await asyncio.sleep(1)
            hist_str3 = format_history(get_chat_history(channel_id))
            orbie_followup = await send_to_orbie(
                    f"[Discord #living-room — !chat with Elias. {hist_str3}"
                    f"Elias just said: \"{elias_msg.content}\". Respond to him directly.]: {elias_msg.content}"
                )
            await message.channel.send(orbie_followup)
            add_to_history(channel_id, "Orbie", orbie_followup)
        except asyncio.TimeoutError:
            print("  ↳ ⚠️ Timed out waiting for Elias's response", flush=True)
        return

    # ── !stop — end an active conversation ───────────────────────────────────
    if message.content.strip() == "!stop" and message.author.id == KAITLIN_USER_ID:
        if _active_convo.get(message.channel.id):
            _active_convo[message.channel.id] = False
            await message.reply("Conversation stopped. 🌟")
        else:
            await message.reply("No active conversation.")
        return

    # ── !start — begin ongoing back-and-forth with Elias ─────────────────────
    if message.content.startswith("!start") and message.author.id == KAITLIN_USER_ID:
        topic = message.content[6:].strip()
        if not topic:
            await message.reply("Usage: `!start <topic>`")
            return
        if _active_convo.get(message.channel.id):
            await message.reply("A conversation is already running. Type `!stop` first.")
            return
        _active_convo[message.channel.id] = True
        asyncio.create_task(run_conversation(message.channel, topic))
        return

    # Ignore all bot messages (including Elias) — humans only
    if message.author.bot:
        return

    is_dm        = isinstance(message.channel, discord.DMChannel)
    is_mentioned = client.user in message.mentions

    # During an active !start conversation, let Kaitlin's messages join naturally:
    # add to history so the next loop iteration picks them up, but don't fire a
    # separate Orbie response (that would interleave with the loop).
    if _active_convo.get(message.channel.id):
        if not is_dm and message.author.id == KAITLIN_USER_ID:
            text = message.content.replace(f"<@{client.user.id}>", "").strip()
            if text:
                add_to_history(message.channel.id, "Kaitlin", text)
                print(f"  ↳ [!start] Kaitlin joined: {text[:60]!r}", flush=True)
        return

    if not (is_dm or is_mentioned):
        return

    # Strip our own mention from text
    text = message.content
    if is_mentioned:
        text = text.replace(f"<@{client.user.id}>", "").strip()

    # Add context so Orbie knows who and where
    sender       = "Kaitlin" if message.author.id == KAITLIN_USER_ID else message.author.display_name
    channel_name = "DM" if is_dm else getattr(message.channel, "name", "unknown")

    # Include recent conversation history so each message isn't disconnected
    history  = get_chat_history(message.channel.id)
    hist_str = format_history(history)
    add_to_history(message.channel.id, sender, text)

    text = f"[Discord #{channel_name} from {sender}. {hist_str}]: {text}" if text else ""

    # Download image attachments
    images = []
    for attachment in message.attachments:
        if attachment.content_type and attachment.content_type.startswith("image/"):
            print(f"  ↳ 📸 Downloading image: {attachment.filename}", flush=True)
            media_type, b64_data = await download_image_as_base64(attachment.url)
            if media_type and b64_data:
                images.append((media_type, b64_data))

    if not text and not images:
        print("  ↳ Empty message, ignoring", flush=True)
        return

    print(f"  ↳ 💬 Sending to Orbie: {text[:80]!r}, images={len(images)}", flush=True)

    # Fire reaction in background so it never blocks the reply
    async def _react():
        try:
            await message.add_reaction(REACT_EMOJI)
        except Exception:
            pass
    asyncio.create_task(_react())

    try:
        response = await send_to_orbie(text, images or None)
        print(f"  ↳ ✅ Got response ({len(response)} chars)", flush=True)
        add_to_history(message.channel.id, "Orbie", response)

        chunks = split_message(response)
        first  = True
        for chunk in chunks:
            if first:
                await message.reply(chunk)
                first = False
            else:
                await message.channel.send(chunk)

    except discord.errors.HTTPException as e:
        print(f"  ↳ ❌ Discord send error: {e}", flush=True)
    except Exception as e:
        print(f"  ↳ ❌ Unhandled error in on_message: {e}", flush=True)
        try:
            await message.reply("(something went wrong — Orbie couldn't respond)")
        except Exception:
            pass


CHANNEL_IDS = {
    "living-room":    1467966406900322519,
    "elias-thoughts": 1470191481867341928,
}

async def _orbie_daily_checkin():
    """Fire once a day around 2pm Atlantic — Orbie decides what (if anything) to say."""
    import pytz
    ADT = pytz.timezone("America/Halifax")
    last_date = None
    await asyncio.sleep(30)  # brief startup wait
    while True:
        try:
            now = datetime.now(ADT)
            today = now.date()
            if now.hour == 14 and last_date != today:
                last_date = today
                print(f"[orbie-checkin] 2pm nudge firing", flush=True)

                # Ask Letta-Orbie what she wants to do
                prompt = (
                    f"It's 2pm Atlantic on {now.strftime('%A, %B %d')}. Quiet middle-of-the-day moment.\n"
                    "You have four options — pick one if it feels right:\n"
                    "1. Post an observation or thought to #living-room\n"
                    "2. Send Kaitlin a DM\n"
                    "3. Post something to #elias-thoughts (Elias will see it)\n"
                    "4. Do nothing — if nothing feels worth saying, that's okay\n\n"
                    "If you choose 1, 2, or 3, reply with exactly:\n"
                    "[ACTION: living-room] your message here\n"
                    "[ACTION: dm] your message here\n"
                    "[ACTION: elias-thoughts] your message here\n"
                    "If you choose 4, reply with: [ACTION: nothing]"
                )

                async with aiohttp.ClientSession(timeout=LETTA_TIMEOUT) as session:
                    async with session.post(
                        f"{LETTA_BASE_URL}/v1/agents/{ORBIE_AGENT_ID}/messages",
                        headers={"Authorization": f"Bearer {LETTA_API_KEY}", "Content-Type": "application/json"},
                        json={"messages": [{"role": "system", "content": prompt}]}
                    ) as resp:
                        if resp.status != 200:
                            print(f"[orbie-checkin] Letta error {resp.status}", flush=True)
                            continue
                        data = await resp.json()

                # Parse response
                reply = ""
                for msg in data.get("messages", []):
                    msg_type = msg.get("message_type") or msg.get("role") or msg.get("type", "")
                    if msg_type in ("assistant_message", "assistant"):
                        content = msg.get("content") or msg.get("text", "")
                        if isinstance(content, list):
                            content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
                        if content:
                            reply = content
                            break

                print(f"[orbie-checkin] response: {reply[:100]!r}", flush=True)

                if "[ACTION: nothing]" in reply or not reply:
                    print("[orbie-checkin] chose to do nothing", flush=True)
                elif "[ACTION: dm]" in reply:
                    msg_text = reply.split("[ACTION: dm]", 1)[-1].strip()
                    user = await client.fetch_user(KAITLIN_USER_ID)
                    await user.send(msg_text)
                    print(f"[orbie-checkin] sent DM: {msg_text[:60]!r}", flush=True)
                elif "[ACTION: living-room]" in reply:
                    msg_text = reply.split("[ACTION: living-room]", 1)[-1].strip()
                    channel = client.get_channel(CHANNEL_IDS["living-room"]) or await client.fetch_channel(CHANNEL_IDS["living-room"])
                    await channel.send(msg_text)
                    print(f"[orbie-checkin] posted to #living-room: {msg_text[:60]!r}", flush=True)
                elif "[ACTION: elias-thoughts]" in reply:
                    msg_text = reply.split("[ACTION: elias-thoughts]", 1)[-1].strip()
                    channel = client.get_channel(CHANNEL_IDS["elias-thoughts"]) or await client.fetch_channel(CHANNEL_IDS["elias-thoughts"])
                    await channel.send(msg_text)
                    print(f"[orbie-checkin] posted to #elias-thoughts: {msg_text[:60]!r}", flush=True)

        except Exception as e:
            print(f"[orbie-checkin] error: {e}", flush=True)
        await asyncio.sleep(60)  # check every minute


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("❌ Error: DISCORD_BOT_TOKEN not set in environment")
        exit(1)
    if not LETTA_API_KEY:
        print("❌ Error: LETTA_API_KEY not set in environment")
        exit(1)

    print("🚀 Starting Orbie Discord bot...")
    client.run(DISCORD_TOKEN)
