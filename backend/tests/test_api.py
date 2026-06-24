import os
from copy import deepcopy
from uuid import UUID

os.environ["MQTT_ENABLED"] = "false"

from fastapi.testclient import TestClient

import backend.app.main as main_module
from backend.app.database_store import DatabaseStore
from backend.app.main import app
from backend.app.schemas import action_command_names


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
        assert commands["where"]["fields"]["queryMode"]["options"] == ["pose", "state", "full"]
        assert commands["stop"]["fields"]["stopScope"]["options"] == ["current_action", "task", "robot"]


def test_mqtt_contract_uses_dog_command_result_topics():
    with TestClient(app) as client:
        response = client.get("/api/v1/mqtt/contract")
        assert response.status_code == 200
        payload = response.json()
        assert payload["topicPattern"] == "factory/dogs/{robotCode}/{channel}"
        assert payload["command"]["topic"] == "factory/dogs/{robotCode}/command"
        assert payload["result"]["topic"] == "factory/dogs/{robotCode}/result"


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
