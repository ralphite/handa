"""Storage-format surface of the contract: paths, event store, session ids.

Facade over src.storage. The file layouts these expose cover session dirs,
runtime event JSONL, attachments, and browser state.
"""
from __future__ import annotations

from ..storage.paths import attachments_dir as attachments_dir
from ..storage.paths import browser_dir as browser_dir
from ..storage.paths import resolve_storage_root as resolve_storage_root
from ..storage.paths import runtime_events_path as runtime_events_path
from ..storage.paths import session_dir as session_dir
from ..storage.paths import sessions_dir as sessions_dir
from ..storage.runtime_event_store import RuntimeEventStore as RuntimeEventStore
from ..storage.session_service import create_session_id as create_session_id
