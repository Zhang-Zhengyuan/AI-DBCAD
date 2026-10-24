"""Regression for two official sessions after closing a per-task log."""
import asyncio
import contextlib
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

# Import outside redirected streams: stdio_client's errlog default is bound here.
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from study import UPSTREAM


class MCPLoggingTests(unittest.TestCase):
    def test_two_sessions_with_separate_logs(self):
        async def exercise(root):
            for i in range(2):
                with (root / f'{i}.log').open('w') as log, contextlib.redirect_stderr(log):
                    params = StdioServerParameters(command=sys.executable,
                        args=[str(UPSTREAM / 'src/harnesses/cadquery_mcp/server.py')])
                    async with stdio_client(params) as (read, write):
                        async with ClientSession(read, write) as session:
                            await session.initialize()
                            result = await session.call_tool('inspect', {})
                            self.assertFalse(result.isError)
        with TemporaryDirectory() as root:
            asyncio.run(exercise(Path(root)))


if __name__ == '__main__':
    unittest.main()
