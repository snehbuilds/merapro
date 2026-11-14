"""Task registry. Add each queue's blueprint here as milestones land."""

from __future__ import annotations

import procrastinate

from mera.workers.tasks.accounts import blueprint as accounts_blueprint
from mera.workers.tasks.approvals import blueprint as approvals_blueprint
from mera.workers.tasks.drafting import blueprint as drafting_blueprint
from mera.workers.tasks.engagement import blueprint as engagement_blueprint
from mera.workers.tasks.ingest import blueprint as ingest_blueprint
from mera.workers.tasks.mcp import blueprint as mcp_blueprint
from mera.workers.tasks.media import blueprint as media_blueprint
from mera.workers.tasks.metrics import blueprint as metrics_blueprint
from mera.workers.tasks.privacy import blueprint as privacy_blueprint
from mera.workers.tasks.publish import blueprint as publish_blueprint
from mera.workers.tasks.system import blueprint as system_blueprint

BLUEPRINTS: dict[str, procrastinate.Blueprint] = {
    "system": system_blueprint,
    "accounts": accounts_blueprint,
    "ingest": ingest_blueprint,
    "drafting": drafting_blueprint,
    "publish": publish_blueprint,
    "approvals": approvals_blueprint,
    "metrics": metrics_blueprint,
    "media": media_blueprint,
    "engagement": engagement_blueprint,
    "mcp": mcp_blueprint,
    "privacy": privacy_blueprint,
}
