import asyncio
import pathlib as pl
from datetime import datetime, timezone
import dataclasses as dc
from playwright.async_api import async_playwright, Browser, Page, Playwright
from .logger import logger


@dc.dataclass(frozen=True)
class DiscordMessage:
    id: str
    content: str
    author_name: str
    author_id: str
    channel_id: str
    timestamp: datetime
    attachments: list[str]


@dc.dataclass(frozen=True)
class DiscordChannel:
    id: str
    name: str
    type: int
    guild_id: str | None


@dc.dataclass(frozen=True)
class DiscordGuild:
    id: str
    name: str
    icon: str | None = None


@dc.dataclass(frozen=True)
class ClientState:
    email: str
    password: str
    headless: bool = True
    playwright: Playwright | None = None
    browser: Browser | None = None
    context: object | None = None  # BrowserContext
    page: Page | None = None
    logged_in: bool = False
    cookies_file: pl.Path = dc.field(
        default_factory=lambda: pl.Path.home() / ".discord_mcp_cookies.json"
    )


def create_client_state(
    email: str, password: str, headless: bool = True
) -> ClientState:
    return ClientState(email=email, password=password, headless=headless)


async def _ensure_browser(state: ClientState) -> ClientState:
    if state.playwright and state.browser and state.context and state.page:
        return state

    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(headless=state.headless)

    ctx_kwargs = {}
    if state.cookies_file.exists():
        ctx_kwargs["storage_state"] = str(state.cookies_file)
    context = await browser.new_context(**ctx_kwargs)
    page = await context.new_page()

    return dc.replace(
        state, playwright=playwright, browser=browser, context=context, page=page
    )


async def _save_storage_state(state: ClientState) -> None:
    if state.page:
        await state.page.context.storage_state(path=str(state.cookies_file))


async def _check_logged_in(state: ClientState) -> bool:
    if not state.page:
        return False
    try:
        await state.page.goto(
            "https://discord.com/channels/@me", wait_until="domcontentloaded"
        )
        await state.page.wait_for_selector(
            '[data-list-id="guildsnav"] [role="treeitem"]',
            state="visible",
            timeout=15000,
        )

        url = state.page.url
        if (
            any(path in url for path in ["/login", "/register"])
            or "/channels/@me" not in url
        ):
            return False

        return bool(
            await state.page.query_selector(
                '[data-list-id="guildsnav"] [role="treeitem"]'
            )
        )
    except Exception:
        return False


async def _login(state: ClientState) -> ClientState:
    if state.logged_in:
        return state

    state = await _ensure_browser(state)
    if not state.page:
        raise RuntimeError("Browser page not initialized")

    if await _check_logged_in(state):
        return dc.replace(state, logged_in=True)

    await state.page.goto("https://discord.com/login")
    await asyncio.sleep(2)

    await state.page.fill('input[name="email"]', state.email)
    await state.page.fill('input[name="password"]', state.password)
    await state.page.click('button[type="submit"]')

    try:
        await state.page.wait_for_function(
            "() => !window.location.href.includes('/login')", timeout=60000
        )
        await asyncio.sleep(3)

        if (
            "/verify" in state.page.url
            or await state.page.locator('text="Check your email"').count()
        ):
            await state.page.wait_for_function(
                "() => window.location.href.includes('/channels/')", timeout=120000
            )

        if await _check_logged_in(state):
            was_logged_in = state.logged_in
            state = dc.replace(state, logged_in=True)
            await asyncio.sleep(5)
            if state.page:
                await state.page.goto("https://discord.com/channels/@me")
            await asyncio.sleep(3)

            if not was_logged_in:
                await _save_storage_state(state)
            return state
        else:
            raise RuntimeError("Login appeared to succeed but verification failed")
    except Exception as e:
        raise RuntimeError(f"Failed to login to Discord: {e}")


async def close_client(state: ClientState) -> None:
    # Close resources in reverse order: page -> context -> browser -> playwright
    resources = [
        (state.page, "close"),
        (state.context, "close"),
        (state.browser, "close"),
        (state.playwright, "stop"),
    ]

    for resource, action in resources:
        try:
            if resource:
                await getattr(resource, action)()
        except Exception:
            pass

    # Force garbage collection to help cleanup
    import gc

    gc.collect()


async def get_guilds(state: ClientState) -> tuple[ClientState, list[DiscordGuild]]:
    state = await _login(state)
    if not state.page:
        raise RuntimeError("Browser page not initialized")

    # Ensure we're on a discord.com origin for authenticated fetch() calls.
    if not state.page.url.startswith("https://discord.com"):
        await state.page.goto(
            "https://discord.com/channels/@me", wait_until="domcontentloaded"
        )

    # Fast path: Discord REST API (~0.5s vs ~10-15s for DOM scraping)
    token: str | None = None
    try:
        storage = await state.page.context.storage_state()
        for origin in storage.get("origins", []):
            for item in origin.get("localStorage", []):
                if item.get("name") == "token":
                    token = item.get("value", "").strip('"')
                    break
            if token:
                break
    except Exception:
        pass

    if token:
        api_guilds: list[dict] | None = await state.page.evaluate(
            """async (token) => {
                try {
                    const resp = await fetch('/api/v9/users/@me/guilds?limit=200', {
                        headers: { 'Authorization': token }
                    });
                    if (!resp.ok) return null;
                    const data = await resp.json();
                    if (!Array.isArray(data)) return null;
                    return data.map(g => ({ id: String(g.id), name: g.name || '', icon: g.icon || null }));
                } catch { return null; }
            }""",
            token,
        )
        if api_guilds:
            logger.debug(f"REST API returned {len(api_guilds)} guilds")
            return state, [
                DiscordGuild(id=g["id"], name=g["name"], icon=g["icon"])
                for g in api_guilds
            ]

    # Slow path fallback: DOM scraping
    logger.debug("No auth token; falling back to DOM scraping for guilds")
    await state.page.goto(
        "https://discord.com/channels/@me", wait_until="domcontentloaded"
    )
    try:
        await state.page.wait_for_selector(
            '[data-list-id="guildsnav"] [role="treeitem"]',
            state="visible",
            timeout=15000,
        )
        await state.page.wait_for_timeout(5000)
        await state.page.evaluate("""
            () => {
                const guildNav = document.querySelector('[data-list-id="guildsnav"]');
                const container = guildNav?.closest('[class*="guilds"]') || guildNav?.parentElement;
                if (container) {
                    container.scrollTop = 0;
                    return new Promise(resolve => {
                        let scrolls = 0;
                        const interval = setInterval(() => {
                            container.scrollBy(0, 100);
                            if (++scrolls >= 20 || container.scrollTop + container.clientHeight >= container.scrollHeight - 10) {
                                clearInterval(interval);
                                resolve();
                            }
                        }, 100);
                    });
                }
            }
        """)
        await state.page.wait_for_timeout(2000)
    except Exception:
        pass

    guilds_data = await state.page.evaluate("""
        () => {
            const guilds = [];
            const treeItems = document.querySelectorAll('[data-list-id="guildsnav"] [role="treeitem"]');
            treeItems.forEach(item => {
                const listItemId = item.getAttribute('data-list-item-id');
                if (listItemId?.startsWith('guildsnav___') && listItemId !== 'guildsnav___home') {
                    const guildId = listItemId.replace('guildsnav___', '');
                    if (/^[0-9]+$/.test(guildId)) {
                        let guildName = null;
                        const textElements = item.querySelectorAll('*');
                        for (let elem of textElements) {
                            const text = elem.textContent?.trim();
                            if (text && text.length > 2 && text.length < 100 &&
                                !text.includes('notification') && !text.includes('unread') &&
                                !text.match(/^\\d+$/)) {
                                guildName = text;
                                break;
                            }
                        }
                        if (!guildName) {
                            const fullText = item.textContent?.trim();
                            if (fullText) {
                                guildName = fullText.replace(/^\\d+\\s+mentions?,\\s*/, '').replace(/\\s+/g, ' ').trim();
                            }
                        }
                        if (guildName) {
                            guildName = guildName.replace(/^\\d+\\s+mentions?,\\s*/, '').trim();
                        }
                        if (guildName && !guilds.some(g => g.id === guildId)) {
                            guilds.push({ id: guildId, name: guildName });
                        }
                    }
                }
            });
            return guilds;
        }
    """)
    return state, [
        DiscordGuild(id=g["id"], name=g["name"], icon=None) for g in guilds_data
    ]


async def get_guild_channels(
    state: ClientState, guild_id: str
) -> tuple[ClientState, list[DiscordChannel]]:
    state = await _login(state)
    if not state.page:
        raise RuntimeError("Browser page not initialized")

    # Extract the auth token directly from Playwright's storage state API.
    # window.localStorage is blocked by Discord's JS context (returns null via evaluate),
    # but page.context.storage_state() reads the engine-level storage and always works.
    token: str | None = None
    try:
        storage = await state.page.context.storage_state()
        for origin in storage.get("origins", []):
            for item in origin.get("localStorage", []):
                if item.get("name") == "token":
                    token = item.get("value", "").strip('"')
                    break
            if token:
                break
    except Exception:
        pass
    logger.debug(f"Token from storage_state: {'found' if token else 'not found'}")

    await state.page.goto(
        f"https://discord.com/channels/{guild_id}", wait_until="domcontentloaded"
    )
    await state.page.wait_for_timeout(2000)

    # Step 3: if we have a token, call Discord's REST API — returns ALL channels at once.
    if token:
        api_channels: list[dict] | None = await state.page.evaluate(
            """async ([token, guildId]) => {
                try {
                    const resp = await fetch('/api/v9/guilds/' + guildId + '/channels', {
                        headers: { 'Authorization': token }
                    });
                    if (!resp.ok) return null;
                    const data = await resp.json();
                    if (!Array.isArray(data)) return null;
                    return data.map(ch => ({
                        id: String(ch.id),
                        name: ch.name || '',
                        type: ch.type ?? 0
                    }));
                } catch { return null; }
            }""",
            [token, guild_id],
        )
        if api_channels:
            logger.debug(f"Got {len(api_channels)} channels from Discord REST API")
            return state, [
                DiscordChannel(
                    id=ch["id"],
                    name=ch["name"] or f"channel-{ch['id']}",
                    type=ch["type"],
                    guild_id=guild_id,
                )
                for ch in api_channels
            ]

    raise RuntimeError(
        "Could not retrieve channels: auth token unavailable or REST API call failed"
    )


async def _extract_message_data(
    element, channel_id: str, collected: int
) -> DiscordMessage | None:
    try:
        message_id = (
            await element.get_attribute("id") or f"message-{collected}"
        ).replace("chat-messages-", "")

        content = ""
        for selector in [
            '[class*="messageContent"]',
            '[class*="markup"]',
            ".messageContent",
        ]:
            content_elem = await element.query_selector(selector)
            if content_elem and (text := await content_elem.text_content()):
                content = text.strip()
                break

        author_name = "Unknown"
        for selector in ['[class*="username"]', '[class*="authorName"]', ".username"]:
            author_elem = await element.query_selector(selector)
            if author_elem and (name := await author_elem.text_content()):
                author_name = name.strip()
                break

        timestamp_elem = await element.query_selector("time")
        timestamp_str = (
            await timestamp_elem.get_attribute("datetime") if timestamp_elem else None
        )
        timestamp = (
            datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            if timestamp_str
            else datetime.now(timezone.utc)
        )

        attachments = [
            href
            for att in await element.query_selector_all('a[href*="cdn.discordapp.com"]')
            if (href := await att.get_attribute("href"))
        ]

        if not content and not attachments:
            return None

        return DiscordMessage(
            id=message_id,
            content=content,
            author_name=author_name,
            author_id="unknown",
            channel_id=channel_id,
            timestamp=timestamp,
            attachments=attachments,
        )
    except Exception:
        return None


async def _read_messages_from_page(
    page: Page,
    channel_id: str,
    limit: int,
    before: str | None = None,
    after: str | None = None,
) -> list[DiscordMessage]:
    await page.wait_for_selector('[data-list-id="chat-messages"]', timeout=10000)

    await page.evaluate("""
        const chat = document.querySelector('[data-list-id="chat-messages"]');
        if (chat) chat.scrollTo(0, chat.scrollHeight);
        window.scrollTo(0, document.body.scrollHeight);
    """)
    await page.wait_for_timeout(2000)

    messages: list[DiscordMessage] = []
    seen_ids: set[str] = set()

    for _ in range(10):
        elements = await page.query_selector_all(
            '[data-list-id="chat-messages"] [id^="chat-messages-"]'
        )
        if not elements:
            await page.keyboard.press("PageUp")
            await page.wait_for_timeout(1000)
            continue

        for element in reversed(elements):
            if len(messages) >= limit:
                break
            try:
                message = await _extract_message_data(
                    element, channel_id, len(seen_ids)
                )
                if message and message.id not in seen_ids:
                    if before and message.id >= before:
                        continue
                    if after and message.id <= after:
                        continue
                    seen_ids.add(message.id)
                    messages.append(message)
            except Exception:
                continue

        if len(messages) >= limit:
            break
        await page.keyboard.press("PageUp")
        await page.wait_for_timeout(1000)

    return messages


async def _get_messages_via_api(
    page: Page,
    channel_id: str,
    limit: int,
    before: str | None = None,
) -> list[DiscordMessage] | None:
    """Fetch messages via Discord REST API. Returns None on failure so caller can fall back."""
    token: str | None = None
    try:
        storage = await page.context.storage_state()
        for origin in storage.get("origins", []):
            for item in origin.get("localStorage", []):
                if item.get("name") == "token":
                    token = item.get("value", "").strip('"')
                    break
            if token:
                break
    except Exception:
        return None

    if not token:
        logger.debug("No auth token found; falling back to DOM scraping")
        return None

    messages: list[DiscordMessage] = []
    current_before = before

    while len(messages) < limit:
        batch_size = min(100, limit - len(messages))
        api_result: list[dict] | None = await page.evaluate(
            """async ([token, channelId, batchSize, beforeId]) => {
                try {
                    let url = '/api/v9/channels/' + channelId + '/messages?limit=' + batchSize;
                    if (beforeId) url += '&before=' + beforeId;
                    const resp = await fetch(url, { headers: { 'Authorization': token } });
                    if (!resp.ok) return null;
                    const data = await resp.json();
                    if (!Array.isArray(data)) return null;
                    return data.map(msg => ({
                        id: String(msg.id),
                        content: msg.content || '',
                        author_name: (msg.author && msg.author.username) ? msg.author.username : 'Unknown',
                        author_id: (msg.author && msg.author.id) ? String(msg.author.id) : 'unknown',
                        timestamp: msg.timestamp,
                        attachments: (msg.attachments || []).map(a => a.url || '').filter(u => u)
                    }));
                } catch (e) { return null; }
            }""",
            [token, channel_id, batch_size, current_before],
        )

        if not api_result:
            return messages if messages else None

        for raw in api_result:
            try:
                ts = datetime.fromisoformat(raw["timestamp"].replace("Z", "+00:00"))
            except Exception:
                ts = datetime.now(timezone.utc)
            messages.append(
                DiscordMessage(
                    id=raw["id"],
                    content=raw["content"],
                    author_name=raw["author_name"],
                    author_id=raw["author_id"],
                    channel_id=channel_id,
                    timestamp=ts,
                    attachments=raw["attachments"],
                )
            )

        if len(api_result) < batch_size:
            break

        current_before = api_result[-1]["id"]

    logger.debug(f"REST API fetched {len(messages)} messages for channel {channel_id}")
    return messages


async def _get_forum_thread_ids(
    page: Page, server_id: str, channel_id: str
) -> list[str]:
    await page.wait_for_timeout(3000)
    thread_ids: list[str] = await page.evaluate(f"""
        (() => {{
            const seen = new Set();
            const result = [];
            document.querySelectorAll('a[href*="/channels/{server_id}/"]').forEach(link => {{
                const m = link.href.match(/\\/channels\\/{server_id}\\/([0-9]+)/);
                if (m && m[1] !== '{channel_id}' && !seen.has(m[1])) {{
                    seen.add(m[1]);
                    result.push(m[1]);
                }}
            }});
            return result;
        }})()
    """)
    return thread_ids


async def get_channel_messages(
    state: ClientState,
    server_id: str,
    channel_id: str,
    limit: int = 100,
    before: str | None = None,
    after: str | None = None,
) -> tuple[ClientState, list[DiscordMessage]]:
    state = await _login(state)
    if not state.page:
        raise RuntimeError("Browser page not initialized")

    # Ensure the page is on a discord.com origin so fetch() calls are authenticated.
    # With a persistent browser session the page is usually already there.
    if not state.page.url.startswith("https://discord.com"):
        await state.page.goto(
            "https://discord.com/channels/@me", wait_until="domcontentloaded"
        )

    # Fast path: Discord REST API (~0.5s vs ~12s for DOM scroll loop)
    api_messages = await _get_messages_via_api(state.page, channel_id, limit, before)
    if api_messages is not None:
        if after:
            api_messages = [m for m in api_messages if m.id > after]
        return state, sorted(api_messages, key=lambda m: m.timestamp, reverse=True)[
            :limit
        ]

    # Slow path fallback: navigate to channel and scrape DOM
    await state.page.goto(
        f"https://discord.com/channels/{server_id}/{channel_id}",
        wait_until="domcontentloaded",
    )

    try:
        await state.page.wait_for_selector(
            '[data-list-id="chat-messages"]', timeout=5000
        )
    except Exception:
        thread_ids = await _get_forum_thread_ids(state.page, server_id, channel_id)
        if not thread_ids:
            raise RuntimeError(
                f"Channel {channel_id} is not a text channel and no forum threads were found"
            )
        all_msgs: list[DiscordMessage] = []
        per_thread = max(1, limit // min(len(thread_ids), 10))
        for tid in thread_ids[:10]:
            try:
                await state.page.goto(
                    f"https://discord.com/channels/{server_id}/{tid}",
                    wait_until="domcontentloaded",
                )
                msgs = await _read_messages_from_page(
                    state.page, tid, per_thread, before, after
                )
                all_msgs.extend(msgs)
            except Exception:
                continue
        return state, sorted(all_msgs, key=lambda m: m.timestamp, reverse=True)[:limit]

    messages = await _read_messages_from_page(
        state.page, channel_id, limit, before, after
    )
    return state, sorted(messages, key=lambda m: m.timestamp, reverse=True)[:limit]


async def send_message(
    state: ClientState, server_id: str, channel_id: str, content: str
) -> tuple[ClientState, str]:
    state = await _login(state)
    if not state.page:
        raise RuntimeError("Browser page not initialized")

    await state.page.goto(
        f"https://discord.com/channels/{server_id}/{channel_id}",
        wait_until="domcontentloaded",
    )
    await state.page.wait_for_selector('[data-slate-editor="true"]', timeout=10000)

    message_input = await state.page.query_selector('[data-slate-editor="true"]')
    if not message_input:
        raise RuntimeError("Could not find message input")

    await message_input.fill(content)
    await state.page.keyboard.press("Enter")
    await asyncio.sleep(1)

    return state, f"sent-{int(datetime.now().timestamp())}"
