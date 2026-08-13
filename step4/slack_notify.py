"""STEP4 진행 알림용 Slack notifier.

토큰은 코드나 config에 저장하지 않고 환경변수로만 받는다.
알림 종류는 세 가지만 보낸다.
1) 군집 완료 요약
2) 오류/재시도
3) 전체 종료 요약
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any
from urllib import error, request


@dataclass
class _ConditionSummary:
    root_cause_primary: str | None
    root_cause_secondary: str | None
    schema_valid: bool | None
    semantic_valid: bool | None
    completion_tokens: int | None


@dataclass
class SlackNotifier:
    logger: logging.Logger
    bot_token: str | None
    channel_id: str | None
    enabled: bool = field(init=False)
    _buffer: dict[str, dict[str, _ConditionSummary]] = field(default_factory=dict)
    _sent_clusters: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.enabled = bool(self.bot_token and self.channel_id)
        if self.enabled:
            self.logger.info("Slack notifier 활성화: 군집 완료/오류/종료 요약 전송")
        else:
            self.logger.info("Slack notifier 비활성화: SLACK_BOT_TOKEN 또는 SLACK_CHANNEL_ID 없음")

    @classmethod
    def from_env(cls, logger: logging.Logger) -> "SlackNotifier":
        return cls(
            logger=logger,
            bot_token=os.environ.get("SLACK_BOT_TOKEN"),
            channel_id=os.environ.get("SLACK_CHANNEL_ID"),
        )

    def record_cluster_result(self, cluster_id: str, condition: str, flat_row: dict[str, Any]) -> None:
        if not self.enabled or cluster_id in self._sent_clusters:
            return
        if condition not in {"identity_on", "identity_off"}:
            return

        per_cluster = self._buffer.setdefault(cluster_id, {})
        per_cluster[condition] = _ConditionSummary(
            root_cause_primary=flat_row.get("root_cause_primary"),
            root_cause_secondary=flat_row.get("root_cause_secondary"),
            schema_valid=flat_row.get("schema_valid"),
            semantic_valid=flat_row.get("semantic_valid"),
            completion_tokens=flat_row.get("completion_tokens"),
        )
        if {"identity_on", "identity_off"} <= set(per_cluster.keys()):
            self._post_cluster_summary(cluster_id, per_cluster)
            self._sent_clusters.add(cluster_id)

    def record_retry(
        self,
        *,
        cluster_id: str,
        stage: str,
        condition: str,
        attempt_idx: int,
        attempts_total: int,
        completion_tokens: int,
    ) -> None:
        if not self.enabled:
            return
        text = (
            f"*STEP4 재시도 알림*: `{cluster_id}`\n"
            f"- stage: `{stage}`\n"
            f"- condition: `{condition}`\n"
            f"- retry: {attempt_idx}/{attempts_total}\n"
            f"- completion_tokens: {completion_tokens}"
        )
        self._post_text(text, context=f"retry:{cluster_id}:{stage}:{condition}:{attempt_idx}")

    def record_error(
        self,
        *,
        cluster_id: str,
        stage: str,
        condition: str,
        parse_error: str | None,
        schema_errors: list[str],
        semantic_errors: list[str],
    ) -> None:
        if not self.enabled:
            return
        text = (
            f"*STEP4 오류 알림*: `{cluster_id}`\n"
            f"- stage: `{stage}`\n"
            f"- condition: `{condition}`\n"
            f"- parse_error: {parse_error or 'N/A'}\n"
            f"- schema_errors: {schema_errors[:3] or ['없음']}\n"
            f"- semantic_errors: {semantic_errors[:3] or ['없음']}"
        )
        self._post_text(text, context=f"error:{cluster_id}:{stage}:{condition}")

    def record_run_complete(
        self,
        *,
        doc_count: int,
        failure_count: int,
        started_at: str | None,
        finished_at: str | None,
        report_path: str,
    ) -> None:
        if not self.enabled:
            return
        text = (
            "*STEP4 전체 종료*\n"
            f"- 처리 군집 수: {doc_count}\n"
            f"- 실패 건수: {failure_count}\n"
            f"- 시작: {started_at}\n"
            f"- 종료: {finished_at}\n"
            f"- report: `{report_path}`"
        )
        self._post_text(text, context="run_complete")

    def _post_cluster_summary(self, cluster_id: str, per_cluster: dict[str, _ConditionSummary]) -> None:
        on = per_cluster["identity_on"]
        off = per_cluster["identity_off"]
        text = (
            f"*STEP4 cluster 완료*: `{cluster_id}`\n"
            f"- `identity_on` 주원인: {on.root_cause_primary or 'N/A'}\n"
            f"- `identity_on` 부원인: {on.root_cause_secondary or '[]'}\n"
            f"- `identity_on` 유효성: schema={on.schema_valid} semantic={on.semantic_valid} "
            f"tokens={on.completion_tokens}\n"
            f"- `identity_off` 주원인: {off.root_cause_primary or 'N/A'}\n"
            f"- `identity_off` 부원인: {off.root_cause_secondary or '[]'}\n"
            f"- `identity_off` 유효성: schema={off.schema_valid} semantic={off.semantic_valid} "
            f"tokens={off.completion_tokens}"
        )
        self._post_text(text, context=f"cluster:{cluster_id}")

    def _post_text(self, text: str, *, context: str) -> None:
        payload = json.dumps(
            {"channel": self.channel_id, "text": text},
            ensure_ascii=False,
        ).encode("utf-8")
        req = request.Request(
            "https://slack.com/api/chat.postMessage",
            data=payload,
            headers={
                "Authorization": f"Bearer {self.bot_token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=10) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            if not body.get("ok"):
                self.logger.warning("Slack 알림 전송 실패(%s): %s", context, body)
            else:
                self.logger.info("Slack 알림 전송 완료(%s)", context)
        except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            self.logger.warning("Slack 알림 전송 예외(%s): %s", context, exc)
