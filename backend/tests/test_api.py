import os
from copy import deepcopy

os.environ["MQTT_ENABLED"] = "false"

from fastapi.testclient import TestClient

from backend.app.main import app


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
        assert payload["services"]["mqtt"]["supportedCommands"] == ["goto_pose", "stop", "where"]


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
