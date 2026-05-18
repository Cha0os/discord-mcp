import json
import os
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent


def _parse_items(result) -> list[dict]:
    return [
        json.loads(item.text)
        for item in result.content
        if isinstance(item, TextContent)
    ]


def _check_error(result) -> None:
    if result.isError:
        first = result.content[0] if result.content else None
        error_text = first.text if isinstance(first, TextContent) else "Unknown error"
        raise Exception(f"Tool failed: {error_text[:200]}")


def _valid_server_id(server_id: str) -> bool:
    """Discord snowflake IDs are 17-19 digits."""
    return server_id.isdigit() and 17 <= len(server_id) <= 19


def _make_server_params(config) -> StdioServerParameters:
    return StdioServerParameters(
        command="uv",
        args=["run", "python", "main.py"],
        env={
            "DISCORD_EMAIL": config.email,
            "DISCORD_PASSWORD": config.password,
            "DISCORD_HEADLESS": "true",
        },
    )


@pytest.mark.integration
@pytest.mark.browser
@pytest.mark.slow
@pytest.mark.asyncio
async def test_mcp_get_servers_tool(real_config):
    """Test the get_servers MCP tool via proper MCP client."""
    async with stdio_client(_make_server_params(real_config)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            result = await session.call_tool("get_servers", {})
            assert result.content, "No content in result"
            _check_error(result)

            servers_data = _parse_items(result)
            assert isinstance(servers_data, list)
            assert len(servers_data) > 0

            print(f"\nFound {len(servers_data)} servers:")
            for s in servers_data:
                print(f"  {s['name']} (ID: {s['id']})")

            assert servers_data[0]["id"] is not None
            assert servers_data[0]["name"] is not None


@pytest.mark.integration
@pytest.mark.browser
@pytest.mark.slow
@pytest.mark.asyncio
async def test_mcp_get_channels_tool(real_config):
    """Test the get_channels MCP tool — discovers a real server from get_servers."""
    async with stdio_client(_make_server_params(real_config)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # Discover a real accessible server first
            servers_result = await session.call_tool("get_servers", {})
            _check_error(servers_result)
            servers = _parse_items(servers_result)
            valid_servers = [s for s in servers if _valid_server_id(s["id"])]
            if not valid_servers:
                pytest.skip("No valid Discord servers found via get_servers")

            server_id = valid_servers[0]["id"]
            print(f"\nUsing server: {valid_servers[0]['name']} (ID: {server_id})")

            result = await session.call_tool("get_channels", {"server_id": server_id})
            assert result.content, "No content in result"
            _check_error(result)

            channels_data = _parse_items(result)
            assert isinstance(channels_data, list)
            assert len(channels_data) > 0, f"No channels found in server {server_id}"

            print(f"Found {len(channels_data)} channels:")
            for ch in channels_data:
                assert "id" in ch
                assert "name" in ch
                assert "type" in ch
                print(f"  {ch['name']} (ID: {ch['id']}, type: {ch['type']})")


@pytest.mark.integration
@pytest.mark.browser
@pytest.mark.slow
@pytest.mark.asyncio
async def test_mcp_send_message_tool(real_config):
    """Test the send_message MCP tool.

    Requires DISCORD_TEST_SERVER_ID and DISCORD_TEST_CHANNEL_ID env vars
    to avoid accidentally posting to random channels.
    """
    server_id = os.getenv("DISCORD_TEST_SERVER_ID")
    channel_id = os.getenv("DISCORD_TEST_CHANNEL_ID")
    if not server_id or not channel_id:
        pytest.skip(
            "Set DISCORD_TEST_SERVER_ID and DISCORD_TEST_CHANNEL_ID to run this test"
        )

    test_message = "hi from discord mcp fastmcp test"

    async with stdio_client(_make_server_params(real_config)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            print(f"\nSending to server {server_id}, channel {channel_id}")
            result = await session.call_tool(
                "send_message",
                {
                    "server_id": server_id,
                    "channel_id": channel_id,
                    "content": test_message,
                },
            )
            assert result.content, "No content in result"
            _check_error(result)

            first = result.content[0]
            assert isinstance(first, TextContent)
            response_data = json.loads(first.text)

            assert isinstance(response_data, dict)
            assert "message_ids" in response_data
            assert "status" in response_data
            assert "chunks" in response_data
            assert response_data["status"] == "sent"
            assert len(response_data["message_ids"]) >= 1
            print(
                f"Sent {response_data['chunks']} message(s), IDs: {response_data['message_ids']}"
            )


@pytest.mark.integration
@pytest.mark.browser
@pytest.mark.slow
@pytest.mark.asyncio
async def test_mcp_read_messages_tool(real_config):
    """Test the read_messages MCP tool — discovers a real server and text channel."""
    async with stdio_client(_make_server_params(real_config)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # Discover a real server
            servers_result = await session.call_tool("get_servers", {})
            _check_error(servers_result)
            servers = _parse_items(servers_result)
            valid_servers = [s for s in servers if _valid_server_id(s["id"])]
            if not valid_servers:
                pytest.skip("No valid Discord servers found via get_servers")

            server_id = valid_servers[0]["id"]
            print(f"\nUsing server: {valid_servers[0]['name']} (ID: {server_id})")

            # Discover a text channel (type 0) in that server
            channels_result = await session.call_tool(
                "get_channels", {"server_id": server_id}
            )
            _check_error(channels_result)
            channels = _parse_items(channels_result)
            text_channels = [ch for ch in channels if ch.get("type") == "0"]
            if not text_channels:
                pytest.skip(f"No text channels found in server {server_id}")

            channel_id = text_channels[0]["id"]
            print(f"Using channel: {text_channels[0]['name']} (ID: {channel_id})")

            # Read messages from that channel
            result = await session.call_tool(
                "read_messages",
                {
                    "server_id": server_id,
                    "channel_id": channel_id,
                    "hours_back": 8760,
                    "max_messages": 20,
                },
            )
            assert result.content, "No content in result - expected to find messages"
            _check_error(result)

            messages_data = _parse_items(result)
            assert isinstance(messages_data, list)
            assert len(messages_data) > 0, (
                "Expected at least one message in the past year"
            )

            print(
                f"\n=== Read {len(messages_data)} messages from channel {channel_id} ==="
            )
            for i, msg in enumerate(messages_data, 1):
                content_preview = msg.get("content", "")[:100]
                print(f"Message {i}: [{msg.get('author_name')}] {content_preview}")
            print("=" * 50)

            for msg in messages_data:
                assert "id" in msg
                assert "content" in msg
                assert "author_name" in msg
                assert "timestamp" in msg
                assert "attachments" in msg
