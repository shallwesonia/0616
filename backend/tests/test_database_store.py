from copy import deepcopy
from uuid import UUID

from backend.app.database_store import DatabaseStore
from backend.app.schemas import MessageRecord, RobotState


WORKSPACE_ID = UUID("00000000-0000-0000-0000-000000000099")


def create_store(tmp_path) -> DatabaseStore:
    database_path = tmp_path / "platform.db"
    return DatabaseStore(
        database_url=f"sqlite+pysqlite:///{database_path.as_posix()}",
        workspace_id=WORKSPACE_ID,
        state_path=tmp_path / "missing-state.json",
        create_schema=True,
    )


def test_database_store_seeds_and_publishes_map(tmp_path):
    store = create_store(tmp_path)
    current_map = store.current_map()
    updated_map = current_map.model_copy(deep=True)
    updated_map.name = "Database map"

    draft_id = store.save_draft(updated_map)
    assert store.draft_map(draft_id).name == "Database map"
    assert store.validate_map(store.draft_map(draft_id)) == []

    published = store.publish_draft(draft_id)
    assert published is not None
    assert published.name == "Database map"
    assert store.current_map().name == "Database map"

    health = store.storage_health()
    assert health["status"] == "ok"
    assert health["backend"] == "sqlite"
    assert health["workspaceId"] == str(WORKSPACE_ID)
    assert health["draftCount"] == 1


def test_database_store_persists_messages_and_robot_state(tmp_path):
    store = create_store(tmp_path)
    message = MessageRecord(
        messageId="EVT-DB-001",
        messageType="event",
        source="device",
        topic="factory/dogs/DOG-DB/result",
        createdAt="2026-06-22T01:02:03+00:00",
        payload={
            "event": "pose.updated",
            "commandId": "CMD-DB-001",
            "taskId": "TASK-DB-001",
            "traceId": "TRACE-DB-001",
            "robotCode": "DOG-DB",
            "data": {"x": 120, "y": 240},
        },
    )
    store.append_message(message)
    store.append_message(message)

    filtered = store.query_messages(
        robot_code="DOG-DB",
        command_id="CMD-DB-001",
        trace_id="TRACE-DB-001",
        event="pose.updated",
    )
    assert [item.messageId for item in filtered] == ["EVT-DB-001"]
    assert [item.messageId for item in store.command_trace("CMD-DB-001")] == ["EVT-DB-001"]
    assert store.last_heartbeat_at() == "2026-06-22T01:02:03+00:00"

    command = MessageRecord(
        messageId="CMD-DB-001",
        messageType="command",
        source="agent",
        topic="factory/dogs/DOG-DB/command",
        createdAt="2026-06-22T01:02:02+00:00",
        payload={"commandId": "CMD-DB-001", "robotCode": "DOG-DB", "command": "where"},
    )
    store.append_message(command)
    for index in range(201):
        store.append_message(
            MessageRecord(
                messageId=f"HEARTBEAT-{index}",
                messageType="event",
                source="device",
                topic="factory/dogs/DOG-DB/result",
                createdAt=f"2026-06-23T00:{index // 60:02d}:{index % 60:02d}+00:00",
                payload={"event": "pose.updated", "robotCode": "DOG-DB"},
            )
        )
    assert store.recent_runtime_summary()["lastCommand"]["messageId"] == "CMD-DB-001"

    robot = RobotState(
        robotId="DOG-DB",
        robotType="quadruped",
        state="executing",
        x=120,
        y=240,
        progress=45,
        currentAction="goto_pose",
        updatedAt="2026-06-22T01:02:03+00:00",
    )
    store.upsert_robot_state(robot)
    updated_robot = deepcopy(robot)
    updated_robot.state = "idle"
    updated_robot.progress = 100
    store.upsert_robot_state(updated_robot)

    robots = {item.robotId: item for item in store.robots()}
    assert robots["DOG-DB"].state == "idle"
    assert robots["DOG-DB"].progress == 100
