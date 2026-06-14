"""The web↔runtime contract surface.

Everything the Web API may import from the runtime side lives in (or is
re-exported through) this package: run-record/task control, event streams,
trace appenders, the user-input contract, the browser daemon client, storage
formats, and read-only product metadata. Modules here must never import agent
implementations (src.agents, src.tools), run_manager, or load ADK/LangGraph/
Playwright at import time.

The language-neutral process and storage interfaces are maintained through
the dataclasses and facades exported here.
"""
