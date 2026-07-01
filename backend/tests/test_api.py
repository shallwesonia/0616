import os
import urllib.parse
from copy import deepcopy
from uuid import UUID

os.environ["MQTT_ENABLED"] = "false"

from fastapi.testclient import TestClient

import backend.app.main as main_module
from backend.app.database_store import DatabaseStore
from backend.app.hub_client import HubClientError, HubIntegrationService, scene_name_for_scenario
from backend.app.main import app
from backend.app.schemas import ACTION_TARGET_TYPE_OPTIONS, ActionCreate, HubIntegrationStatus, SimulationRunCreate, SimulationTaskCreate, action_command_names


class FakeHubClient:
    enabled = True
    base_url = "http://hub.test/api/v1"
    health_url = "http://hub.test/health"

    def __init__(self):
        self.counter = 0
        self.requests = []
        self.scenes_by_name = {}
        self.entities_by_scene = {}
        self.current_states_by_scene = {}

    def status(self):
        return HubIntegrationStatus(
            enabled=True,
            baseUrl=self.base_url,
            healthUrl=self.health_url,
            status="ok",
            mqttSubscription={},
        )

    def request(self, method: str, path: str, payload=None):
        self.requests.append((method, path, payload))
        self.counter += 1
        hub_id = UUID(int=self.counter)
        if method == "GET" and path.startswith("/scenes/by-name"):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(path).query)
            scene_name = query.get("scene_name", [""])[0]
            if scene_name in self.scenes_by_name:
                return self.scenes_by_name[scene_name]
            raise HubClientError(404, "scene not found")
        if method == "GET" and path == "/scenes":
            return list(self.scenes_by_name.values())
        if method == "GET" and path.startswith("/entities"):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(path).query)
            scene_id = query.get("scene_id", [""])[0]
            return self.entities_by_scene.get(scene_id, [])
        if method == "GET" and path.startswith("/current-state"):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(path).query)
            scene_id = query.get("scene_id", [""])[0]
            run_id = query.get("run_id", [None])[0]
            states = self.current_states_by_scene.get(scene_id, [])
            if run_id:
                states = [state for state in states if state.get("run_id") == run_id]
            return states
        if method == "GET" and path.startswith("/runs/"):
            return {"id": str(hub_id), "run_id": path.rsplit("/", 1)[-1]}
        if method == "POST" and path == "/scenes":
            scene_name = payload["scene_name"]
            if scene_name in self.scenes_by_name:
                return self.scenes_by_name[scene_name]
            scene = {"id": str(hub_id), **payload}
            self.scenes_by_name[scene_name] = scene
            return scene
        if method == "POST" and path == "/entities":
            entity = {"id": str(hub_id), **payload}
            self.entities_by_scene.setdefault(payload["scene_id"], []).append(entity)
            return entity
        if method == "POST" and path == "/runs":
            return {"id": str(hub_id), **payload}
        if method == "POST" and path == "/tasks":
            return {"id": str(hub_id), **payload}
        if method == "POST" and path == "/traces":
            return {"id": str(hub_id), **payload}
        if method == "POST" and path.startswith("/traces/") and path.endswith("/events"):
            return {"id": str(hub_id), "trace_id": path.split("/")[2], **payload}
        if method == "POST" and path == "/plans":
            return {"id": str(hub_id), **payload}
        if method == "POST" and path == "/actions":
            return {"id": str(hub_id), **payload}
        raise AssertionError(f"unexpected Hub request: {method} {path}")


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


def test_publish_map_auto_syncs_hub_scene_and_entities(tmp_path, monkeypatch):
    test_store = DatabaseStore(
        database_url=f"sqlite+pysqlite:///{(tmp_path / 'api-map-publish-hub.db').as_posix()}",
        create_schema=True,
    )
    fake_hub = FakeHubClient()
    monkeypatch.setattr(main_module, "store", test_store)
    monkeypatch.setattr(main_module, "hub_service", lambda: HubIntegrationService(test_store, fake_hub))

    with TestClient(app) as client:
        current_map = client.get("/api/v1/maps/current").json()
        current_map["objects"].append(
            {
                "id": "resource-auto-hub",
                "type": "resourcePoint",
                "name": "Auto Hub Resource",
                "x": 1000,
                "y": 360,
                "width": 34,
                "height": 34,
                "color": "#fef3c7",
            }
        )
        draft = client.post(f"/api/v1/maps/{current_map['id']}/drafts", json={"map": current_map})
        assert draft.status_code == 200

        response = client.post(f"/api/v1/maps/{current_map['id']}/drafts/{draft.json()['draftId']}/publish")

    assert response.status_code == 200
    payload = response.json()
    assert payload["map"]["objects"][-1]["id"] == "resource-auto-hub"
    assert payload["targetSync"]["created"] >= 1
    assert payload["hubSync"]["enabled"] is True
    assert payload["hubSync"]["status"] == "synced"
    expected_scene_name = f"0616-default-site-a-{payload['map']['configVersion'].removeprefix('v')}"
    assert payload["hubSync"]["sceneName"] == expected_scene_name
    assert payload["hubSync"]["hubSceneId"]
    assert payload["hubSync"]["sceneSyncMode"] == "created"
    assert payload["hubSync"]["scene"]["ok"] is True
    assert payload["hubSync"]["entities"]["ok"] is True

    scene_gets = [item for item in fake_hub.requests if item[0] == "GET" and item[1].startswith("/scenes/by-name")]
    scene_posts = [item for item in fake_hub.requests if item[0] == "POST" and item[1] == "/scenes"]
    entity_posts = [item for item in fake_hub.requests if item[0] == "POST" and item[1] == "/entities"]
    assert scene_gets
    assert scene_posts
    assert len(scene_posts) == 1
    assert entity_posts
    latest_scene_payload = scene_posts[0][2]
    assert latest_scene_payload["metadata"]["siteMapVersion"] == payload["map"]["configVersion"]
    assert latest_scene_payload["scene_name"] == expected_scene_name
    assert latest_scene_payload["metadata"]["scene_name"] == expected_scene_name
    entity_names = {item[2]["entity_name"] for item in entity_posts}
    entity_types = {item[2]["entity_type"] for item in entity_posts}
    assert {"robot-001", "robot-002", "robot-003", "cargo-001", "container-001", "inspection-001"}.issubset(entity_names)
    assert "resource-auto-hub" not in entity_names
    assert not {"station", "zone", "pathNode", "pathEdge", "pathGroup", "mapObject"}.intersection(entity_types)

    scene_messages = client.get("/api/v1/messages", params={"event": "scene.updated", "limit": 10})
    assert scene_messages.status_code == 200
    assert any(message["payload"]["scene_name"] == expected_scene_name for message in scene_messages.json())


def test_hub_scene_sync_reuses_existing_scene_by_name(tmp_path):
    test_store = DatabaseStore(
        database_url=f"sqlite+pysqlite:///{(tmp_path / 'api-hub-scene-reuse.db').as_posix()}",
        workspace_id=UUID("00000000-0000-0000-0000-000000000094"),
        state_path=tmp_path / "missing-state.json",
        create_schema=True,
    )
    fake_hub = FakeHubClient()
    scenario = test_store.get_scenario("default-site-a")
    assert scenario is not None
    scene_name = scene_name_for_scenario(scenario.scenarioId, scenario.siteMapVersion)
    fake_hub.scenes_by_name[scene_name] = {
        "id": "11111111-1111-1111-1111-111111111111",
        "scene_name": scene_name,
        "description": "existing scene",
        "map_config": {"existing": True},
        "metadata": {"externalId": scenario.scenarioId},
    }

    response = HubIntegrationService(test_store, fake_hub).sync_scene("default-site-a", force=True)

    assert response.ok is True
    assert response.items[0].status == "reused"
    assert "v0.1 does not update" in response.items[0].detail
    assert not any(item[0] == "POST" and item[1] == "/scenes" for item in fake_hub.requests)
    mapping = test_store.get_hub_mapping("scenario", "default-site-a", "scene")
    assert mapping is not None
    assert mapping.hubId == "11111111-1111-1111-1111-111111111111"
    assert mapping.metadata["sceneSyncMode"] == "reused_existing_no_update"


def test_hub_scene_sync_recreates_stale_mapping(tmp_path):
    test_store = DatabaseStore(
        database_url=f"sqlite+pysqlite:///{(tmp_path / 'api-hub-scene-stale.db').as_posix()}",
        workspace_id=UUID("00000000-0000-0000-0000-000000000095"),
        state_path=tmp_path / "missing-state.json",
        create_schema=True,
    )
    fake_hub = FakeHubClient()
    scenario = test_store.get_scenario("default-site-a")
    assert scenario is not None
    scene_name = scene_name_for_scenario(scenario.scenarioId, scenario.siteMapVersion)
    test_store.upsert_hub_mapping(
        local_type="scenario",
        local_id="default-site-a",
        hub_type="scene",
        hub_id="22222222-2222-2222-2222-222222222222",
        metadata={"scene_name": scene_name, "sceneName": scene_name},
    )

    response = HubIntegrationService(test_store, fake_hub).sync_scene("default-site-a", force=True)

    assert response.ok is True
    assert response.items[0].status == "synced"
    assert response.items[0].detail == "Hub Scene recreated after stale local mapping"
    assert any(item[0] == "POST" and item[1] == "/scenes" for item in fake_hub.requests)
    mapping = test_store.get_hub_mapping("scenario", "default-site-a", "scene")
    assert mapping is not None
    assert mapping.hubId != "22222222-2222-2222-2222-222222222222"
    assert mapping.metadata["sceneSyncMode"] == "recreated_after_stale_mapping"


def test_hub_current_state_endpoint_reads_hub_robot_state(tmp_path, monkeypatch):
    test_store = DatabaseStore(
        database_url=f"sqlite+pysqlite:///{(tmp_path / 'api-hub-current-state.db').as_posix()}",
        workspace_id=UUID("00000000-0000-0000-0000-000000000096"),
        state_path=tmp_path / "missing-state.json",
        create_schema=True,
    )
    fake_hub = FakeHubClient()
    scenario = test_store.get_scenario("default-site-a")
    assert scenario is not None
    scene_name = scene_name_for_scenario(scenario.scenarioId, scenario.siteMapVersion)
    scene_id = "33333333-3333-3333-3333-333333333333"
    robot_entity_id = "44444444-4444-4444-4444-444444444444"
    fake_hub.scenes_by_name[scene_name] = {
        "id": scene_id,
        "scene_name": scene_name,
        "description": "existing scene",
        "map_config": {},
        "metadata": {"externalId": scenario.scenarioId},
    }
    fake_hub.entities_by_scene[scene_id] = [
        {
            "id": robot_entity_id,
            "scene_id": scene_id,
            "entity_name": "robot-001",
            "entity_type": "robot",
            "properties": {"robotId": "robot-001", "robotType": "machine-dog", "state": "Idle", "x": 220, "y": 360},
            "metadata": {"externalId": "robot-001"},
        }
    ]
    fake_hub.current_states_by_scene[scene_id] = [
        {
            "id": "55555555-5555-5555-5555-555555555555",
            "scene_id": scene_id,
            "run_id": "default",
            "state_version": 7,
            "active_task_id": None,
            "active_plan_id": None,
            "active_action_id": None,
            "last_observation_id": "66666666-6666-6666-6666-666666666666",
            "last_observation_at": "2026-07-01T03:00:00Z",
            "updated_at": "2026-07-01T03:00:01Z",
            "entities": [
                {
                    "entity_id": robot_entity_id,
                    "state_type": "position",
                    "state_data": {"x": 760, "y": 420, "yaw": 1.57, "battery": 77},
                    "last_observed_at": "2026-07-01T03:00:00Z",
                    "updated_at": "2026-07-01T03:00:01Z",
                }
            ],
        }
    ]
    monkeypatch.setattr(main_module, "store", test_store)
    monkeypatch.setattr(main_module, "hub_service", lambda: HubIntegrationService(test_store, fake_hub))

    with TestClient(app) as client:
        response = client.get("/api/v1/integrations/hub/current-state")

    assert response.status_code == 200
    payload = response.json()
    assert payload["environmentState"]["source"] == "hub"
    assert payload["environmentState"]["sceneName"] == scene_name
    assert payload["stateVersion"] == 7
    assert payload["robotStates"][0]["robotId"] == "robot-001"
    assert payload["robotStates"][0]["x"] == 760
    assert payload["robotStates"][0]["battery"] == 77


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
        assert payload["payload"]["scene_name"].startswith("0616-default-site-a-")
        assert not payload["payload"]["scene_name"].startswith("0616-default-site-a-v")
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


def test_hub_integration_status_exposes_mqtt_subscription(monkeypatch):
    monkeypatch.setenv("HUB_SYNC_ENABLED", "false")
    with TestClient(app) as client:
        response = client.get("/api/v1/integrations/hub/status")
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "disabled"
        assert payload["mqttSubscription"]["topics"][0]["topic"] == "factory/dogs/+/result"
        assert "scene_name" in payload["mqttSubscription"]["requiredPayloadFields"]


def test_hub_sync_run_graph_creates_uuid_mappings(tmp_path):
    test_store = DatabaseStore(
        database_url=f"sqlite+pysqlite:///{(tmp_path / 'api-hub-sync.db').as_posix()}",
        workspace_id=UUID("00000000-0000-0000-0000-000000000093"),
        state_path=tmp_path / "missing-state.json",
        create_schema=True,
    )
    run = test_store.create_simulation_run(SimulationRunCreate(scenarioId="default-site-a", name="Hub sync"))
    task = test_store.create_simulation_task(
        run.runId,
        SimulationTaskCreate(
            goal="Sync task",
            input={"command": "goto_pose", "target": {"x": 760, "y": 420, "z": 0, "yaw": 0}},
            constraints={},
            priority=5,
            expectedOutcome="synced",
            createdBy="test",
        ),
    )
    assert task is not None
    action = test_store.create_action(
        ActionCreate(
            runId=run.runId,
            taskId=task.taskId,
            planId=task.activePlan.planId,
            planStepId=task.activePlan.steps[0].planStepId,
            robotCode="robot-001",
            command="goto_pose",
            params={"x": 760, "y": 420, "z": 0, "yaw": 0},
            timeoutMs=60000,
            operatorId="test",
        ),
    )
    assert action is not None

    fake_hub = FakeHubClient()
    response = HubIntegrationService(test_store, fake_hub).sync_run_graph(run.runId)
    assert response.ok is True
    mapping_types = {(item.localType, item.hubType) for item in response.mappings}
    assert ("scenario", "scene") in mapping_types
    assert ("robot", "entity") in mapping_types
    assert ("target", "entity") in mapping_types
    assert ("run", "run") in mapping_types
    assert ("task", "task") in mapping_types
    assert ("plan", "plan") in mapping_types
    assert ("action", "action") in mapping_types
    assert ("trace", "trace") in mapping_types

    trace_mapping = test_store.get_hub_mapping("trace", task.traceId, "trace")
    assert trace_mapping is not None
    assert UUID(trace_mapping.hubId)
    assert trace_mapping.externalTraceId == task.traceId
    assert trace_mapping.hubTraceId == trace_mapping.hubId
    action_payloads = [payload for method, path, payload in fake_hub.requests if method == "POST" and path == "/actions"]
    assert action_payloads[0]["metadata"]["externalId"] == action.actionId
    assert UUID(action_payloads[0]["task_id"])
    assert UUID(action_payloads[0]["plan_id"])
    assert UUID(action_payloads[0]["entity_id"])


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

        target_pose_response = client.post(
            "/api/v1/actions",
            json={
                "runId": run_id,
                "robotCode": "robot-001",
                "command": "goto_pose",
                "params": {"targetId": "inspection-001", "targetType": "inspectionPoint", "x": 760, "y": 420},
            },
        )
        assert target_pose_response.status_code == 200
        target_pose_payload = target_pose_response.json()
        assert target_pose_payload["params"]["targetId"] == "inspection-001"
        assert target_pose_payload["params"]["x"] == 520
        assert target_pose_payload["params"]["y"] == 360

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


def test_task_chain_and_manual_plan_creation(tmp_path, monkeypatch):
    test_store = DatabaseStore(
        database_url=f"sqlite+pysqlite:///{(tmp_path / 'api-task-chain.db').as_posix()}",
        workspace_id=UUID("00000000-0000-0000-0000-000000000094"),
        state_path=tmp_path / "missing-state.json",
        create_schema=True,
    )
    monkeypatch.setattr(main_module, "store", test_store)
    with TestClient(app) as client:
        run_response = client.post("/api/v1/simulation-runs", json={"scenarioId": "default-site-a"})
        assert run_response.status_code == 200
        run_id = run_response.json()["runId"]

        chain_response = client.post(
            f"/api/v1/simulation-runs/{run_id}/task-chains",
            json={
                "name": "serial transport chain",
                "mode": "serial",
                "triggerPolicy": "auto",
                "robotStrategy": "specified",
                "failurePolicy": "stop_chain",
                "priority": 4,
                "tasks": [
                    {
                        "goal": "go to source",
                        "input": {"command": "goto_pose", "target": {"x": 300, "y": 220, "z": 0, "yaw": 0}},
                        "triggerCondition": "chain_started",
                    },
                    {
                        "goal": "go to target",
                        "input": {"command": "goto_pose", "target": {"x": 520, "y": 340, "z": 0, "yaw": 0}},
                        "dependsOn": ["1"],
                        "triggerCondition": "previous_succeeded",
                    },
                ],
            },
        )
        assert chain_response.status_code == 200
        chain = chain_response.json()
        assert chain["chainId"].startswith("CHAIN-")
        assert len(chain["items"]) == 2
        assert chain["items"][1]["status"] == "Waiting"
        first_task_id = chain["items"][0]["taskId"]

        listed = client.get(f"/api/v1/simulation-runs/{run_id}/task-chains")
        assert listed.status_code == 200
        assert listed.json()[0]["chainId"] == chain["chainId"]

        task_response = client.get(f"/api/v1/tasks/{first_task_id}")
        assert task_response.status_code == 200
        task = task_response.json()
        assert task["constraints"]["chainId"] == chain["chainId"]
        original_plan_id = task["activePlan"]["planId"]

        plan_response = client.post(
            f"/api/v1/tasks/{first_task_id}/plans",
            json={
                "strategy": "manual",
                "generatedBy": "simulation-console",
                "activate": True,
                "steps": [
                    {
                        "actionType": "goto_pose",
                        "target": {"x": 310, "y": 230},
                        "params": {"x": 310, "y": 230, "z": 0, "yaw": 0},
                        "successCondition": "arrived source",
                    },
                    {
                        "actionType": "where",
                        "params": {"queryMode": "pose"},
                        "dependsOn": ["1"],
                        "successCondition": "pose confirmed",
                    },
                ],
                "assumptions": {"reason": "operator adjusted route"},
            },
        )
        assert plan_response.status_code == 200
        plan = plan_response.json()
        assert plan["planVersion"] == 2
        assert plan["status"] == "Active"
        assert len(plan["steps"]) == 2

        plans = client.get(f"/api/v1/tasks/{first_task_id}/plans")
        assert plans.status_code == 200
        statuses = {item["planId"]: item["status"] for item in plans.json()}
        assert statuses[original_plan_id] == "Superseded"
        assert statuses[plan["planId"]] == "Active"

        refreshed = client.get(f"/api/v1/tasks/{first_task_id}").json()
        assert refreshed["activePlan"]["planId"] == plan["planId"]


def test_manual_orchestration_task_plan_steps(tmp_path, monkeypatch):
    test_store = DatabaseStore(
        database_url=f"sqlite+pysqlite:///{(tmp_path / 'api-manual-orchestration.db').as_posix()}",
        workspace_id=UUID("00000000-0000-0000-0000-000000000095"),
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
                "goal": "manual pick and place orchestration",
                "input": {"mode": "manual_orchestration", "stepCount": 5, "robotCode": "robot-001"},
                "constraints": {"robotCode": "robot-001", "orchestrationMode": "serial"},
                "priority": 5,
            },
        )
        assert task_response.status_code == 200
        task = task_response.json()

        plan_response = client.post(
            f"/api/v1/tasks/{task['taskId']}/plans",
            json={
                "strategy": "manual_orchestration",
                "generatedBy": "simulation-console",
                "activate": True,
                "steps": [
                    {
                        "actionType": "goto_pose",
                        "target": {"targetId": "station-1", "targetType": "station"},
                        "params": {"targetId": "station-1", "targetType": "station", "speed": 1, "tolerance": 50},
                    },
                    {
                        "actionType": "pick",
                        "target": {"targetId": "cargo-001", "targetType": "cargo"},
                        "params": {"targetId": "cargo-001", "targetType": "cargo", "durationMinMs": 3000, "durationMaxMs": 5000},
                        "dependsOn": ["1"],
                    },
                    {
                        "actionType": "goto_pose",
                        "target": {"targetId": "station-2", "targetType": "station"},
                        "params": {"targetId": "station-2", "targetType": "station", "speed": 1, "tolerance": 50},
                        "dependsOn": ["2"],
                    },
                    {
                        "actionType": "place",
                        "target": {"targetId": "station-2", "targetType": "station"},
                        "params": {"targetId": "station-2", "targetType": "station", "durationMinMs": 3000, "durationMaxMs": 5000},
                        "dependsOn": ["3"],
                    },
                    {
                        "actionType": "goto_pose",
                        "target": {"x": 100, "y": 100, "z": 0, "yaw": 0},
                        "params": {"x": 100, "y": 100, "z": 0, "yaw": 0, "speed": 1, "tolerance": 50},
                        "dependsOn": ["4"],
                    },
                ],
                "assumptions": {"source": "simulation-dashboard", "orchestrationMode": "serial", "expandedStepCount": 5},
            },
        )
        assert plan_response.status_code == 200
        plan = plan_response.json()
        assert plan["strategy"] == "manual_orchestration"
        assert plan["planVersion"] == 2
        assert plan["status"] == "Active"
        assert [step["actionType"] for step in plan["steps"]] == ["goto_pose", "pick", "goto_pose", "place", "goto_pose"]
        assert plan["steps"][1]["dependsOn"] == ["1"]
        assert plan["steps"][4]["params"]["x"] == 100

        first_schedule = client.post(
            f"/api/v1/simulation-runs/{run_id}/schedule",
            json={"taskId": task["taskId"], "strategy": "specified_robot", "robotCode": "robot-001", "autoIssue": True},
        )
        assert first_schedule.status_code == 200
        first_payload = first_schedule.json()
        assert first_payload["decision"]["decisionType"] == "action_created"
        assert first_payload["action"]["planStepId"] == plan["steps"][0]["planStepId"]
        assert first_payload["action"]["command"] == "goto_pose"

        second_schedule = client.post(
            f"/api/v1/simulation-runs/{run_id}/schedule",
            json={"taskId": task["taskId"], "strategy": "specified_robot", "robotCode": "robot-001", "autoIssue": True},
        )
        assert second_schedule.status_code == 200
        second_payload = second_schedule.json()
        assert second_payload["decision"]["decisionType"] == "action_created"
        assert second_payload["action"]["planStepId"] == plan["steps"][1]["planStepId"]
        assert second_payload["action"]["command"] == "pick"

        refreshed = client.get(f"/api/v1/tasks/{task['taskId']}").json()
        assert refreshed["activePlan"]["planId"] == plan["planId"]
        assert refreshed["activePlan"]["steps"][0]["status"] == "Issued"
        assert refreshed["activePlan"]["steps"][1]["status"] == "Issued"
        assert refreshed["activePlan"]["steps"][2]["status"] == "Pending"


def test_mqtt_contract_uses_dog_command_result_topics():
    with TestClient(app) as client:
        response = client.get("/api/v1/mqtt/contract")
        assert response.status_code == 200
        payload = response.json()
        assert payload["topicPattern"] == "factory/dogs/{robotCode}/{channel}"
        assert payload["command"]["topic"] == "factory/dogs/{robotCode}/command"
        assert payload["result"]["topic"] == "factory/dogs/{robotCode}/result"
        assert "scene_name" in payload["command"]["required"]
        assert "scene_name" in payload["result"]["required"]
        assert "messageId" in payload["result"]["required"]
        assert "sceneName" in payload["result"]["fields"]


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
