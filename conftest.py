"""Session setup that must run before any test package imports Basic Memory.

pytest loads this rootdir conftest before tests/conftest.py and test-int/conftest.py.
"""

import os

# Rich consoles are module globals, built when a CLI module is first imported, and
# each keeps the COLUMNS it saw then: later env changes, such as a CliRunner's
# env={"COLUMNS": ...}, cannot widen it. Under xdist that first import lands in
# whichever test a worker runs first, so CLI table and message assertions passed or
# failed by test order. Pin one wide width before anything imports the CLI (the test
# conftests import the MCP server, which imports basic_memory.cli.auth).
os.environ["COLUMNS"] = "240"
