"""The structured report — the contract between the pipeline and the channels.

The pipeline produces a `Report`: pure semantic data (which items are P0/P1, the
trend text, the reading picks, the stats). It contains NO rendering — no Lark
cards, no Slack blocks, no markdown chrome. Each notifier renders a Report using
whatever its channel supports: Lark uses a collapsible card, Telegram falls back
to plain sections, Console prints text. The core never knows or cares.

This is what keeps "content" separate from "presentation": add a channel by
writing a renderer, not by touching the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from radar.models import Item, RunStats


@dataclass
class Report:
    """A finished daily digest, ready to be rendered by any notifier."""

    date: str
    stats: RunStats
    items: list[Item] = field(default_factory=list)  # already ranked, P0/P1 tagged
    trend: str = ""
    reading: str = ""

    @property
    def p0(self) -> list[Item]:
        return [i for i in self.items if i.priority == "P0"]

    @property
    def p1(self) -> list[Item]:
        return [i for i in self.items if i.priority != "P0"]


@dataclass
class WeeklyReport:
    """A finished weekly deep dive. `sections` is {key: markdown}; `order` is the
    (key, heading) list in display order for the active language."""

    date: str
    sections: dict[str, str]
    order: list[tuple[str, str]]
