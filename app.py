"""Maestro desktop launcher — double-click the .exe to open the control room.

No arguments => starts the web dashboard and opens your browser. Any arguments
are passed straight through to the normal CLI (run / race / delegate / demo).
"""

import sys

# Force-bundle the agent backends (they are lazy-imported at runtime, so
# PyInstaller wouldn't see them otherwise). Anthropic API backend is omitted on
# purpose (optional dependency); claude-cli/codex/ollama/openai cover everything.
import maestro.server  # noqa: F401
import maestro.race  # noqa: F401
import maestro.agents.cli_agent  # noqa: F401
import maestro.agents.autonomous  # noqa: F401
import maestro.agents.ollama_agent  # noqa: F401
import maestro.agents.openai_compat  # noqa: F401
from maestro.cli import main

if __name__ == "__main__":
    if len(sys.argv) == 1:
        sys.argv.append("serve")
    sys.exit(main())
