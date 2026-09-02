from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal


Language = Literal["zh", "en"]
BlockAction = Literal["translate", "copy", "source_only"]


@dataclass(frozen=True)
class BlockPlan:
    """Immutable rendering and translation decision for one source block."""

    node_id: str
    category: str
    source_text: str
    source_lang: Language
    target_lang: Language | None
    action: BlockAction
    target_text: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TranslationBundle:
    """All decisions shared by translation, HTML, validation, and GT output."""

    plans: dict[str, BlockPlan]
    dropped: dict[str, str]
    warnings: list[dict[str, Any]]

    def plan_for(self, node_id: str) -> BlockPlan | None:
        return self.plans.get(str(node_id))

    def to_dict(self) -> dict[str, Any]:
        return {
            "plans": {node_id: plan.to_dict() for node_id, plan in self.plans.items()},
            "dropped": dict(self.dropped),
            "warnings": list(self.warnings),
        }
