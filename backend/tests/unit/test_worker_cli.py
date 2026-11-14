from __future__ import annotations

import pytest

from mera.infrastructure.queue.app import QUEUES
from mera.workers.main import _parse
from mera.workers.tasks import BLUEPRINTS


def test_default_is_every_queue() -> None:
    assert _parse([]).queue_list == list(QUEUES)


def test_subset_is_accepted() -> None:
    assert _parse(["--queues", "publish, accounts"]).queue_list == ["publish", "accounts"]


def test_unknown_queue_is_rejected() -> None:
    with pytest.raises(SystemExit):
        _parse(["--queues", "publish,nonsense"])


def test_every_registered_task_targets_a_known_queue() -> None:
    for blueprint in BLUEPRINTS.values():
        for task in blueprint.tasks.values():
            assert task.queue in QUEUES, task.name
