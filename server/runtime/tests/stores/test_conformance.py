"""Every agent-store facet, held to its port's conformance suite on SQLite and on PostgreSQL.

Same assertions, same facet code: what differs is only the database underneath, which is what the suites prove.
PostgreSQL runs when ``LOAMZ_TEST_POSTGRES_URL`` reaches a server; otherwise those cases skip.
"""

from __future__ import annotations

import pytest

from loamz.runtime.kernel.runtime.persistence.store import RuntimeStore
from loamz.runtime.stores import AgentStore
from loamz.runtime.testing.conformance.file_store import FileStoreConformance
from loamz.runtime.testing.conformance.graph_store import GraphStoreConformance
from loamz.runtime.testing.conformance.runtime_store import NOW, RuntimeStoreConformance
from loamz.runtime.testing.conformance.short_term_memory import ShortTermMemoryConformance
from loamz.runtime.testing.conformance.task_store import TaskStoreConformance
from loamz.runtime.testing.conformance.thread_store import ThreadStoreConformance
from loamz.runtime.testing.conformance.workspace_store import WorkspaceStoreConformance
from loamz.runtime.testing.doubles import MemoryFileStore


class TestThreads(ThreadStoreConformance):
    @pytest.fixture
    async def store(self, db):
        return AgentStore(db).threads


class TestTasks(TaskStoreConformance):
    @pytest.fixture
    async def store(self, db):
        return AgentStore(db).tasks


class TestGraph(GraphStoreConformance):
    @pytest.fixture
    async def store(self, db):
        return AgentStore(db).graph


class TestSessionState(ShortTermMemoryConformance):
    @pytest.fixture
    async def store(self, db):
        return AgentStore(db).session_state


class TestWorkspaces(WorkspaceStoreConformance):
    @pytest.fixture
    async def store(self, db):
        return AgentStore(db).workspaces


class TestRuntimeStore(RuntimeStoreConformance):
    @pytest.fixture
    async def store(self, db):
        runtime = RuntimeStore(db, clock=lambda: NOW)
        await runtime.start()
        return runtime


class TestMemoryFileStoreDouble(FileStoreConformance):
    @pytest.fixture
    async def store(self):
        return MemoryFileStore()
