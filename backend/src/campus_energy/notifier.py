"""Alert notifications through Amazon SNS (email / SMS / Lambda subscribers)."""

from __future__ import annotations

import os

from .anomaly import severity_rank
from .config import Settings
from .timeutil import local_dt


class SnsNotifier:
    def __init__(self, topic_arn: str | None, settings: Settings, client=None):
        self.topic_arn = topic_arn
        self.settings = settings
        self._client = client

    @classmethod
    def from_env(cls, settings: Settings) -> "SnsNotifier":
        return cls(os.environ.get("ALERT_TOPIC_ARN"), settings)

    @property
    def client(self):
        if self._client is None:
            import boto3

            self._client = boto3.client("sns")
        return self._client

    def notify(self, alerts: list[dict]) -> int:
        """Send one message summarising every alert at or above the configured severity."""
        floor = severity_rank(self.settings.notify_min_severity)
        eligible = [a for a in alerts if severity_rank(a["severity"]) >= floor]
        if not eligible or not self.topic_arn:
            return 0
        eligible.sort(key=lambda a: -severity_rank(a["severity"]))
        subject = f"[Campus Energy] {len(eligible)} alert(s): {eligible[0]['title']}"
        subject = subject.encode("ascii", "ignore").decode()[:99]
        lines = ["Smart Campus Energy Management System detected abnormal usage:", ""]
        for a in eligible:
            when = local_dt(a["ts"], self.settings.utc_offset_s).strftime("%d %b %H:%M")
            lines += [f"[{a['severity'].upper()}] {a['title']} ({a['building_name']}) at {when}", f"    {a['message']}", ""]
        if os.environ.get("DASHBOARD_URL"):
            lines.append(f"Dashboard: {os.environ['DASHBOARD_URL']}")
        self.client.publish(TopicArn=self.topic_arn, Subject=subject, Message="\n".join(lines))
        return len(eligible)
