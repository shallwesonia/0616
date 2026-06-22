export type MapObjectType = "zone" | "obstacle" | "station" | "pathNode" | "resourcePoint";

export interface MapObject {
  id: string;
  type: MapObjectType;
  name: string;
  x: number;
  y: number;
  width?: number;
  height?: number;
  radius?: number;
  color: string;
}

export interface PathEdge {
  id: string;
  from: string;
  to: string;
  direction: "one_way" | "two_way";
  capacity: number;
}

export interface SiteMap {
  id: string;
  name: string;
  width: number;
  height: number;
  unit: string;
  gridSize: number;
  objects: MapObject[];
  pathEdges: PathEdge[];
  configVersion: string;
}

export interface RobotState {
  robotId: string;
  robotType: string;
  state: string;
  x: number;
  y: number;
  progress: number;
  currentAction: string;
  updatedAt: string;
}

export interface MessageRecord {
  messageId: string;
  messageType: string;
  source: string;
  topic: string;
  createdAt: string;
  payload: Record<string, unknown>;
}

export interface DraftResponse {
  draftId: string;
  map: SiteMap;
}

export interface HealthResponse {
  status: string;
  time: string;
  components: {
    api: { status: string };
    storage: Record<string, unknown> & { status: string };
    mqttBridge: Record<string, unknown> & { status: string; connected?: boolean };
    virtualExecutor: Record<string, unknown> & { status: string; lastHeartbeatAt?: string | null };
  };
  recent?: {
    lastCommand?: MessageRecord | null;
    lastEvent?: MessageRecord | null;
    lastError?: MessageRecord | null;
  };
}

export interface CommandResponse {
  commandId: string;
  topic: string;
  payload: Record<string, unknown>;
}

export interface SessionSnapshot {
  messageId: string;
  type: "snapshot";
  sessionId: string;
  timestamp: string;
  data: {
    robots: RobotState[];
    messages: MessageRecord[];
  };
}

export interface ConnectionInfo {
  schemaVersion: string;
  publicHost: string;
  services: {
    frontend: { protocol: string; url: string; lanPort: number };
    api: { protocol: string; baseUrl: string; openApiUrl: string; lanPort: number };
    websocket: { protocol: string; url: string; backendUrl: string };
    mqtt: {
      protocol: string;
      host: string;
      port: number;
      internalHost: string;
      internalPort: number;
      topicPrefix: string;
      commandTopic: string;
      resultTopic: string;
      supportedCommands: string[];
      resultEvents: string[];
    };
  };
  rules: string[];
}

export interface CommandTrace {
  commandId: string;
  messageCount: number;
  messages: MessageRecord[];
}

export interface MqttContract {
  schemaVersion: string;
  protocolVersion: string;
  topicPattern: string;
  command: {
    topic: string;
    supportedCommands: string[];
  };
  result: {
    topic: string;
    events: string[];
  };
}
