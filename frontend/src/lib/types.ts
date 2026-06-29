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
  pathGroupId?: string | null;
  sequence?: number | null;
  speedLimit?: number | null;
  allowedRobotTypes?: string[];
}

export interface PathGroup {
  id: string;
  name: string;
  edgeIds: string[];
  allowedRobotCodes: string[];
  color: string;
  status: "active" | "disabled" | "blocked";
  priority: number;
  metadata: Record<string, unknown>;
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
  pathGroups: PathGroup[];
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

export interface RobotCreate {
  robotCode: string;
  robotName?: string | null;
  robotType: string;
  x: number;
  y: number;
  state?: string;
  currentAction?: string;
  capabilities?: string[];
  actionSetId?: string;
  mapId?: string;
  createMode?: "config_only" | "start_virtual_executor" | "bind_real_gateway";
  executorEndpoint?: string | null;
}

export type TargetType =
  | "cargo"
  | "container"
  | "station"
  | "resource"
  | "mapObject"
  | "inspectionPoint"
  | "zone"
  | "pathNode"
  | "pathEdge"
  | "pathGroup";

export interface TargetRegistryItem {
  targetId: string;
  targetType: TargetType;
  displayName: string;
  mapId: string;
  pose?: { x: number; y: number; z?: number; yaw?: number } | null;
  geometryRef?: string | null;
  metadata: Record<string, unknown>;
  status: "active" | "inactive" | "blocked" | "deleted";
  version: string;
  createdAt: string;
  updatedAt: string;
}

export interface RobotConfig {
  robotCode: string;
  robotName?: string | null;
  robotType: string;
  status: "created" | "enabled" | "disabled" | "deleted";
  enabled: boolean;
  capabilities: string[];
  actionSetId: string;
  mapId: string;
  initialPose: { x: number; y: number; z?: number; yaw?: number };
  createMode: "config_only" | "start_virtual_executor" | "bind_real_gateway";
  executorEndpoint?: string | null;
  metadata: Record<string, unknown>;
  executorId?: string | null;
  executorStatus?: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface ExecutorInstance {
  executorId: string;
  robotCode: string;
  executorType: "virtual" | "real_gateway";
  status: "unbound" | "binding" | "active" | "offline" | "error" | "stopped" | "restarting" | "replaced";
  mqttClientId: string;
  lastHeartbeatAt?: string | null;
  containerName?: string | null;
  gatewayEndpoint?: string | null;
  startedAt?: string | null;
  updatedAt: string;
  metadata: Record<string, unknown>;
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
  robots?: Array<{
    robotCode: string;
    robotType: string;
    initialPose?: { x: number; y: number };
    state?: string;
    capabilities?: string[];
  }>;
  robotTypeIds: string[];
  actionSet: Record<string, unknown> & { commands?: string[] };
  taskFlow: Record<string, unknown>;
  resourceProfile: Record<string, unknown>;
  map: SiteMap;
}

export interface ScenarioValidationCheck {
  code: string;
  label: string;
  status: "passed" | "warning" | "failed";
  detail: string;
}

export interface ScenarioValidationResponse {
  scenarioId: string;
  ok: boolean;
  issues: string[];
  checks: ScenarioValidationCheck[];
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

export interface BatchTaskResponse {
  batchId: string;
  runId: string;
  requestedCount: number;
  createdCount: number;
  tasks: SimulationTask[];
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

export interface ActionCommandSpecParam {
  label: string;
  type: "number" | "string" | "select" | "target" | "pathGroup";
  required?: boolean;
  min?: number | null;
  max?: number | null;
  options?: string[] | null;
  targetTypes?: TargetType[] | null;
  unit?: string | null;
  description?: string | null;
}

export interface ActionCommandSpec {
  command: string;
  label: string;
  required: string[];
  defaults: Record<string, string | number | null>;
  fields: Record<string, ActionCommandSpecParam>;
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

export interface MessageReplayResponse {
  replayId: string;
  runId: string;
  replayMode: string;
  sandbox: boolean;
  message: MessageRecord;
  observation?: Observation | null;
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

export interface TraceGraphNode {
  id: string;
  label: string;
  type: string;
  entityId: string;
  status: string;
  startedAt?: string | null;
}

export interface TraceGraphEdge {
  from: string;
  to: string;
  type: string;
}

export interface TraceGraph {
  traceId: string;
  status: string;
  nodes: TraceGraphNode[];
  edges: TraceGraphEdge[];
}

export interface RunMessageMetrics {
  runId: string;
  messageCount: number;
  categoryCounts: Record<string, number>;
  eventCounts: Record<string, number>;
  duplicateCount: number;
  timeoutCount: number;
  errorCount: number;
  ackDelayMs: {
    count: number;
    avg?: number | null;
    max?: number | null;
  };
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
