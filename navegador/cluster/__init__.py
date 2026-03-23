"""
navegador.cluster — infrastructure for agent swarms and distributed coordination.

Modules:
    core          — ClusterManager: Redis/SQLite graph sync
    pubsub        — GraphNotifier: real-time change notifications via pub/sub
    taskqueue     — TaskQueue: work assignment for agent swarms
    partitioning  — WorkPartitioner: community-based work partitioning
    sessions      — SessionManager: branch-isolated session namespacing
    locking       — DistributedLock: Redis-backed mutual exclusion (#49)
    checkpoint    — CheckpointManager: graph snapshot / rollback (#50)
    observability — SwarmDashboard: agent + task + graph metrics (#51)
    messaging     — MessageBus: agent-to-agent async messaging (#52)
    fossil_live   — FossilLiveAdapter: ATTACH DATABASE integration (#57)
"""

from navegador.cluster.checkpoint import CheckpointManager
from navegador.cluster.core import ClusterManager
from navegador.cluster.fossil_live import FossilLiveAdapter
from navegador.cluster.locking import DistributedLock, LockTimeout
from navegador.cluster.messaging import Message, MessageBus
from navegador.cluster.observability import SwarmDashboard
from navegador.cluster.partitioning import Partition, WorkPartitioner
from navegador.cluster.pubsub import EventType, GraphNotifier
from navegador.cluster.sessions import SessionManager
from navegador.cluster.taskqueue import Task, TaskQueue, TaskStatus

__all__ = [
    # v0.6 cluster infrastructure (#20, #32, #46, #47, #48)
    "ClusterManager",
    "EventType",
    "GraphNotifier",
    "Partition",
    "SessionManager",
    "Task",
    "TaskQueue",
    "TaskStatus",
    "WorkPartitioner",
    # v0.6 additional infrastructure (#49, #50, #51, #52, #57)
    "CheckpointManager",
    "DistributedLock",
    "FossilLiveAdapter",
    "LockTimeout",
    "Message",
    "MessageBus",
    "SwarmDashboard",
]
