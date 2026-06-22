import json
from uuid import UUID

from redis.exceptions import RedisError

from backend.app.runtime_cache import RuntimeCache
from backend.app.schemas import MessageRecord, RobotState


class FakeRedis:
    def __init__(self):
        self.hashes = {}
        self.lists = {}
        self.published = []

    def pipeline(self, transaction=True):
        return self

    def delete(self, *keys):
        for key in keys:
            self.hashes.pop(key, None)
            self.lists.pop(key, None)
        return self

    def hset(self, key, field=None, value=None, mapping=None):
        values = self.hashes.setdefault(key, {})
        if mapping:
            values.update(mapping)
        elif field is not None:
            values[field] = value
        return self

    def hvals(self, key):
        return list(self.hashes.get(key, {}).values())

    def rpush(self, key, *values):
        self.lists.setdefault(key, []).extend(values)
        return self

    def lpush(self, key, value):
        self.lists.setdefault(key, []).insert(0, value)
        return self

    def ltrim(self, key, start, stop):
        self.lists[key] = self.lists.get(key, [])[start : stop + 1]
        return self

    def lrange(self, key, start, stop):
        return self.lists.get(key, [])[start : stop + 1]

    def expire(self, key, seconds):
        return self

    def publish(self, channel, payload):
        self.published.append((channel, payload))
        return self

    def execute(self):
        return []

    def ping(self):
        return True


class BrokenRedis:
    def hvals(self, key):
        raise RedisError("redis unavailable")

    def lrange(self, key, start, stop):
        raise RedisError("redis unavailable")

    def ping(self):
        raise RedisError("redis unavailable")


def robot(state="idle"):
    return RobotState(
        robotId="DOG-CACHE",
        robotType="machine-dog",
        state=state,
        x=10,
        y=20,
        progress=0,
        currentAction="where",
        updatedAt="2026-06-22T00:00:00+00:00",
    )


def message(index):
    return MessageRecord(
        messageId=f"MSG-{index}",
        messageType="event",
        source="device",
        topic="factory/dogs/DOG-CACHE/result",
        createdAt=f"2026-06-22T00:00:0{index}+00:00",
        payload={"event": "pose.updated", "robotCode": "DOG-CACHE"},
    )


def test_runtime_cache_uses_workspace_keys_and_bounded_message_buffer():
    client = FakeRedis()
    workspace_id = UUID("00000000-0000-0000-0000-000000000088")
    cache = RuntimeCache(None, workspace_id, client=client, message_limit=2, ttl_seconds=60)

    cache.warm([robot()], [message(2), message(1)])
    cache.set_robot(robot("executing"))
    cache.append_message(message(3))

    assert cache.prefix == f"sim:workspace:{workspace_id}"
    assert cache.robots()[0].state == "executing"
    assert [item.messageId for item in cache.messages(10)] == ["MSG-3", "MSG-2"]
    assert [json.loads(payload)["type"] for _, payload in client.published] == [
        "robot.state",
        "message.created",
    ]
    assert cache.health()["status"] == "ok"


def test_runtime_cache_reports_degraded_and_allows_store_fallback():
    cache = RuntimeCache(
        None,
        UUID("00000000-0000-0000-0000-000000000077"),
        client=BrokenRedis(),
    )

    assert cache.robots() is None
    assert cache.messages(20) is None
    assert cache.health()["status"] == "degraded"
