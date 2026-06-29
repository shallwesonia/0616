import os
from copy import deepcopy
from uuid import UUID

os.environ["MQTT_ENABLED"] = "false"

from fastapi.testclient import TestClient

import backend.app.main as main_module
from backend.app.database_store import DatabaseStore
from backend.app.main import app
from backend.app.schemas import ACTION_TARGET_TYPE_OPTIONS, action_command_names


def test_health_has_component_statuses():
    with TestClient(app) as client:
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        payload = response.json()
        assert payload["components"]["api"]["status"] == "ok"
        assert "storage" in payload["components"]
        assert "mqttBridge" in payload["components"]
        assert "virtualExecutor" in payload["components"]


def test_map_validation_reports_p0_errors():
    with TestClient(app) as client:
        current_map = client.get("/api/v1/maps/current").json()
        invalid_map = deepcopy(current_map)
        invalid_map["objects"][0]["id"] = invalid_map["objects"][1]["id"]
        invalid_map["objects"].append(
            {
                "id": "pathNode-isolated",
                "type": "pathNode",
                "name": "isolated",
                "x": 1180,
                "y": 740,
                "radius": 7,
                "color": "#111827",
            }
        )
        invalid_map["objects"].append(
            {
                "id": "station-outside",
                "type": "station",
                "name": "outside",
                "x": -10,
                "y": 10,
                "radius": 20,
                "color": "#dcfce7",
            }
        )
        invalid_map["pathEdges"].append(
            {
                "id": "edge-invalid",
                "from": "station-1",
                "to": "missing-node",
                "direction": "two_way",
                "capacity": 1,
            }
        )

        draft = client.post(f"/api/v1/maps/{invalid_map['id']}/drafts", json={"map": invalid_map})
        assert draft.status_code == 200
        draft_id = draft.json()["draftId"]
        validation = client.post(f"/api/v1/maps/{invalid_map['id']}/drafts/{draft_id}/validate")
        assert validation.status_code == 200
        payload = validation.json()
        assert payload["ok"] is False
        assert any("duplicated" in issue for issue in payload["issues"])
        assert any("outside map bounds" in issue for issue in payload["issues"])
        assert any("references missing path node" in issue for issue in payload["issues"])
        assert any("disconnected" in issue for issue in payload["issues"])


def test_command_endpoint_records_command_without_mqtt():
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/commands",
            json={
                "robotId": "robot-001",
                "command": "goto_pose",
                "params": {"x": 760, "y": 420},
                "issuedBy": "test",
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["commandId"].startswith("CMD-")
        assert payload["topic"] == "factory/dogs/robot-001/command"
        assert payload["payload"]["messageType"] == "command"
        assert payload["payload"]["command"] == "goto_pose"
        assert payload["payload"]["robotCode"] == "robot-001"
        assert payload["payload"]["taskId"].startswith("TASK-")
        assert payload["payload"]["requestId"] is None
        assert payload["payload"]["traceId"].startswith("TRACE-")
        assert payload["payload"]["mqttPublished"] is False
        trace = client.get(f"/api/v1/commands/{payload['commandId']}/trace")
        assert trace.status_code == 200
        assert trace.json()["messageCount"] >= 1


def test_robot_create_endpoint_adds_scenario_robot(tmp_path, monkeypatch):
    test_store = DatabaseStore(
        database_url=f"sqlite+pysqlite:///{(tmp_path / 'api-robot-create.db').as_posix()}",
        workspace_id=UUID("00000000-0000-0000-0000-000000000087"),
        state_path=tmp_path / "missing-state.json",
        create_schema=True,
    )
    monkeypatch.setattr(main_module, "store", test_store)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/robots",
            json={"robotCode": "robot-010", "robotType": "machine-dog", "x": 520, "y": 360},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["robotId"] == "robot-010"
        assert payload["x"] == 520

        duplicate = client.post(
            "/api/v1/robots",
            json={"robotCode": "robot-010", "robotType": "machine-dog", "x": 520, "y": 360},
        )
        assert duplicate.status_code == 409

        scenarios = client.get("/api/v1/scenarios").json()
        assert "robot-010" in scenarios[0]["robotCodes"]


def test_command_stop_creates_new_robot_control_action(tmp_path, monkeypatch):
    test_store = DatabaseStore(
        database_url=f"sqlite+pysqlite:///{(tmp_path / 'api-stop-semantics.db').as_posix()}",
        workspace_id=UUID("00000000-0000-0000-0000-000000000088"),
        state_path=tmp_path / "missing-state.json",
        create_schema=True,
    )
    monkeypatch.setattr(main_module, "store", test_store)
    with TestClient(app) as client:
        run_response = client.post(
            "/api/v1/simulation-runs",
            json={"scenarioId": "default-site-a", "name": "stop command semantics"},
        )
        assert run_response.status_code == 200
        run_id = run_response.json()["runId"]

        task_response = client.post(
            f"/api/v1/simulation-runs/{run_id}/tasks",
            json={
                "goal": "Create an action before stop",
                "input": {"command": "goto_pose", "target": {"x": 760, "y": 420, "z": 0, "yaw": 0}},
            },
        )
        assert task_response.status_code == 200
        task_id = task_response.json()["taskId"]

        invalid_action_response = client.post(
            "/api/v1/actions",
            json={
                "runId": run_id,
                "taskId": task_id,
                "robotCode": "robot-001",
                "command": "goto_pose",
                "params": {"x": 760},
            },
        )
        assert invalid_action_response.status_code == 400
        assert "params.y" in invalid_action_response.json()["detail"]

        invalid_target_type_response = client.post(
            "/api/v1/actions",
            json={
                "runId": run_id,
                "taskId": task_id,
                "robotCode": "robot-001",
                "command": "pick",
                "params": {"targetType": "unknown", "targetId": "box-001"},
            },
        )
        assert invalid_target_type_response.status_code == 400
        assert "targetType" in invalid_target_type_response.json()["detail"]

        original_action_response = client.post(
            "/api/v1/actions",
            json={
                "runId": run_id,
                "taskId": task_id,
                "robotCode": "robot-001",
                "command": "goto_pose",
                "params": {"x": 760, "y": 420, "z": 0, "yaw": 0},
            },
        )
        assert original_action_response.status_code == 200
        original_action = original_action_response.json()

        stop_response = client.post(
            "/api/v1/actions",
            json={
                "runId": run_id,
                "taskId": task_id,
                "robotCode": "robot-001",
                "command": "stop",
                "params": {},
            },
        )
        assert stop_response.status_code == 200
        stop_action = stop_response.json()
        assert stop_action["actionId"] != original_action["actionId"]
        assert stop_action["command"] == "stop"
        assert stop_action["commandId"].startswith("CMD-")
        assert stop_action["status"] == "Issued"

        original_after_stop = client.get(f"/api/v1/actions/{original_action['actionId']}")
        assert original_after_stop.status_code == 200
        assert original_after_stop.json()["status"] == "Issued"

        messages = client.get("/api/v1/messages", params={"commandId": stop_action["commandId"], "limit": 20})
        assert messages.status_code == 200
        payload = messages.json()
        assert any(
            message["topic"] == "factory/dogs/robot-001/command"
            and message["payload"]["command"] == "stop"
            for message in payload
        )


def test_connections_contract_has_lan_protocols():
    with TestClient(app) as client:
        response = client.get("/api/v1/connections")
        assert response.status_code == 200
        payload = response.json()
        assert payload["services"]["frontend"]["protocol"] == "HTTP"
        assert payload["services"]["api"]["protocol"] == "HTTP REST"
        assert payload["services"]["websocket"]["protocol"] == "WebSocket"
        assert payload["services"]["mqtt"]["protocol"].startswith("MQTT")
        assert payload["services"]["mqtt"]["port"] == 18830
        assert payload["services"]["mqtt"]["commandTopic"] == "factory/dogs/{robotCode}/command"
        assert payload["services"]["mqtt"]["resultTopic"] == "factory/dogs/{robotCode}/result"
        assert payload["services"]["mqtt"]["supportedCommands"] == action_command_names()
        assert "action.progress" in payload["services"]["mqtt"]["resultEvents"]
        assert "fault.recovered" in payload["services"]["mqtt"]["resultEvents"]


def test_action_command_specs_expose_standard_params():
    with TestClient(app) as client:
        response = client.get("/api/v1/action-command-specs")
        assert response.status_code == 200
        payload = response.json()
        commands = {item["command"]: item for item in payload}
        assert set(commands) == set(action_command_names())
        assert {"x", "y", "z", "yaw", "speed", "tolerance"}.issubset(commands["goto_pose"]["fields"])
        assert commands["goto_pose"]["fields"]["pathGroupId"]["type"] == "pathGroup"
        assert commands["where"]["fields"]["queryMode"]["options"] == ["pose", "state", "full"]
        assert commands["stop"]["fields"]["stopScope"]["options"] == ["current_action", "task", "robot"]
        assert commands["pick"]["defaults"]["targetType"] == "cargo"
        assert commands["place"]["defaults"]["targetType"] == "station"
        assert commands["inspect"]["defaults"]["targetType"] == "inspectionPoint"
        assert commands["pick"]["fields"]["targetType"]["options"] == ACTION_TARGET_TYPE_OPTIONS
        assert commands["pick"]["fields"]["targetId"]["label"] == "目标对象ID"


def test_target_registry_validates_action_targets(tmp_path, monkeypatch):
    test_store = DatabaseStore(
        database_url=f"sqlite+pysqlite:///{(tmp_path / 'api-target-registry.db').as_posix()}",
        workspace_id=UUID("00000000-0000-0000-0000-000000000089"),
        state_path=tmp_path / "missing-state.json",
        create_schema=True,
    )
    monkeypatch.setattr(main_module, "store", test_store)
    with TestClient(app) as client:
        targets = client.get("/api/v1/targets")
        assert targets.status_code == 200
        target_ids = {item["targetId"] for item in targets.json()}
        assert {"cargo-001", "station-1", "inspection-001"}.issubset(target_ids)

        run_response = client.post("/api/v1/simulation-runs", json={"scenarioId": "default-site-a"})
        assert run_response.status_code == 200
        run_id = run_response.json()["runId"]

        action_response = client.post(
            "/api/v1/actions",
            json={
                "runId": run_id,
                "robotCode": "robot-001",
                "command": "goto_pose",
                "params": {"targetId": "station-2", "targetType": "station"},
            },
        )
        assert action_response.status_code == 200
        payload = action_response.json()
        assert payload["params"]["targetId"] == "station-2"
        assert payload["params"]["x"] == 760
        assert payload["params"]["y"] == 420

        invalid = client.post(
            "/api/v1/actions",
            json={
                "runId": run_id,
                "robotCode": "robot-001",
                "command": "pick",
                "params": {"targetId": "missing-cargo", "targetType": "cargo"},
            },
        )
        assert invalid.status_code == 400
        assert "targetId" in invalid.json()["detail"]


def test_action_rejects_robot_using_unassigned_path_group(tmp_path, monkeypatch):
    test_store = DatabaseStore(
        database_url=f"sqlite+pysqlite:///{(tmp_path / 'api-path-group-action.db').as_posix()}",
        workspace_id=UUID("00000000-0000-0000-0000-000000000092"),
        state_path=tmp_path / "missing-state.json",
        create_schema=True,
    )
    monkeypatch.setattr(main_module, "store", test_store)
    with TestClient(app) as client:
        run_response = client.post("/api/v1/simulation-runs", json={"scenarioId": "default-site-a"})
        assert run_response.status_code == 200
        run_id = run_response.json()["runId"]

        rejected = client.post(
            "/api/v1/actions",
            json={
                "runId": run_id,
                "robotCode": "robot-001",
                "command": "goto_pose",
                "params": {"x": 500, "y": 320, "pathGroupId": "path-group-b"},
            },
        )
        assert rejected.status_code == 400
        assert "not allowed" in rejected.json()["detail"]

        accepted = client.post(
            "/api/v1/actions",
            json={
                "runId": run_id,
                "robotCode": "robot-001",
                "command": "goto_pose",
                "params": {"x": 500, "y": 320, "pathGroupId": "path-group-a"},
            },
        )
        assert accepted.status_code == 200
        assert accepted.json()["params"]["pathGroupId"] == "path-group-a"


def test_robot_config_and_executor_management(tmp_path, monkeypatch):
    test_store = DatabaseStore(
        database_url=f"sqlite+pysqlite:///{(tmp_path / 'api-executors.db').as_posix()}",
        workspace_id=UUID("00000000-0000-0000-0000-000000000090"),
        state_path=tmp_path / "missing-state.json",
        create_schema=True,
    )
    monkeypatch.setattr(main_module, "store", test_store)
    with TestClient(app) as client:
        create_response = client.post(
            "/api/v1/robot-configs",
            json={
                "robotCode": "robot-020",
                "robotName": "Robot 020",
                "robotType": "machine-dog",
                "initialPose": {"x": 640, "y": 360, "z": 0, "yaw": 0},
                "createMode": "start_virtual_executor",
            },
        )
        assert create_response.status_code == 200
        config = create_response.json()
        assert config["robotCode"] == "robot-020"
        assert config["executorId"]
        assert config["executorStatus"] == "active"

        executors = client.get("/api/v1/executors", params={"robotCode": "robot-020"})
        assert executors.status_code == 200
        executor = executors.json()[0]
        assert executor["robotCode"] == "robot-020"
        assert executor["metadata"]["ROBOT_CODE"] == "robot-020"

        stop_response = client.post(f"/api/v1/executors/{executor['executorId']}/stop")
        assert stop_response.status_code == 200
        assert stop_response.json()["executor"]["status"] == "stopped"

        disabled = client.post("/api/v1/robot-configs/robot-020/disable")
        assert disabled.status_code == 200
        assert disabled.json()["enabled"] is False


def test_rule_scheduler_creates_agent_decision_and_action(tmp_path, monkeypatch):
    test_store = DatabaseStore(
        database_url=f"sqlite+pysqlite:///{(tmp_path / 'api-rule-scheduler.db').as_posix()}",
        workspace_id=UUID("00000000-0000-0000-0000-000000000091"),
        state_path=tmp_path / "missing-state.json",
        create_schema=True,
    )
    monkeypatch.setattr(main_module, "store", test_store)
    with TestClient(app) as client:
        run_response = client.post("/api/v1/simulation-runs", json={"scenarioId": "default-site-a"})
        assert run_response.status_code == 200
        run_id = run_response.json()["runId"]
        task_response = client.post(
            f"/api/v1/simulation-runs/{run_id}/tasks",
            json={
                "goal": "Schedule goto pose by rule agent",
                "input": {"command": "goto_pose", "target": {"targetId": "station-2", "targetType": "station"}},
            },
        )
        assert task_response.status_code == 200

        schedule_response = client.post(
            f"/api/v1/simulation-runs/{run_id}/schedule",
            json={"strategy": "idle_first", "autoIssue": True},
        )
        assert schedule_response.status_code == 200
        payload = schedule_response.json()
        assert payload["decision"]["decisionType"] == "action_created"
        assert payload["decision"]["selectedRobotCode"] == "robot-001"
        assert payload["action"]["status"] == "Issued"
        assert payload["action"]["commandId"].startswith("CMD-")

        decisions = client.get("/api/v1/messages", params={"messageType": "agentDecision", "limit": 10})
        assert decisions.status_code == 200
        assert any(message["payload"]["decisionId"] == payload["decision"]["decisionId"] for message in decisions.json())


def test_mqtt_contract_uses_dog_command_result_topics():
    with TestClient(app) as client:
        response = client.get("/api/v1/mqtt/contract")
        assert response.status_code == 200
        payload = response.json()
        assert payload["topicPattern"] == "factory/dogs/{robotCode}/{channel}"
        assert payload["command"]["topic"] == "factory/dogs/{robotCode}/command"
        assert payload["result"]["topic"] == "factory/dogs/{robotCode}/result"


def test_scene_world_state_hub_compatibility_api(tmp_path, monkeypatch):
    test_store = DatabaseStore(
        database_url=f"sqlite+pysqlite:///{(tmp_path / 'api-hub-compat.db').as_posix()}",
        workspace_id=UUID("00000000-0000-0000-0000-000000000093"),
        state_path=tmp_path / "missing-state.json",
        create_schema=True,
    )
    monkeypatch.setattr(main_module, "store", test_store)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200

        scene_response = client.post(
            "/api/v1/scenes",
            json={"scene_name": "hub-demo-scene", "description": "Hub compatibility", "map_config": {"mapId": "site-a"}},
        )
        assert scene_response.status_code == 200
        scene = scene_response.json()
        scene_id = scene["scene_id"]
        assert client.get(f"/api/v1/scenes/{scene_id}").status_code == 200

        entity_response = client.post(
            "/api/v1/entities",
            json={
                "scene_id": scene_id,
                "entity_name": "hub-robot-001",
                "entity_type": "robot",
                "properties": {"robotCode": "robot-001"},
            },
        )
        assert entity_response.status_code == 200
        entity = entity_response.json()
        entities = client.get("/api/v1/entities", params={"scene_id": scene_id, "entity_type": "robot"})
        assert entities.status_code == 200
        assert any(item["entity_id"] == entity["entity_id"] for item in entities.json())

        run_response = client.post(
            "/api/v1/runs",
            json={"run_id": "hub-run-001", "scene_id": scene_id, "status": "running", "phase": "observing"},
        )
        assert run_response.status_code == 200
        assert run_response.json()["run_id"] == "hub-run-001"
        assert client.get("/api/v1/runs/hub-run-001").status_code == 200

        task_response = client.post(
            "/api/v1/tasks",
            json={
                "run_id": "hub-run-001",
                "scene_id": scene_id,
                "task_name": "hub transport",
                "task_type": "transport",
                "definition": {"command": "goto_pose", "target": {"x": 500, "y": 320}},
            },
        )
        assert task_response.status_code == 200
        task = task_response.json()
        assert task["task_id"]
        assert task["task_name"] == "hub transport"
        active_plan_id = task["activePlan"]["planId"]

        plan_response = client.post(
            "/api/v1/plans",
            json={"task_id": task["task_id"], "version": 2, "plan_data": {"steps": [{"step_key": "move"}]}},
        )
        assert plan_response.status_code == 200
        plan = plan_response.json()
        assert client.get(f"/api/v1/plans/{plan['plan_id']}").status_code == 200

        action_response = client.post(
            "/api/v1/actions",
            json={
                "task_id": task["task_id"],
                "plan_id": active_plan_id,
                "entity_id": "robot-001",
                "action_type": "move",
                "parameters": {"target_pose": [500, 320, 0, 0], "pathGroupId": "path-group-a"},
            },
        )
        assert action_response.status_code == 200
        action = action_response.json()
        assert action["action_type"] == "move"
        assert action["command"] == "goto_pose"

        plan_status = client.post(
            f"/api/v1/plans/{active_plan_id}/status",
            json={
                "run_id": "hub-run-001",
                "scene_id": scene_id,
                "task_id": task["task_id"],
                "plan_id": active_plan_id,
                "action_id": action["action_id"],
                "plan_status": "active",
            },
        )
        assert plan_status.status_code == 200

        action_status = client.post(
            f"/api/v1/actions/{action['action_id']}/status",
            json={
                "run_id": "hub-run-001",
                "scene_id": scene_id,
                "task_id": task["task_id"],
                "plan_id": active_plan_id,
                "action_id": action["action_id"],
                "action_status": "executing",
            },
        )
        assert action_status.status_code == 200

        observation = client.post(
            "/api/v1/observations",
            json={
                "message_id": "hub-observation-001",
                "scene_id": scene_id,
                "run_id": "hub-run-001",
                "task_id": task["task_id"],
                "action_id": action["action_id"],
                "trace_id": action["traceId"],
                "observation_type": "where.result",
                "observed_at": "2026-06-29T08:00:00+00:00",
                "entity_id": "robot-001",
                "data": {"x": 500, "y": 320},
            },
        )
        assert observation.status_code == 200
        assert observation.json()["message_id"] == "hub-observation-001"

        current_state = client.get("/api/v1/current-state", params={"scene_id": scene_id, "run_id": "hub-run-001"})
        assert current_state.status_code == 200
        assert current_state.json()["run_id"] == "hub-run-001"

        executor_result = client.post(
            "/api/v1/executor-results",
            json={
                "message_id": "hub-executor-result-001",
                "scene_id": scene_id,
                "success": True,
                "result_code": "ok",
                "executed_at": "2026-06-29T08:00:01+00:00",
                "entity_id": "robot-001",
                "action_id": action["action_id"],
                "command_id": action["commandId"],
            },
        )
        assert executor_result.status_code == 200
        results = client.get("/api/v1/executor-results", params={"entity_id": "robot-001"})
        assert results.status_code == 200
        assert any(item["message_id"] == "hub-executor-result-001" for item in results.json())

        trace_response = client.post("/api/v1/traces", json={"trace_type": "shared", "scene_id": scene_id, "run_id": "hub-run-001"})
        assert trace_response.status_code == 200
        trace_id = trace_response.json()["trace_id"]
        assert client.get(f"/api/v1/traces/{trace_id}").status_code == 200
        trace_event = client.post(f"/api/v1/traces/{trace_id}/events", json={"event_type": "demo", "event_data": {"ok": True}})
        assert trace_event.status_code == 200
        assert client.get(f"/api/v1/traces/{trace_id}/events").json()[0]["event_type"] == "demo"

        snapshot = client.post("/api/v1/snapshots", json={"scene_id": scene_id, "run_id": "hub-run-001", "label": "manual"})
        assert snapshot.status_code == 200
        snapshot_id = snapshot.json()["snapshot_id"]
        assert client.get(f"/api/v1/snapshots/{snapshot_id}").status_code == 200
        assert client.get("/api/v1/snapshots", params={"scene_id": scene_id}).status_code == 200

        messages = client.post("/api/v1/messages/query", json={"source": "hub-compat", "limit": 10})
        assert messages.status_code == 200
        assert messages.json()["count"] >= 1


def test_messages_can_be_filtered_by_dog_result_fields():
    with TestClient(app) as client:
        event_id = "EVT-TEST-FILTER-001"
        trace_id = "TRACE-TEST-FILTER-001"
        response = client.post(
            "/api/v1/messages",
            json={
                "messageId": event_id,
                "messageType": "event",
                "source": "device",
                "topic": "factory/dogs/DOG-FILTER/result",
                "createdAt": "2026-06-18T00:00:00+00:00",
                "payload": {
                    "schemaVersion": "1.0",
                    "messageType": "event",
                    "event": "pose.updated",
                    "eventId": event_id,
                    "commandId": None,
                    "taskId": None,
                    "requestId": None,
                    "robotCode": "DOG-FILTER",
                    "traceId": trace_id,
                    "source": "device",
                    "timestamp": "2026-06-18T00:00:00+00:00",
                    "data": {"x": 1, "y": 2, "battery": 90},
                    "error": None,
                },
            },
        )
        assert response.status_code == 200

        filtered = client.get(
            "/api/v1/messages",
            params={"robotCode": "DOG-FILTER", "event": "pose.updated", "traceId": trace_id, "limit": 20},
        )
        assert filtered.status_code == 200
        payload = filtered.json()
        assert any(message["messageId"] == event_id for message in payload)


def test_websocket_snapshot_contains_runtime_data():
    with TestClient(app) as client:
        with client.websocket_connect("/ws/v1/sessions/session-local") as websocket:
            payload = websocket.receive_json()
            assert payload["type"] == "snapshot"
            assert "robots" in payload["data"]
            assert "messages" in payload["data"]
