from __future__ import annotations

import json
import os
from typing import Any

import paho.mqtt.client as mqtt

from .schemas import MessageRecord, RobotState, new_id, utc_now
from .store import JsonStore


class PlatformMqttBridge:
    def __init__(self, store: JsonStore):
        self.store = store
        self.host = os.getenv("MQTT_HOST", "localhost")
        self.port = int(os.getenv("MQTT_PORT", "1883"))
        self.env = os.getenv("SIM_ENV", "dev")
        self.site_id = os.getenv("SITE_ID", "site-a")
        self.client_id = os.getenv("MQTT_CLIENT_ID", "platform-api-local")
        self.enabled = os.getenv("MQTT_ENABLED", "true").lower() == "true"
        self.connected = False
        self.last_connect_at: str | None = None
        self.last_disconnect_at: str | None = None
        self.last_error: str | None = None
        self.client: mqtt.Client | None = None

    def start(self) -> None:
        if not self.enabled:
            return
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self.client_id)
        username = os.getenv("MQTT_USERNAME")
        password = os.getenv("MQTT_PASSWORD")
        if username:
            client.username_pw_set(username, password)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        self.client = client
        try:
            client.connect_async(self.host, self.port, keepalive=30)
            client.loop_start()
        except OSError as exc:
            self.last_error = str(exc)
            self._record_system_message("mqtt.connect.failed", {"error": str(exc)})

    def stop(self) -> None:
        if self.client is None:
            return
        self.client.loop_stop()
        self.client.disconnect()

    def publish_command(self, topic: str, payload: dict[str, Any]) -> bool:
        return self.publish(topic, payload, qos=1, retain=False)

    def publish(self, topic: str, payload: dict[str, Any], qos: int, retain: bool) -> bool:
        if self.client is None or not self.enabled:
            return False
        try:
            info = self.client.publish(topic, json.dumps(payload), qos=qos, retain=retain)
            return info.rc == mqtt.MQTT_ERR_SUCCESS
        except (OSError, RuntimeError, ValueError) as exc:
            self._record_system_message("mqtt.publish.failed", {"topic": topic, "error": str(exc)})
            return False

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: mqtt.ConnectFlags,
        reason_code: mqtt.ReasonCode,
        properties: mqtt.Properties | None,
    ) -> None:
        self.connected = True
        self.last_connect_at = utc_now()
        self.last_error = None
        client.subscribe("factory/dogs/+/result", qos=1)
        self._record_system_message("mqtt.connected", {"reasonCode": str(reason_code)})

    def _on_disconnect(
        self,
        client: mqtt.Client,
        userdata: Any,
        disconnect_flags: mqtt.DisconnectFlags,
        reason_code: mqtt.ReasonCode,
        properties: mqtt.Properties | None,
    ) -> None:
        self.connected = False
        self.last_disconnect_at = utc_now()
        self._record_system_message("mqtt.disconnected", {"reasonCode": str(reason_code)})

    def _on_message(self, client: mqtt.Client, userdata: Any, message: mqtt.MQTTMessage) -> None:
        try:
            envelope = json.loads(message.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self.last_error = str(exc)
            self._record_system_message("mqtt.payload.invalid", {"topic": message.topic, "error": str(exc)})
            return

        self.last_error = None
        if self._is_dog_result_topic(message.topic):
            self._handle_dog_result(message.topic, envelope)
            return

        message_type = envelope.get("messageType") or message.topic.rsplit("/", 1)[-1]
        robot_id = envelope.get("robotId") or self._robot_id_from_topic(message.topic)
        payload = envelope.get("payload", {})
        created_at = envelope.get("timestamp") or utc_now()

        self.store.append_message(
            MessageRecord(
                messageId=envelope.get("messageId") or new_id("msg"),
                messageType=message_type,
                source=envelope.get("source") or "mqtt",
                topic=message.topic,
                createdAt=created_at,
                payload=envelope,
            )
        )

        if message_type == "state" and robot_id:
            position = payload.get("position", {})
            self.store.upsert_robot_state(
                RobotState(
                    robotId=robot_id,
                    robotType=payload.get("robotType", "unknown"),
                    state=payload.get("robotState", "Idle"),
                    x=float(position.get("x", 0)),
                    y=float(position.get("y", 0)),
                    progress=int(payload.get("progress", 0)),
                    currentAction=payload.get("currentAction", "unknown"),
                    updatedAt=created_at,
                )
            )

    def _handle_dog_result(self, topic: str, envelope: dict[str, Any]) -> None:
        topic_robot_code = self._robot_code_from_result_topic(topic)
        robot_code = envelope.get("robotCode") or topic_robot_code
        if not robot_code or robot_code != topic_robot_code:
            self.last_error = "result topic robotCode mismatch"
            self._record_system_message(
                "mqtt.result.invalid",
                {"topic": topic, "robotCode": robot_code, "topicRobotCode": topic_robot_code},
            )
            return

        event_name = envelope.get("event", "unknown")
        created_at = envelope.get("timestamp") or utc_now()
        event_id = envelope.get("eventId") or new_id("evt")
        message_record = MessageRecord(
            messageId=event_id,
            messageType="event",
            source=envelope.get("source") or "device",
            topic=topic,
            createdAt=created_at,
            payload=envelope,
        )
        self.store.append_message(message_record)
        self._upsert_robot_from_result(robot_code, event_name, envelope, created_at)
        if hasattr(self.store, "ingest_observation_from_message"):
            self.store.ingest_observation_from_message(message_record)

    def _upsert_robot_from_result(
        self,
        robot_code: str,
        event_name: str,
        envelope: dict[str, Any],
        created_at: str,
    ) -> None:
        if event_name not in {
            "pose.updated",
            "where.result",
            "task.started",
            "task.succeeded",
            "task.failed",
            "task.stopped",
            "task.timeout",
            "device.offline",
        }:
            return

        data = envelope.get("data") or {}
        previous = next((robot for robot in self.store.robots() if robot.robotId == robot_code), None)
        previous_state = previous.state if previous else "Idle"
        previous_action = previous.currentAction if previous else "等待指令"
        previous_progress = previous.progress if previous else 0

        state_by_event = {
            "task.started": "Moving",
            "task.succeeded": "Idle",
            "task.failed": "Error",
            "task.stopped": "Idle",
            "task.timeout": "Error",
            "device.offline": "Offline",
        }
        progress_by_event = {
            "task.started": 0,
            "task.succeeded": 100,
            "task.failed": 0,
            "task.stopped": 0,
            "task.timeout": 0,
        }
        x = float(data.get("x", previous.x if previous else 0))
        y = float(data.get("y", previous.y if previous else 0))
        self.store.upsert_robot_state(
            RobotState(
                robotId=robot_code,
                robotType=previous.robotType if previous else "machine-dog",
                state=state_by_event.get(event_name, previous_state),
                x=x,
                y=y,
                progress=progress_by_event.get(event_name, previous_progress),
                currentAction=event_name if event_name != "pose.updated" else previous_action,
                updatedAt=created_at,
            )
        )

    def _record_system_message(self, message_type: str, payload: dict[str, Any]) -> None:
        self.store.append_message(
            MessageRecord(
                messageId=new_id("msg"),
                messageType=message_type,
                source="platform-api",
                topic="mqtt/system",
                createdAt=utc_now(),
                payload=payload,
            )
        )

    @staticmethod
    def _robot_id_from_topic(topic: str) -> str | None:
        parts = topic.split("/")
        if len(parts) >= 6 and parts[3] == "robot":
            return parts[4]
        return None

    @staticmethod
    def _is_dog_result_topic(topic: str) -> bool:
        parts = topic.split("/")
        return len(parts) == 4 and parts[0] == "factory" and parts[1] == "dogs" and parts[3] == "result"

    @staticmethod
    def _robot_code_from_result_topic(topic: str) -> str | None:
        parts = topic.split("/")
        if len(parts) == 4 and parts[0] == "factory" and parts[1] == "dogs" and parts[3] == "result":
            return parts[2]
        return None

    def health(self) -> dict[str, Any]:
        return {
            "status": "disabled" if not self.enabled else ("ok" if self.connected else "degraded"),
            "enabled": self.enabled,
            "connected": self.connected,
            "host": self.host,
            "port": self.port,
            "clientId": self.client_id,
            "lastConnectAt": self.last_connect_at,
            "lastDisconnectAt": self.last_disconnect_at,
            "lastError": self.last_error,
        }
