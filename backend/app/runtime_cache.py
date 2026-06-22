from __future__ import annotations

import json
import os
from typing import Any
from uuid import UUID

from redis import Redis
from redis.exceptions import RedisError

from .schemas import MessageRecord, RobotState


class RuntimeCache:
    def __init__(
        self,
        redis_url: str | None,
        workspace_id: UUID,
        enabled: bool = True,
        ttl_seconds: int = 86400,
        message_limit: int = 200,
        client: Any | None = None,
    ) -> None:
        self.enabled = enabled
        self.workspace_id = workspace_id
        self.ttl_seconds = ttl_seconds
        self.message_limit = message_limit
        self.prefix = f"sim:workspace:{workspace_id}"
        self.robot_key = f"{self.prefix}:runtime:robots"
        self.message_key = f"{self.prefix}:runtime:messages"
        self.event_channel = f"{self.prefix}:events"
        self.last_error: str | None = None
        if enabled and not redis_url and client is None:
            raise RuntimeError("REDIS_URL is required when REDIS_ENABLED=true")
        self.client = client
        if enabled and self.client is None:
            self.client = Redis.from_url(
                redis_url,
                decode_responses=True,
                socket_connect_timeout=1,
                socket_timeout=1,
                health_check_interval=30,
            )

    def warm(self, robots: list[RobotState], messages: list[MessageRecord]) -> None:
        if not self.enabled:
            return
        try:
            pipe = self.client.pipeline(transaction=True)
            pipe.delete(self.robot_key, self.message_key)
            if robots:
                pipe.hset(
                    self.robot_key,
                    mapping={robot.robotId: self._json(robot.model_dump()) for robot in robots},
                )
                pipe.expire(self.robot_key, self.ttl_seconds)
            if messages:
                pipe.rpush(self.message_key, *[self._json(message.model_dump()) for message in messages])
                pipe.ltrim(self.message_key, 0, self.message_limit - 1)
                pipe.expire(self.message_key, self.ttl_seconds)
            pipe.execute()
            self.last_error = None
        except RedisError as exc:
            self.last_error = str(exc)

    def set_robot(self, robot: RobotState) -> None:
        if not self.enabled:
            return
        payload = self._json(robot.model_dump())
        try:
            pipe = self.client.pipeline(transaction=True)
            pipe.hset(self.robot_key, robot.robotId, payload)
            pipe.expire(self.robot_key, self.ttl_seconds)
            pipe.publish(self.event_channel, self._json({"type": "robot.state", "data": robot.model_dump()}))
            pipe.execute()
            self.last_error = None
        except RedisError as exc:
            self.last_error = str(exc)

    def append_message(self, message: MessageRecord) -> None:
        if not self.enabled:
            return
        payload = self._json(message.model_dump())
        try:
            pipe = self.client.pipeline(transaction=True)
            pipe.lpush(self.message_key, payload)
            pipe.ltrim(self.message_key, 0, self.message_limit - 1)
            pipe.expire(self.message_key, self.ttl_seconds)
            pipe.publish(self.event_channel, self._json({"type": "message.created", "data": message.model_dump()}))
            pipe.execute()
            self.last_error = None
        except RedisError as exc:
            self.last_error = str(exc)

    def robots(self) -> list[RobotState] | None:
        if not self.enabled:
            return None
        try:
            values = self.client.hvals(self.robot_key)
            self.last_error = None
            return sorted(
                [RobotState.model_validate(json.loads(value)) for value in values],
                key=lambda robot: robot.robotId,
            )
        except (RedisError, json.JSONDecodeError, ValueError) as exc:
            self.last_error = str(exc)
            return None

    def messages(self, limit: int) -> list[MessageRecord] | None:
        if not self.enabled:
            return None
        try:
            values = self.client.lrange(self.message_key, 0, max(0, limit - 1))
            self.last_error = None
            return [MessageRecord.model_validate(json.loads(value)) for value in values]
        except (RedisError, json.JSONDecodeError, ValueError) as exc:
            self.last_error = str(exc)
            return None

    def health(self) -> dict[str, Any]:
        if not self.enabled:
            return {"status": "disabled"}
        try:
            self.client.ping()
            self.last_error = None
            return {
                "status": "ok",
                "backend": "redis",
                "workspaceId": str(self.workspace_id),
                "keyPrefix": self.prefix,
                "messageLimit": self.message_limit,
                "ttlSeconds": self.ttl_seconds,
            }
        except RedisError as exc:
            self.last_error = str(exc)
            return {
                "status": "degraded",
                "backend": "redis",
                "workspaceId": str(self.workspace_id),
                "error": self.last_error,
            }

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def runtime_cache_from_env(workspace_id: UUID) -> RuntimeCache:
    redis_url = os.getenv("REDIS_URL")
    enabled_value = os.getenv("REDIS_ENABLED")
    enabled = bool(redis_url) if enabled_value is None else enabled_value.lower() == "true"
    return RuntimeCache(
        redis_url=redis_url,
        workspace_id=workspace_id,
        enabled=enabled,
        ttl_seconds=int(os.getenv("REDIS_RUNTIME_TTL_SECONDS", "86400")),
        message_limit=int(os.getenv("REDIS_MESSAGE_BUFFER_LIMIT", "200")),
    )
