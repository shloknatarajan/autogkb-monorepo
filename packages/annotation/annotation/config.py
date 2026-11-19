"""Environment configuration for the standalone annotation package."""

from pathlib import Path

from dotenv import find_dotenv, load_dotenv


def load_environment() -> None:
    """Load the workspace .env without overriding explicit process settings."""
    repository_env = Path(__file__).resolve().parents[3] / ".env"
    if repository_env.is_file():
        load_dotenv(repository_env, override=False)
        return

    discovered_env = find_dotenv(usecwd=True)
    if discovered_env:
        load_dotenv(discovered_env, override=False)
