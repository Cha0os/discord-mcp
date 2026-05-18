import pytest
import os
from dotenv import load_dotenv
from src.discord_mcp.config import DiscordConfig

load_dotenv()


@pytest.fixture
def real_config():
    """Provide real Discord configuration from environment."""
    email = os.getenv("DISCORD_EMAIL")
    password = os.getenv("DISCORD_PASSWORD")

    if not email or not password:
        pytest.skip(
            "Discord credentials not available. Set DISCORD_EMAIL and DISCORD_PASSWORD environment variables."
        )

    return DiscordConfig(
        email=email,
        password=password,
        headless=True,
        default_guild_ids=["780179350682599445"],
        max_messages_per_channel=50,
        default_hours_back=24,
    )


@pytest.fixture(autouse=True)
def setup_test_environment():
    """Setup test environment before each test."""
    os.environ["DISCORD_HEADLESS"] = "true"
    yield
