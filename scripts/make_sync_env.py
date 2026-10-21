"""
Writes the environment file of the sync account: only the settings the crawler, the
indexer and the news sync need, read with the same parser the application uses
(python-dotenv), so `export NAME=...`, quotes and comments are understood and nothing
else is copied. Credentials (the API's, the admin area's, SMTP, cloud) are never in it.

Usage:  python scripts/make_sync_env.py /opt/chatbot/.env > /opt/chatbot/.env.sync
"""

import sys

from dotenv import dotenv_values

ALLOWED = {
    "API_PORT", "INSTANCE_DIR", "MIGRATED_DOMAINS", "OLLAMA_BASE_URL", "OLLAMA_EMBED_MODEL",
    "OLLAMA_MODEL", "SITE_NAME", "SITE_URL", "SYNC_CONTROL_DIR",
}
ALLOWED_PREFIXES = ("CRAWL_",)


def render(values: dict) -> str:
    lines = []
    for name, value in values.items():
        if value is None or not (name in ALLOWED or name.startswith(ALLOWED_PREFIXES)):
            continue
        quoted = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
        lines.append(f'{name}="{quoted}"')
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.stdout.write(render(dotenv_values(sys.argv[1])))
