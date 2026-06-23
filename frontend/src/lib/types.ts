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

export interface ScenarioSummary {
  scenarioId: string;
  name: string;
  siteMapId: string;
  siteMapVersion: string;
  robotCodes: string[];
  robotTypeIds: string[];
  actionSet: Record<string, unknown> & { commands?: string[] };
  taskFlow: Record<string, unknown>;
  resourceProfile: Record<string, unknown>;
  map: SiteMap;
}

export interface TaskTemplate {
  templateId: string;
  name: string;
  description: string;
  defaultGoal: string;
  defaultInput: Record<string, unknown>;
  supportedCommands: string[];
}

export interface PlanStep {
  planStepId: string;
  sequence: number;
  actionType: string;
  target: Record<string, unknown>;
  params: Record<string, unknown>;
  dependsOn: string[];
  successCondition?: string | null;
  failurePolicy: string;
  timeoutMs: number;
  status: string;
}

export interface SimulationPlan {
  planId: string;
  runId: string;
  taskId: string;
  traceId: string;
  planVersion: number;
  strategy: string;
  steps: PlanStep[];
  dependencies: Record<string, unknown>;
  assumptions: Record<string, unknown>;
  generatedBy: string;
  generationLatencyMs: number;
  status: string;
  createdAt: string;
  activatedAt?: string | null;
}

export interface SimulationTask {
  taskId: string;
  runId: string;
  traceId: string;
  goal: string;
  input: Record<string, unknown>;
  constraints: Record<string, unknown>;
  priority: number;
  expectedOutcome?: string | null;
  status: string;
  createdBy: string;
  createdAt: string;
  startedAt?: string | null;
  finishedAt?: string | null;
  activePlan?: SimulationPlan | null;
}

export interface SimulationRun {
  runId: string;
  scenarioId: string;
  name: string;
  status: string;
  mapId: string;
  mapVersion: string;
  scenario: Record<string, unknown>;
  createdAt: string;
  startedAt?: string | null;
  finishedAt?: string | null;
  updatedAt: string;
}

export interface SimulationAction {
  actionId: string;
  runId: string;
  taskId?: string | null;
  planId?: string | null;
  planStepId?: string | null;
  traceId: string;
  robotCode: string;
  command: string;
  params: Record<string, unknown>;
  commandId?: string | null;
  requestId?: string | null;
  attemptNo: number;
  timeoutMs: number;
  status: string;
  result?: Record<string, unknown> | null;
  createdAt: string;
  issuedAt?: string | null;
  startedAt?: string | null;
  finishedAt?: string | null;
}

export interface Observation {
  observationId: string;
  runId: string;
  taskId?: string | null;
  actionId?: string | null;
  traceId?: string | null;
  source: string;
  event: string;
  category: "Ack" | "Telemetry" | "Event" | "Alert" | string;
  eventId?: string | null;
  messageId?: string | null;
  robotCode?: string | null;
  commandId?: string | null;
  requestId?: string | null;
  timestamp: string;
  data: Record<string, unknown>;
  error?: Record<string, unknown> | null;
  processingStatus: string;
}

export interface CurrentState {
  runId: string;
  stateVersion: number;
  taskState: Record<string, unknown>;
  activePlan?: Record<string, unknown> | null;
  robotStates: Array<Record<string, unknown>>;
  resourceStates: Record<string, unknown>;
  environmentState: Record<string, unknown>;
  pendingActions: Array<Record<string, unknown>>;
  activeEvents: Array<Record<string, unknown>>;
  lastObservationId?: string | null;
  lastObservationAt?: string | null;
  updatedAt: string;
}

export interface Snapshot {
  snapshotId: string;
  runId: string;
  taskId?: string | null;
  traceId?: string | null;
  stateVersion: number;
  reason: string;
  snapshot: Record<string, unknown>;
  checksum: string;
  createdAt: string;
}

export interface TraceSpan {
  spanId: string;
  parentSpanId?: string | null;
  traceId: string;
  runId: string;
  taskId?: string | null;
  entityType: string;
  entityId: string;
  operation: string;
  status: string;
  startedAt: string;
  finishedAt?: string | null;
  durationMs?: number | null;
  inputRef?: string | null;
  outputRef?: string | null;
  errorRef?: string | null;
}

export interface TraceResponse {
  traceId: string;
  runId?: string | null;
  taskId?: string | null;
  status: string;
  startedAt?: string | null;
  finishedAt?: string | null;
  durationMs?: number | null;
  spans: TraceSpan[];
}

export interface SimulationSnapshot {
  messageId: string;
  type: "simulation.snapshot";
  workspaceId: string;
  runId: string;
  timestamp: string;
  data: {
    run: SimulationRun | null;
    currentState: CurrentState | null;
    tasks: SimulationTask[];
    actions: SimulationAction[];
    messages: MessageRecord[];
    observations: Observation[];
  };
}
