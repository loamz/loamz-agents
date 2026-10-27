"""loamz.runtime.kernel.context — The context window: building it from a thread, compacting it, counting its tokens."""

from __future__ import annotations

from loamz.runtime.kernel.context.builder import DefaultContextBuilder
from loamz.runtime.kernel.context.compaction.coordinator import (
    CompactionCoordinator,
    DefaultCompactionCoordinator,
)
from loamz.runtime.kernel.context.compaction.pipeline import CompactionPipeline
from loamz.runtime.kernel.context.compaction.presets import build_token_budget_pipeline
from loamz.runtime.kernel.context.compaction.selective_tool_call import (
    SelectiveToolCallCompactionStrategy,
)
from loamz.runtime.kernel.context.compaction.sliding_window import SlidingWindowCompaction
from loamz.runtime.kernel.context.compaction.summarization import SummarizationCompaction
from loamz.runtime.kernel.context.compaction.threshold import ThresholdCheckpointStrategy
from loamz.runtime.kernel.context.compaction.token_budget_composed import (
    TokenBudgetComposedStrategy,
)
from loamz.runtime.kernel.context.compaction.tool_result import ToolResultCompactionStrategy
from loamz.runtime.kernel.context.compaction.truncation import TruncationStrategy
from loamz.runtime.kernel.context.context import AgentContext, ContextConfig
from loamz.runtime.kernel.context.history import (
    AncestryCheckpointResolver,
    DefaultHistoryResolver,
    ThreadStore,
    project_messages,
)
from loamz.runtime.kernel.context.protocols import (
    CompactionContext,
    CompactionPhase,
    CompactionResult,
    CompactionStrategy,
    ContextBuilder,
    ContextWindow,
)

__all__ = [
    "AgentContext",
    "AncestryCheckpointResolver",
    "CompactionContext",
    "CompactionCoordinator",
    "CompactionPhase",
    "CompactionPipeline",
    "CompactionResult",
    "CompactionStrategy",
    "ContextBuilder",
    "ContextConfig",
    "ContextWindow",
    "DefaultCompactionCoordinator",
    "DefaultContextBuilder",
    "DefaultHistoryResolver",
    "ThreadStore",
    "SelectiveToolCallCompactionStrategy",
    "SlidingWindowCompaction",
    "SummarizationCompaction",
    "ThresholdCheckpointStrategy",
    "TokenBudgetComposedStrategy",
    "ToolResultCompactionStrategy",
    "TruncationStrategy",
    "build_token_budget_pipeline",
    "project_messages",
]
