from copy import deepcopy
from uuid import UUID

from backend.app.database_store import DatabaseStore
from backend.app.schemas import (
    ActionCreate,
    BatchTaskCreate,
    MessageReplayCreate,
    MessageRecord,
    RobotState,
    SimulationEventCreate,
    SimulationEventRecoveryCreate,
    SimulationRunCreate,
    SimulationTaskCreate,
    SnapshotCreate,
)


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


def test_database_store_p3_simulation_run_chain(tmp_path):
    store = create_store(tmp_path)

    scenario = store.list_scenarios()[0]
    run = store.create_simulation_run(SimulationRunCreate(scenarioId=scenario.scenarioId, name="P3 test run"))
    assert run.runId.startswith("RUN-")
    assert run.status == "Draft"

    started = store.update_simulation_run_status(run.runId, "Running")
    assert started is not None
    assert started.status == "Running"

    task = store.create_simulation_task(
        run.runId,
        SimulationTaskCreate(
            goal="Move to sort station",
            input={"command": "goto_pose", "target": {"x": 760, "y": 420, "z": 0, "yaw": 0}},
        ),
    )
    assert task is not None
    assert task.activePlan is not None
    assert task.activePlan.steps[0].actionType == "goto_pose"

    action = store.create_action(
        ActionCreate(
            runId=run.runId,
            taskId=task.taskId,
            command="goto_pose",
            params={"x": 760, "y": 420, "z": 0, "yaw": 0},
        )
    )
    assert action is not None
    issued = store.mark_action_issued(
        action.actionId,
        "CMD-P3-001",
        None,
        {"mqttPublished": False, "commandId": "CMD-P3-001"},
    )
    assert issued is not None
    assert issued.status == "Issued"

    result_message = MessageRecord(
        messageId="EVT-P3-001",
        messageType="event",
        source="device",
        topic="factory/dogs/robot-001/result",
        createdAt="2026-06-23T00:00:00+00:00",
        payload={
            "schemaVersion": "1.0",
            "messageType": "event",
            "event": "task.succeeded",
            "eventId": "EVT-P3-001",
            "commandId": "CMD-P3-001",
            "taskId": task.taskId,
            "requestId": None,
            "robotCode": "robot-001",
            "traceId": task.traceId,
            "source": "device",
            "timestamp": "2026-06-23T00:00:00+00:00",
            "data": {"x": 760, "y": 420},
            "error": None,
        },
    )
    store.append_message(result_message)
    observation = store.ingest_observation_from_message(result_message)
    assert observation is not None
    assert observation.category == "Event"
    assert observation.actionId == action.actionId

    completed_action = store.get_action(action.actionId)
    assert completed_action is not None
    assert completed_action.status == "Succeeded"
    completed_task = store.get_task(task.taskId)
    assert completed_task is not None
    assert completed_task.status == "Succeeded"

    alert = store.inject_simulation_event(
        run.runId,
        SimulationEventCreate(eventType="path.blocked", targetType="path", targetId="edge-2", severity="error"),
    )
    assert alert is not None
    assert alert.category == "Alert"

    state = store.get_current_state(run.runId)
    assert state is not None
    assert state.stateVersion >= 4
    assert "edge-2" in state.resourceStates["blockedPaths"]
    assert state.activeEvents[0]["event"] == "path.blocked"

    snapshot = store.create_snapshot(run.runId, SnapshotCreate(reason="test-checkpoint"))
    assert snapshot is not None
    assert snapshot.stateVersion == state.stateVersion
    assert store.list_snapshots(run.runId)[0].snapshotId == snapshot.snapshotId

    trace = store.get_trace(task.traceId)
    assert trace.status == "Open"
    assert any(span.operation == "command.issued" for span in trace.spans)
    assert any(span.operation == "observation.task.succeeded" for span in trace.spans)

    exported = store.export_simulation_run(run.runId)
    assert exported is not None
    assert exported["manifest"]["secretPolicy"] == "secrets-excluded"
    assert exported["run"]["runId"] == run.runId
    assert exported["observations"]


def test_database_store_simulation_cockpit_enhancements(tmp_path):
    store = create_store(tmp_path)
    scenario = store.list_scenarios()[0]

    validation = store.validate_scenario(scenario.scenarioId)
    assert validation is not None
    assert validation.ok is True
    assert any(check.code == "map.integrity" for check in validation.checks)

    run = store.create_simulation_run(SimulationRunCreate(scenarioId=scenario.scenarioId, name="enhanced cockpit"))
    batch = store.create_batch_tasks(
        run.runId,
        BatchTaskCreate(
            templateId="sort-transfer",
            goal="Batch transfer",
            count=3,
            targetRange={"x": [700, 760], "y": [380, 420]},
            randomSeed=42,
        ),
    )
    assert batch is not None
    assert batch.createdCount == 3
    assert all(task.constraints["batchId"] == batch.batchId for task in batch.tasks)

    task = batch.tasks[0]
    action = store.create_action(
        ActionCreate(
            runId=run.runId,
            taskId=task.taskId,
            command="goto_pose",
            params={"x": 760, "y": 420, "z": 0, "yaw": 0},
        )
    )
    assert action is not None
    issued = store.mark_action_issued(action.actionId, "CMD-ENH-001", None, {"mqttPublished": False})
    assert issued is not None

    result_message = MessageRecord(
        messageId="EVT-ENH-001",
        messageType="event",
        source="device",
        topic="factory/dogs/robot-001/result",
        createdAt="2026-06-23T00:10:00+00:00",
        payload={
            "event": "command.accepted",
            "eventId": "EVT-ENH-001",
            "commandId": "CMD-ENH-001",
            "taskId": task.taskId,
            "robotCode": "robot-001",
            "traceId": task.traceId,
            "timestamp": "2026-06-23T00:10:00+00:00",
            "data": {},
            "error": None,
        },
    )
    store.append_message(result_message)
    store.ingest_observation_from_message(result_message)

    replay = store.replay_run_message(
        run.runId,
        "EVT-ENH-001",
        MessageReplayCreate(reason="test replay"),
    )
    assert replay is not None
    assert replay.message.payload["event"] == "message.replayed"
    assert replay.message.payload["data"]["sandbox"] is True

    alert = store.inject_simulation_event(
        run.runId,
        SimulationEventCreate(eventType="path.blocked", targetType="path", targetId="edge-2", severity="error"),
    )
    assert alert is not None
    assert "edge-2" in store.get_current_state(run.runId).resourceStates["blockedPaths"]

    recovered = store.recover_simulation_event(
        run.runId,
        SimulationEventRecoveryCreate(eventType="path.blocked", targetType="path", targetId="edge-2"),
    )
    assert recovered is not None
    state = store.get_current_state(run.runId)
    assert state is not None
    assert "edge-2" not in state.resourceStates["blockedPaths"]
    assert recovered.event == "fault.recovered"
