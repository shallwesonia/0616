from copy import deepcopy
from uuid import UUID

import pytest
from sqlalchemy import select

from backend.app.db_models import SiteMapRecord
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


def test_database_store_seeds_multi_robot_scenario(tmp_path):
    store = create_store(tmp_path)

    robot_codes = [robot.robotId for robot in store.robots()]
    assert robot_codes[:3] == ["robot-001", "robot-002", "robot-003"]

    scenario = store.list_scenarios()[0]
    assert scenario.robotCodes == ["robot-001", "robot-002", "robot-003"]
    assert [robot["robotCode"] for robot in scenario.robots] == ["robot-001", "robot-002", "robot-003"]

    run = store.create_simulation_run(SimulationRunCreate(scenarioId=scenario.scenarioId, name="multi robot seed"))
    state = store.get_current_state(run.runId)
    assert state is not None
    assert [robot["robotId"] for robot in state.robotStates] == ["robot-001", "robot-002", "robot-003"]


def test_create_action_targets_requested_robot_code(tmp_path):
    store = create_store(tmp_path)
    scenario = store.list_scenarios()[0]
    run = store.create_simulation_run(SimulationRunCreate(scenarioId=scenario.scenarioId, name="multi robot action"))

    action = store.create_action(
        ActionCreate(
            runId=run.runId,
            robotCode="robot-002",
            command="where",
            params={},
        )
    )

    assert action is not None
    assert action.robotCode == "robot-002"

    with pytest.raises(ValueError, match="unknown robotCode"):
        store.create_action(
            ActionCreate(
                runId=run.runId,
                robotCode="robot-missing",
                command="where",
                params={},
            )
        )


def test_database_store_seeds_and_publishes_map(tmp_path):
    store = create_store(tmp_path)
    current_map = store.current_map()
    assert [group.id for group in current_map.pathGroups] == ["path-group-a", "path-group-b"]
    assert current_map.pathEdges[0].pathGroupId == "path-group-a"
    assert current_map.pathEdges[1].pathGroupId == "path-group-b"
    assert store.validate_map(current_map) == []

    targets = store.list_targets()
    path_group_targets = [target for target in targets if target.targetType == "pathGroup"]
    assert {target.targetId for target in path_group_targets} == {"path-group-a", "path-group-b"}

    updated_map = current_map.model_copy(deep=True)
    updated_map.name = "Database map"

    draft_id = store.save_draft(updated_map)
    assert store.draft_map(draft_id).name == "Database map"
    assert store.validate_map(store.draft_map(draft_id)) == []

    published_result = store.publish_draft(draft_id)
    assert published_result is not None
    published, target_sync = published_result
    assert published is not None
    assert published.name == "Database map"
    assert target_sync["updated"] >= 1
    assert store.current_map().name == "Database map"

    health = store.storage_health()
    assert health["status"] == "ok"
    assert health["backend"] == "sqlite"
    assert health["workspaceId"] == str(WORKSPACE_ID)
    assert health["draftCount"] == 1


def test_publish_draft_syncs_target_registry_to_current_map(tmp_path):
    store = create_store(tmp_path)
    current_map = store.current_map()
    updated_map = current_map.model_copy(deep=True)
    source_station = next(item for item in updated_map.objects if item.id == "station-1")
    new_station = source_station.model_copy(
        update={"id": "station-new", "name": "New Station", "x": 880, "y": 260}
    )
    updated_map.objects = [item for item in updated_map.objects if item.id != "station-1"]
    updated_map.objects.append(new_station)

    draft_id = store.save_draft(updated_map)
    published_result = store.publish_draft(draft_id)
    assert published_result is not None
    published, target_sync = published_result

    assert published is not None
    assert target_sync["created"] >= 1
    assert target_sync["inactivated"] >= 1
    expected_map_target_ids = {
        *{item.id for item in published.objects},
        *{edge.id for edge in published.pathEdges},
        *{group.id for group in published.pathGroups},
    }
    active_map_targets = [
        target
        for target in store.list_targets(status="active")
        if target.metadata.get("source") == "map"
    ]
    assert {target.targetId for target in active_map_targets} == expected_map_target_ids
    assert all(target.version == published.configVersion for target in active_map_targets)
    assert store.get_target("station-1").status == "inactive"
    assert store.get_target("station-new").pose.x == 880
    assert store.validate_scenario("default-site-a").ok


def test_map_validation_rejects_invalid_path_group_bindings(tmp_path):
    store = create_store(tmp_path)
    invalid_map = store.current_map().model_copy(deep=True)
    invalid_map.pathEdges[0].pathGroupId = "missing-path-group"
    invalid_map.pathGroups[0].allowedRobotCodes = ["robot-missing"]

    issues = store.validate_map(invalid_map)
    assert any("references missing path group" in issue for issue in issues)
    assert any("references missing robot" in issue for issue in issues)


def test_legacy_map_read_adds_default_path_groups(tmp_path):
    store = create_store(tmp_path)
    with store.database.session() as session:
        row = session.scalar(
            select(SiteMapRecord).where(
                SiteMapRecord.workspace_id == WORKSPACE_ID,
                SiteMapRecord.status == "active",
            )
        )
        assert row is not None
        legacy_map = deepcopy(row.map_json)
        legacy_map.pop("pathGroups", None)
        for edge in legacy_map["pathEdges"]:
            edge.pop("pathGroupId", None)
            edge.pop("sequence", None)
        row.map_json = legacy_map

    current_map = store.current_map()
    assert [group.id for group in current_map.pathGroups] == ["path-group-a", "path-group-b"]
    assert current_map.pathEdges[0].pathGroupId == "path-group-a"
    assert current_map.pathEdges[1].pathGroupId == "path-group-b"


def test_legacy_custom_map_read_segments_edges_by_order(tmp_path):
    store = create_store(tmp_path)
    with store.database.session() as session:
        row = session.scalar(
            select(SiteMapRecord).where(
                SiteMapRecord.workspace_id == WORKSPACE_ID,
                SiteMapRecord.status == "active",
            )
        )
        assert row is not None
        legacy_map = deepcopy(row.map_json)
        legacy_map.pop("pathGroups", None)
        legacy_map["pathEdges"][0]["id"] = "custom-edge-a"
        legacy_map["pathEdges"][1]["id"] = "custom-edge-b"
        for edge in legacy_map["pathEdges"]:
            edge.pop("pathGroupId", None)
            edge.pop("sequence", None)
        row.map_json = legacy_map

    current_map = store.current_map()
    assert [group.id for group in current_map.pathGroups] == ["path-group-1", "path-group-2"]
    assert current_map.pathGroups[0].edgeIds == ["custom-edge-a"]
    assert current_map.pathGroups[0].allowedRobotCodes == ["robot-001"]
    assert current_map.pathGroups[1].allowedRobotCodes == ["robot-002"]
    assert current_map.pathEdges[0].pathGroupId == "path-group-1"
    assert current_map.pathEdges[1].pathGroupId == "path-group-2"


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


def test_stop_action_is_platform_lifecycle_only(tmp_path):
    store = create_store(tmp_path)
    scenario = store.list_scenarios()[0]
    run = store.create_simulation_run(SimulationRunCreate(scenarioId=scenario.scenarioId, name="stop semantics"))
    task = store.create_simulation_task(
        run.runId,
        SimulationTaskCreate(goal="Move before platform stop", input={"command": "goto_pose", "target": {"x": 760, "y": 420}}),
    )
    assert task is not None
    action = store.create_action(
        ActionCreate(
            runId=run.runId,
            taskId=task.taskId,
            command="goto_pose",
            params={"x": 760, "y": 420, "z": 0, "yaw": 0},
        )
    )
    assert action is not None
    issued = store.mark_action_issued(action.actionId, "CMD-STOP-SEM-001", None, {"mqttPublished": False})
    assert issued is not None
    before_command_messages = [message.messageId for message in store.query_messages(message_type="command", limit=100)]

    stopped = store.stop_action(action.actionId)

    assert stopped is not None
    assert stopped.status == "Stopped"
    after_command_messages = [message.messageId for message in store.query_messages(message_type="command", limit=100)]
    assert after_command_messages == before_command_messages
    trace = store.get_trace(action.traceId)
    assert any(span.operation == "action.stopped" for span in trace.spans)


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
    assert replay.message.source == "simulation-replay"
    assert replay.message in store.list_run_messages(run.runId, limit=20)
    assert replay.message.payload["data"]["sandbox"] is True

    offline = store.inject_simulation_event(
        run.runId,
        SimulationEventCreate(eventType="robot.offline", targetType="robot", targetId="robot-001", severity="critical"),
    )
    assert offline is not None
    assert offline.event == "robot.offline"
    assert offline.source == "simulation-console"
    assert offline.category == "Alert"

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
