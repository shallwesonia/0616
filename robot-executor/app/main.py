from __future__ import annotations

import json
import os
import random
import signal
import threading
import time
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import paho.mqtt.client as mqtt


ACTION_PROFILES = {
    "goto_pose": (30000, 35000),
    "stop": (1000, 1500),
    "where": (200, 500),
    "pick": (3000, 5000),
    "place": (3000, 5000),
    "load": (5000, 8000),
    "unload": (5000, 8000),
    "inspect": (4000, 7000),
    "charge": (10000, 15000),
    "wait": (1000, 3000),
}

ACTION_STATES = {
    "goto_pose": "Moving",
    "pick": "Picking",
    "place": "Placing",
    "load": "Loading",
    "unload": "Unloading",
    "inspect": "Inspecting",
    "charge": "Charging",
    "wait": "Waiting",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def protocol_id(prefix: str) -> str:
    date_part = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"{prefix}-{date_part}-{uuid4().hex[:6].upper()}"


class VirtualRobotExecutor:
    def __init__(self) -> None:
        self.env = os.getenv("SIM_ENV", "dev")
        self.site_id = os.getenv("SITE_ID", "site-a")
        self.robot_code = os.getenv("ROBOT_CODE", os.getenv("ROBOT_ID", "robot-001"))
        self.robot_type = os.getenv("ROBOT_TYPE", "machine-dog")
        self.scene_name: str | None = os.getenv("SCENE_NAME")
        self.host = os.getenv("MQTT_HOST", "localhost")
        self.port = int(os.getenv("MQTT_PORT", "1883"))
        self.client_id = os.getenv("MQTT_CLIENT_ID", f"virtual-dog-{self.robot_code}")
        self.time_scale = float(os.getenv("TIME_SCALE", "1.0"))
        self.random_event_rate = float(os.getenv("RANDOM_EVENT_RATE", "0.05"))
        self.position = {
            "x": float(os.getenv("START_X", "220")),
            "y": float(os.getenv("START_Y", "360")),
            "z": 0.0,
            "yaw": 0.0,
        }
        self.battery = 88
        self.state = "Idle"
        self.current_command_id: str | None = None
        self.current_task_id: str | None = None
        self.current_trace_id: str | None = None
        self.current_run_id: str | None = None
        self.command_context = threading.local()
        self.busy_lock = threading.Lock()
        self.cancel_current_task = threading.Event()
        self.offline_mode = threading.Event()
        self.fail_next_action = threading.Event()
        self.path_blocked = threading.Event()
        self.stop_event = threading.Event()
        self.client = self._create_client()

    def _create_client(self) -> mqtt.Client:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self.client_id)
        username = os.getenv("MQTT_USERNAME")
        password = os.getenv("MQTT_PASSWORD")
        if username:
            client.username_pw_set(username, password)
        client.will_set(
            self.result_topic(),
            payload=json.dumps(
                self.result_event(
                    "device.offline",
                    command_id=None,
                    task_id=None,
                    request_id=None,
                    trace_id=f"TRACE-{self.robot_code}-POSE",
                    data={"lastSeenAt": utc_now()},
                    error=self.error("DEVICE_OFFLINE", "device disconnected unexpectedly", retryable=True),
                    source="mqtt",
                )
            ),
            qos=1,
            retain=False,
        )
        client.on_connect = self.on_connect
        client.on_message = self.on_message
        return client

    def run(self) -> None:
        self.client.connect(self.host, self.port, keepalive=30)
        self.client.loop_start()
        signal.signal(signal.SIGTERM, lambda *_: self.stop_event.set())
        signal.signal(signal.SIGINT, lambda *_: self.stop_event.set())
        while not self.stop_event.is_set():
            self.publish_pose()
            time.sleep(5)
        self.publish_pose()
        self.client.loop_stop()
        self.client.disconnect()

    def on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: mqtt.ConnectFlags,
        reason_code: mqtt.ReasonCode,
        properties: mqtt.Properties | None,
    ) -> None:
        client.subscribe(self.command_topic(), qos=1)
        client.subscribe(f"sim/{self.env}/{self.site_id}/broadcast/event", qos=1)
        self.publish_pose()

    def on_message(self, client: mqtt.Client, userdata: Any, message: mqtt.MQTTMessage) -> None:
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self.publish_result(
                self.result_event(
                    "command.rejected",
                    command_id=None,
                    task_id=None,
                    request_id=None,
                    trace_id=f"TRACE-{self.robot_code}-ERROR",
                    data={},
                    error=self.error("INVALID_TARGET_POSE", f"invalid json payload: {exc}", retryable=False),
                )
            )
            return

        if message.topic == self.command_topic():
            threading.Thread(target=self.execute_command, args=(payload,), daemon=True).start()
        elif message.topic.endswith("/broadcast/event"):
            self.handle_broadcast_event(payload)

    def execute_command(self, command_payload: dict[str, Any]) -> None:
        command_name = command_payload.get("command")
        command_id = command_payload.get("commandId") or protocol_id("CMD")
        task_id = command_payload.get("taskId")
        request_id = command_payload.get("requestId")
        trace_id = command_payload.get("traceId") or protocol_id("TRACE")
        params = command_payload.get("params") or {}
        self.command_context.run_id = self.extract_run_id(command_payload)
        self.remember_scene_name(command_payload)

        if command_payload.get("messageType") != "command" or command_payload.get("robotCode") != self.robot_code:
            self.publish_command_rejected(command_id, task_id, request_id, trace_id, "INVALID_TARGET_POSE", "invalid command payload")
            return
        if self.offline_mode.is_set() and command_name != "stop":
            self.state = "Offline"
            self.publish_command_rejected(command_id, task_id, request_id, trace_id, "DEVICE_OFFLINE", "device is offline", retryable=True)
            self.publish_result(
                self.result_event(
                    "device.offline",
                    command_id=command_id,
                    task_id=task_id,
                    request_id=request_id,
                    trace_id=trace_id,
                    data={"state": self.state, "reason": "offline_mode"},
                    error=self.error("DEVICE_OFFLINE", "device is offline", retryable=True),
                )
            )
            return
        if command_name == "where":
            self.handle_where(command_id, request_id, trace_id, params)
            return
        if command_name == "stop":
            self.handle_stop(command_id, task_id, trace_id, params)
            return
        if command_name == "goto_pose":
            self.execute_goto_pose(command_id, task_id, trace_id, params)
            return
        if command_name in ACTION_PROFILES:
            self.execute_generic_action(str(command_name), command_id, task_id, trace_id, params)
            return
        self.publish_command_rejected(command_id, task_id, request_id, trace_id, "INVALID_TARGET_POSE", "unsupported command")

    def planned_duration_ms(self, action_name: str, params: dict[str, Any]) -> int:
        min_ms, max_ms = ACTION_PROFILES.get(action_name, (1000, 3000))
        if "durationMinMs" in params:
            min_ms = int(float(params["durationMinMs"]))
        if "durationMaxMs" in params:
            max_ms = int(float(params["durationMaxMs"]))
        if min_ms < 0 or max_ms < min_ms:
            raise ValueError("invalid duration range")
        return random.randint(min_ms, max_ms)

    def should_fail_current_action(self) -> tuple[str, str] | None:
        if self.offline_mode.is_set():
            return ("DEVICE_OFFLINE", "device went offline")
        if self.path_blocked.is_set():
            return ("PATH_BLOCKED", "path is blocked")
        if self.fail_next_action.is_set():
            self.fail_next_action.clear()
            return ("ACTION_FAILED", "simulated action failure")
        return None

    def execute_goto_pose(self, command_id: str, task_id: str | None, trace_id: str, params: dict[str, Any]) -> None:
        if task_id is None:
            self.publish_command_rejected(command_id, None, None, trace_id, "INVALID_TARGET_POSE", "taskId is required")
            return
        if "x" not in params or "y" not in params:
            self.publish_command_rejected(command_id, task_id, None, trace_id, "INVALID_TARGET_POSE", "params.x and params.y are required")
            return
        if not self.busy_lock.acquire(blocking=False):
            self.publish_command_rejected(command_id, task_id, None, trace_id, "DEVICE_BUSY", "device is executing another task", retryable=True)
            return

        self.cancel_current_task.clear()
        self.current_command_id = command_id
        self.current_task_id = task_id
        self.current_trace_id = trace_id
        self.current_run_id = self.extract_run_id_from_context()
        self.state = "Moving"
        try:
            planned_ms = self.planned_duration_ms("goto_pose", params)
            self.publish_command_accepted(command_id, task_id, None, trace_id)
            self.publish_result(
                self.result_event(
                    "action.started",
                    command_id=command_id,
                    task_id=task_id,
                    request_id=None,
                    trace_id=trace_id,
                    data={
                        "action": "goto_pose",
                        "plannedDurationMs": planned_ms,
                        "target": {"x": float(params["x"]), "y": float(params["y"])},
                    },
                )
            )
            self.publish_result(
                self.result_event(
                    "task.started",
                    command_id=command_id,
                    task_id=task_id,
                    request_id=None,
                    trace_id=trace_id,
                    data={**self.pose_data(), "battery": self.battery},
                )
            )

            duration_s = max(0.2, (planned_ms / 1000) * self.time_scale)
            steps = max(5, int(duration_s / 1))
            start_x = self.position["x"]
            start_y = self.position["y"]
            target_x = float(params["x"])
            target_y = float(params["y"])

            for step in range(1, steps + 1):
                failure = self.should_fail_current_action()
                if failure:
                    error_code, error_message = failure
                    self.state = "Error" if error_code != "DEVICE_OFFLINE" else "Offline"
                    self.publish_result(
                        self.result_event(
                            "action.failed",
                            command_id=command_id,
                            task_id=task_id,
                            request_id=None,
                            trace_id=trace_id,
                            data={"action": "goto_pose", "progress": int(((step - 1) / steps) * 100), **self.pose_data()},
                            error=self.error(error_code, error_message, retryable=True),
                        )
                    )
                    self.publish_result(
                        self.result_event(
                            "task.failed",
                            command_id=command_id,
                            task_id=task_id,
                            request_id=None,
                            trace_id=trace_id,
                            data={"result": 0, "action": "goto_pose"},
                            error=self.error(error_code, error_message, retryable=True),
                        )
                    )
                    return
                if self.stop_event.is_set() or self.cancel_current_task.is_set():
                    self.state = "Idle"
                    self.publish_result(
                        self.result_event(
                            "task.stopped",
                            command_id=command_id,
                            task_id=task_id,
                            request_id=None,
                            trace_id=trace_id,
                            data={"reason": "stop_requested", **self.pose_data()},
                        )
                    )
                    return
                ratio = step / steps
                self.position["x"] = start_x + (target_x - start_x) * ratio
                self.position["y"] = start_y + (target_y - start_y) * ratio
                if "yaw" in params:
                    self.position["yaw"] = float(params["yaw"])
                if random.random() < self.random_event_rate:
                    self.publish_pose()
                self.publish_pose()
                self.publish_result(
                    self.result_event(
                        "action.progress",
                        command_id=command_id,
                        task_id=task_id,
                        request_id=None,
                        trace_id=trace_id,
                        data={
                            "action": "goto_pose",
                            "progress": int(ratio * 100),
                            "elapsedMs": int(planned_ms * ratio),
                            "remainingMs": max(0, int(planned_ms * (1 - ratio))),
                            **self.pose_data(),
                        },
                    )
                )
                time.sleep(duration_s / steps)

            self.position["x"] = target_x
            self.position["y"] = target_y
            self.state = "Idle"
            self.publish_pose()
            self.publish_result(
                self.result_event(
                    "action.succeeded",
                    command_id=command_id,
                    task_id=task_id,
                    request_id=None,
                    trace_id=trace_id,
                    data={
                        "result": 1,
                        "action": "goto_pose",
                        "plannedDurationMs": planned_ms,
                        "actualDurationMs": int(duration_s * 1000),
                        **self.pose_data(),
                    },
                )
            )
            self.publish_result(
                self.result_event(
                    "task.succeeded",
                    command_id=command_id,
                    task_id=task_id,
                    request_id=None,
                    trace_id=trace_id,
                    data={
                        "result": 1,
                        **self.pose_data(),
                        "battery": self.battery,
                        "plannedDurationMs": planned_ms,
                        "actualDurationMs": int(duration_s * 1000),
                    },
                )
            )
        except Exception as exc:
            self.state = "Error"
            self.publish_result(
                self.result_event(
                    "action.failed",
                    command_id=command_id,
                    task_id=task_id,
                    request_id=None,
                    trace_id=trace_id,
                    data={"result": 0, "action": "goto_pose"},
                    error=self.error("NAVIGATION_BLOCKED", str(exc), retryable=True),
                )
            )
            self.publish_result(
                self.result_event(
                    "task.failed",
                    command_id=command_id,
                    task_id=task_id,
                    request_id=None,
                    trace_id=trace_id,
                    data={"result": 0},
                    error=self.error("NAVIGATION_BLOCKED", str(exc), retryable=True),
                )
            )
        finally:
            self.current_command_id = None
            self.current_task_id = None
            self.current_trace_id = None
            self.current_run_id = None
            self.cancel_current_task.clear()
            self.busy_lock.release()

    def execute_generic_action(
        self,
        action_name: str,
        command_id: str,
        task_id: str | None,
        trace_id: str,
        params: dict[str, Any],
    ) -> None:
        if task_id is None:
            self.publish_command_rejected(command_id, None, None, trace_id, "INVALID_TARGET_POSE", "taskId is required")
            return
        if not self.busy_lock.acquire(blocking=False):
            self.publish_command_rejected(command_id, task_id, None, trace_id, "DEVICE_BUSY", "device is executing another task", retryable=True)
            return

        self.cancel_current_task.clear()
        self.current_command_id = command_id
        self.current_task_id = task_id
        self.current_trace_id = trace_id
        self.current_run_id = self.extract_run_id_from_context()
        self.state = ACTION_STATES.get(action_name, "Executing")
        try:
            planned_ms = self.planned_duration_ms(action_name, params)
            duration_s = max(0.2, (planned_ms / 1000) * self.time_scale)
            steps = max(3, int(duration_s / 1))
            self.publish_command_accepted(command_id, task_id, None, trace_id)
            self.publish_result(
                self.result_event(
                    "action.started",
                    command_id=command_id,
                    task_id=task_id,
                    request_id=None,
                    trace_id=trace_id,
                    data={"action": action_name, "plannedDurationMs": planned_ms, "params": params},
                )
            )
            self.publish_result(
                self.result_event(
                    "task.started",
                    command_id=command_id,
                    task_id=task_id,
                    request_id=None,
                    trace_id=trace_id,
                    data={**self.pose_data(), "battery": self.battery, "action": action_name},
                )
            )

            for step in range(1, steps + 1):
                failure = self.should_fail_current_action()
                if failure:
                    error_code, error_message = failure
                    self.state = "Error" if error_code != "DEVICE_OFFLINE" else "Offline"
                    self.publish_result(
                        self.result_event(
                            "action.failed",
                            command_id=command_id,
                            task_id=task_id,
                            request_id=None,
                            trace_id=trace_id,
                            data={"action": action_name, "progress": int(((step - 1) / steps) * 100), **self.pose_data()},
                            error=self.error(error_code, error_message, retryable=True),
                        )
                    )
                    self.publish_result(
                        self.result_event(
                            "task.failed",
                            command_id=command_id,
                            task_id=task_id,
                            request_id=None,
                            trace_id=trace_id,
                            data={"result": 0, "action": action_name},
                            error=self.error(error_code, error_message, retryable=True),
                        )
                    )
                    return
                if self.stop_event.is_set() or self.cancel_current_task.is_set():
                    self.state = "Idle"
                    self.publish_result(
                        self.result_event(
                            "task.stopped",
                            command_id=command_id,
                            task_id=task_id,
                            request_id=None,
                            trace_id=trace_id,
                            data={"reason": "stop_requested", "action": action_name, **self.pose_data()},
                        )
                    )
                    return
                ratio = step / steps
                if action_name == "charge":
                    self.battery = min(100, self.battery + max(1, int(12 / steps)))
                self.publish_pose()
                self.publish_result(
                    self.result_event(
                        "action.progress",
                        command_id=command_id,
                        task_id=task_id,
                        request_id=None,
                        trace_id=trace_id,
                        data={
                            "action": action_name,
                            "progress": int(ratio * 100),
                            "elapsedMs": int(planned_ms * ratio),
                            "remainingMs": max(0, int(planned_ms * (1 - ratio))),
                            **self.pose_data(),
                            "battery": self.battery,
                        },
                    )
                )
                time.sleep(duration_s / steps)

            self.state = "Idle"
            self.publish_pose()
            self.publish_result(
                self.result_event(
                    "action.succeeded",
                    command_id=command_id,
                    task_id=task_id,
                    request_id=None,
                    trace_id=trace_id,
                    data={
                        "result": 1,
                        "action": action_name,
                        "plannedDurationMs": planned_ms,
                        "actualDurationMs": int(duration_s * 1000),
                        **self.pose_data(),
                        "battery": self.battery,
                    },
                )
            )
            self.publish_result(
                self.result_event(
                    "task.succeeded",
                    command_id=command_id,
                    task_id=task_id,
                    request_id=None,
                    trace_id=trace_id,
                    data={"result": 1, "action": action_name, "battery": self.battery, **self.pose_data()},
                )
            )
        except Exception as exc:
            self.state = "Error"
            self.publish_result(
                self.result_event(
                    "action.failed",
                    command_id=command_id,
                    task_id=task_id,
                    request_id=None,
                    trace_id=trace_id,
                    data={"result": 0, "action": action_name},
                    error=self.error("ACTION_FAILED", str(exc), retryable=True),
                )
            )
            self.publish_result(
                self.result_event(
                    "task.failed",
                    command_id=command_id,
                    task_id=task_id,
                    request_id=None,
                    trace_id=trace_id,
                    data={"result": 0, "action": action_name},
                    error=self.error("ACTION_FAILED", str(exc), retryable=True),
                )
            )
        finally:
            self.current_command_id = None
            self.current_task_id = None
            self.current_trace_id = None
            self.current_run_id = None
            self.cancel_current_task.clear()
            self.busy_lock.release()

    def handle_where(self, command_id: str, request_id: str | None, trace_id: str, params: dict[str, Any]) -> None:
        if request_id is None:
            self.publish_command_rejected(command_id, None, None, trace_id, "WHERE_FAILED", "requestId is required")
            return
        self.publish_command_accepted(command_id, None, request_id, trace_id)
        self.publish_result(
            self.result_event(
                "where.result",
                command_id=command_id,
                task_id=None,
                request_id=request_id,
                trace_id=trace_id,
                data={**self.pose_data(), "battery": self.battery, "queryMode": params.get("queryMode", "pose")},
            )
        )

    def handle_stop(self, command_id: str, task_id: str | None, trace_id: str, params: dict[str, Any]) -> None:
        if task_id is None:
            self.publish_command_rejected(command_id, None, None, trace_id, "STOP_FAILED", "taskId is required")
            return
        self.publish_command_accepted(command_id, task_id, None, trace_id)
        self.cancel_current_task.set()
        if not self.busy_lock.locked():
            self.publish_result(
                self.result_event(
                    "task.stopped",
                    command_id=command_id,
                    task_id=task_id,
                    request_id=None,
                    trace_id=trace_id,
                    data={"reason": params.get("reason", "manual_stop"), **self.pose_data()},
                )
            )

    def handle_broadcast_event(self, envelope: dict[str, Any]) -> None:
        self.remember_scene_name(envelope)
        payload = envelope.get("payload", {})
        event_type = payload.get("eventType") or envelope.get("event")
        target_type = payload.get("targetType")
        target_id = payload.get("targetId")
        if target_type == "robot" and target_id and target_id != self.robot_code:
            return
        if event_type == "robot.offline":
            self.offline_mode.set()
            self.state = "Offline"
            self.cancel_current_task.set()
            self.publish_result(
                self.result_event(
                    "device.offline",
                    command_id=self.current_command_id,
                    task_id=self.current_task_id,
                    request_id=None,
                    trace_id=self.current_trace_id or envelope.get("traceId") or f"TRACE-{self.robot_code}-FAULT",
                    data={"reason": "simulation_event", "targetId": target_id, "state": self.state},
                    error=self.error("DEVICE_OFFLINE", "robot.offline injected by simulation console", retryable=True),
                    source="simulation-console",
                )
            )
            return
        if event_type == "fault.recovered":
            self.offline_mode.clear()
            self.path_blocked.clear()
            self.fail_next_action.clear()
            if not self.busy_lock.locked():
                self.state = "Idle"
            self.publish_result(
                self.result_event(
                    "fault.recovered",
                    command_id=self.current_command_id,
                    task_id=self.current_task_id,
                    request_id=None,
                    trace_id=self.current_trace_id or envelope.get("traceId") or f"TRACE-{self.robot_code}-FAULT",
                    data={"targetId": target_id, "state": self.state, **self.pose_data()},
                    source="simulation-console",
                )
            )
            return
        if event_type in {"action.failed", "task.failed"}:
            self.fail_next_action.set()
            return
        if event_type in {"resource.blocked", "path.blocked"}:
            self.path_blocked.set()
            self.cancel_current_task.set()
            self.publish_result(
                self.result_event(
                    "path.blocked",
                    command_id=self.current_command_id,
                    task_id=self.current_task_id,
                    request_id=None,
                    trace_id=self.current_trace_id or envelope.get("traceId") or f"TRACE-{self.robot_code}-PATH",
                    data={"targetType": target_type, "targetId": target_id, **self.pose_data()},
                    error=self.error("PATH_BLOCKED", "path blocked by simulation console", retryable=True),
                    source="simulation-console",
                )
            )

    def publish_command_accepted(
        self,
        command_id: str,
        task_id: str | None,
        request_id: str | None,
        trace_id: str,
    ) -> None:
        self.publish_result(
            self.result_event(
                "command.accepted",
                command_id=command_id,
                task_id=task_id,
                request_id=request_id,
                trace_id=trace_id,
                data={"acceptedAt": utc_now()},
            )
        )

    def publish_command_rejected(
        self,
        command_id: str | None,
        task_id: str | None,
        request_id: str | None,
        trace_id: str,
        error_code: str,
        error_message: str,
        retryable: bool = False,
    ) -> None:
        self.publish_result(
            self.result_event(
                "command.rejected",
                command_id=command_id,
                task_id=task_id,
                request_id=request_id,
                trace_id=trace_id,
                data={},
                error=self.error(error_code, error_message, retryable=retryable),
            )
        )

    def publish_pose(self) -> None:
        self.publish_result(
            self.result_event(
                "pose.updated",
                command_id=self.current_command_id,
                task_id=self.current_task_id,
                request_id=None,
                trace_id=self.current_trace_id or f"TRACE-{self.robot_code}-POSE",
                data={**self.pose_data(), "battery": self.battery, "state": self.state},
            ),
            qos=1,
            retain=False,
        )

    def publish_result(self, payload: dict[str, Any], qos: int = 1, retain: bool = False) -> None:
        self.client.publish(self.result_topic(), json.dumps(payload), qos=qos, retain=retain)

    def result_event(
        self,
        event_name: str,
        command_id: str | None,
        task_id: str | None,
        request_id: str | None,
        trace_id: str,
        data: dict[str, Any],
        error: dict[str, Any] | None = None,
        source: str = "device",
    ) -> dict[str, Any]:
        run_id = self.extract_run_id_from_context()
        return {
            "schemaVersion": "1.0",
            "messageType": "event",
            "messageId": protocol_id("MSG"),
            "scene_name": self.scene_name,
            "sceneName": self.scene_name,
            "runId": run_id,
            "run_id": run_id,
            "event": event_name,
            "eventId": protocol_id("EVT"),
            "commandId": command_id,
            "taskId": task_id,
            "requestId": request_id,
            "robotCode": self.robot_code,
            "traceId": trace_id,
            "source": source,
            "timestamp": utc_now(),
            "data": data,
            "error": error,
        }

    def remember_scene_name(self, payload: dict[str, Any]) -> None:
        scene_name = payload.get("scene_name") or payload.get("sceneName")
        if scene_name:
            self.scene_name = str(scene_name)

    @staticmethod
    def extract_run_id(payload: dict[str, Any]) -> str | None:
        run_id = payload.get("runId") or payload.get("run_id")
        return str(run_id) if run_id else None

    def extract_run_id_from_context(self) -> str | None:
        run_id = getattr(self.command_context, "run_id", None)
        return str(run_id) if run_id else self.current_run_id

    @staticmethod
    def error(error_code: str, error_message: str, retryable: bool, source: str = "device") -> dict[str, Any]:
        return {
            "errorCode": error_code,
            "errorMessage": error_message,
            "detail": error_message,
            "retryable": retryable,
            "source": source,
        }

    def pose_data(self) -> dict[str, float]:
        return {
            "x": self.position["x"],
            "y": self.position["y"],
            "z": self.position["z"],
            "yaw": self.position["yaw"],
        }

    def command_topic(self) -> str:
        return f"factory/dogs/{self.robot_code}/command"

    def result_topic(self) -> str:
        return f"factory/dogs/{self.robot_code}/result"


if __name__ == "__main__":
    VirtualRobotExecutor().run()
