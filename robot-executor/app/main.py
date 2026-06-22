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
        self.busy_lock = threading.Lock()
        self.cancel_current_task = threading.Event()
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

        if command_payload.get("messageType") != "command" or command_payload.get("robotCode") != self.robot_code:
            self.publish_command_rejected(command_id, task_id, request_id, trace_id, "INVALID_TARGET_POSE", "invalid command payload")
            return
        if command_name == "where":
            self.handle_where(command_id, request_id, trace_id)
            return
        if command_name == "stop":
            self.handle_stop(command_id, task_id, trace_id, params)
            return
        if command_name == "goto_pose":
            self.execute_goto_pose(command_id, task_id, trace_id, params)
            return
        self.publish_command_rejected(command_id, task_id, request_id, trace_id, "INVALID_TARGET_POSE", "unsupported command")

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
        self.state = "Moving"
        try:
            self.publish_command_accepted(command_id, task_id, None, trace_id)
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

            min_ms, max_ms = ACTION_PROFILES["goto_pose"]
            planned_ms = random.randint(min_ms, max_ms)
            duration_s = max(0.2, (planned_ms / 1000) * self.time_scale)
            steps = max(5, int(duration_s / 1))
            start_x = self.position["x"]
            start_y = self.position["y"]
            target_x = float(params["x"])
            target_y = float(params["y"])

            for step in range(1, steps + 1):
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
                time.sleep(duration_s / steps)

            self.position["x"] = target_x
            self.position["y"] = target_y
            self.state = "Idle"
            self.publish_pose()
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
            self.cancel_current_task.clear()
            self.busy_lock.release()

    def handle_where(self, command_id: str, request_id: str | None, trace_id: str) -> None:
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
                data={**self.pose_data(), "battery": self.battery},
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
        payload = envelope.get("payload", {})
        if payload.get("eventType") == "resource.blocked":
            self.cancel_current_task.set()

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
        return {
            "schemaVersion": "1.0",
            "messageType": "event",
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
