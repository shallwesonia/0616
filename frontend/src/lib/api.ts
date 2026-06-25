import type {
  CommandResponse,
  CommandTrace,
  ConnectionInfo,
  CurrentState,
  DraftResponse,
  ExecutorInstance,
  BatchTaskResponse,
  ActionCommandSpec,
  HealthResponse,
  MessageReplayResponse,
  MessageRecord,
  MqttContract,
  Observation,
  RobotConfig,
  RobotCreate,
  RobotState,
  ScenarioSummary,
  ScenarioValidationResponse,
  SimulationAction,
  SimulationRun,
  SimulationTask,
  Snapshot,
  SiteMap,
  TaskTemplate,
  RunMessageMetrics,
  TraceGraph,
  TraceResponse,
  TargetRegistryItem
} from "./types";

const API_BASE = import.meta.env.VITE_API_BASE ?? "";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(options?.headers ?? {})
    },
    ...options
  });
  if (!response.ok) {
    throw new Error(`API ${response.status}: ${await response.text()}`);
  }
  return response.json() as Promise<T>;
}

export function getMap() {
  return request<SiteMap>("/api/v1/maps/current");
}

export function getHealth() {
  return request<HealthResponse>("/api/v1/health");
}

export function getConnections() {
  return request<ConnectionInfo>("/api/v1/connections");
}

export function getMqttContract() {
  return request<MqttContract>("/api/v1/mqtt/contract");
}

export function saveMapDraft(map: SiteMap) {
  return request<DraftResponse>(`/api/v1/maps/${map.id}/drafts`, {
    method: "POST",
    body: JSON.stringify({ map })
  });
}

export function validateDraft(mapId: string, draftId: string) {
  return request<{ ok: boolean; issues: string[] }>(
    `/api/v1/maps/${mapId}/drafts/${draftId}/validate`,
    { method: "POST" }
  );
}

export function publishDraft(mapId: string, draftId: string) {
  return request<SiteMap>(`/api/v1/maps/${mapId}/drafts/${draftId}/publish`, {
    method: "POST"
  });
}

export function getRobots() {
  return request<RobotState[]>("/api/v1/robots");
}

export function createRobot(robot: RobotCreate) {
  return request<RobotState>("/api/v1/robots", {
    method: "POST",
    body: JSON.stringify(robot)
  });
}

export function getTargets() {
  return request<TargetRegistryItem[]>("/api/v1/targets");
}

export function getRobotConfigs() {
  return request<RobotConfig[]>("/api/v1/robot-configs");
}

export function createRobotConfig(robot: {
  robotCode: string;
  robotName?: string | null;
  robotType: string;
  initialPose: { x: number; y: number; z?: number; yaw?: number };
  createMode?: "config_only" | "start_virtual_executor" | "bind_real_gateway";
  executorEndpoint?: string | null;
}) {
  return request<RobotConfig>("/api/v1/robot-configs", {
    method: "POST",
    body: JSON.stringify(robot)
  });
}

export function getExecutors(robotCode?: string) {
  const query = robotCode ? `?robotCode=${encodeURIComponent(robotCode)}` : "";
  return request<ExecutorInstance[]>(`/api/v1/executors${query}`);
}

export function getMessages() {
  return request<MessageRecord[]>("/api/v1/messages");
}

export function createCommand(command: {
  robotId: string;
  robotCode?: string;
  commandType?: string;
  command?: string;
  target?: Record<string, unknown>;
  params?: Record<string, unknown>;
  parameters?: Record<string, unknown>;
}) {
  return request<CommandResponse>("/api/v1/commands", {
    method: "POST",
    body: JSON.stringify({
      timeoutMs: 60000,
      priority: 5,
      issuedBy: "console",
      ...command
    })
  });
}

export function getCommandTrace(commandId: string) {
  return request<CommandTrace>(`/api/v1/commands/${commandId}/trace`);
}

export function createExport(exportType: string) {
  return request<{ exportId: string; fileName: string; url: string }>("/api/v1/exports", {
    method: "POST",
    body: JSON.stringify({ exportType })
  });
}

export function importMap(map: SiteMap) {
  return request<{ draftId: string; ok: boolean; issues: string[]; map: SiteMap }>(
    "/api/v1/imports/map",
    {
      method: "POST",
      body: JSON.stringify({ map })
    }
  );
}

export function triggerConsoleEvent(eventType: string) {
  return request<{ eventId: string; mqttPublished: boolean }>("/api/v1/events", {
    method: "POST",
    body: JSON.stringify({
      eventType,
      severity: eventType.includes("error") ? "error" : "warning",
      eventData: { source: "frontend-console" }
    })
  });
}

export function getSessionSocketUrl(sessionId: string) {
  const path = `/ws/v1/sessions/${sessionId}`;
  const base = import.meta.env.VITE_WS_BASE ?? API_BASE;
  const url = new URL(path, base || window.location.origin);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

export function getScenarios() {
  return request<ScenarioSummary[]>("/api/v1/scenarios");
}

export function validateScenario(scenarioId: string) {
  return request<ScenarioValidationResponse>(`/api/v1/scenarios/${encodeURIComponent(scenarioId)}/validation`);
}

export function getTaskTemplates() {
  return request<TaskTemplate[]>("/api/v1/task-templates");
}

export function getActionCommandSpecs() {
  return request<ActionCommandSpec[]>("/api/v1/action-command-specs");
}

export function createSimulationRun(scenarioId: string, name?: string) {
  return request<SimulationRun>("/api/v1/simulation-runs", {
    method: "POST",
    body: JSON.stringify({ scenarioId, name })
  });
}

export function getSimulationRuns() {
  return request<SimulationRun[]>("/api/v1/simulation-runs");
}

export function startSimulationRun(runId: string) {
  return request<SimulationRun>(`/api/v1/simulation-runs/${runId}/start`, { method: "POST" });
}

export function pauseSimulationRun(runId: string) {
  return request<SimulationRun>(`/api/v1/simulation-runs/${runId}/pause`, { method: "POST" });
}

export function resumeSimulationRun(runId: string) {
  return request<SimulationRun>(`/api/v1/simulation-runs/${runId}/resume`, { method: "POST" });
}

export function stopSimulationRun(runId: string) {
  return request<SimulationRun>(`/api/v1/simulation-runs/${runId}/stop`, { method: "POST" });
}

export function createSimulationTask(
  runId: string,
  payload: {
    goal: string;
    input?: Record<string, unknown>;
    constraints?: Record<string, unknown>;
    priority?: number;
    expectedOutcome?: string;
  }
) {
  return request<SimulationTask>(`/api/v1/simulation-runs/${runId}/tasks`, {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export function createSimulationTaskFromTemplate(
  runId: string,
  templateId: string,
  parameters: Record<string, unknown>
) {
  return request<SimulationTask>(`/api/v1/simulation-runs/${runId}/tasks/from-template`, {
    method: "POST",
    body: JSON.stringify({ templateId, parameters })
  });
}

export function createSimulationTasksBatch(
  runId: string,
  payload: {
    templateId?: string | null;
    goal: string;
    count: number;
    intervalMs?: number;
    priority?: number;
    targetRange?: Record<string, unknown>;
    parameters?: Record<string, unknown>;
    randomSeed?: number | null;
    randomizeRobot?: boolean;
    randomizeTaskType?: boolean;
    autoRun?: boolean;
    createdBy?: string;
  }
) {
  return request<BatchTaskResponse>(`/api/v1/simulation-runs/${runId}/tasks/batch`, {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export function getSimulationTasks(runId: string) {
  return request<SimulationTask[]>(`/api/v1/simulation-runs/${runId}/tasks`);
}

export function createSimulationAction(payload: {
  runId: string;
  taskId?: string | null;
  planId?: string | null;
  planStepId?: string | null;
  robotCode?: string | null;
  command: string;
  params?: Record<string, unknown>;
  timeoutMs?: number;
  operatorId?: string;
}) {
  return request<SimulationAction>("/api/v1/actions", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export function getSimulationActions(runId: string) {
  return request<SimulationAction[]>(`/api/v1/actions?runId=${encodeURIComponent(runId)}`);
}

export function getCurrentState(runId: string) {
  return request<CurrentState>(`/api/v1/current-states/${runId}`);
}

export function getRunMessages(runId: string, category?: string) {
  const query = category ? `?category=${encodeURIComponent(category)}` : "";
  return request<MessageRecord[]>(`/api/v1/simulation-runs/${runId}/messages${query}`);
}

export function getRunMessageMetrics(runId: string) {
  return request<RunMessageMetrics>(`/api/v1/simulation-runs/${runId}/message-metrics`);
}

export function replayRunMessage(
  runId: string,
  messageId: string,
  payload: {
    replayMode?: "single" | "task" | "time_window";
    sandbox?: boolean;
    reason?: string;
    operatorId?: string;
  } = {}
) {
  return request<MessageReplayResponse>(
    `/api/v1/simulation-runs/${runId}/messages/${encodeURIComponent(messageId)}/replay`,
    {
      method: "POST",
      body: JSON.stringify({ replayMode: "single", sandbox: true, reason: "operator replay", ...payload })
    }
  );
}

export function getRunObservations(runId: string) {
  return request<Observation[]>(`/api/v1/simulation-runs/${runId}/observations`);
}

export function injectSimulationEvent(
  runId: string,
  payload: {
    eventType: string;
    targetType: "robot" | "path" | "station" | "interface" | "message" | "resource";
    targetId?: string | null;
    severity?: "info" | "warning" | "error" | "critical";
    data?: Record<string, unknown>;
    durationMs?: number | null;
    autoRecover?: boolean;
  }
) {
  return request<Observation>(`/api/v1/simulation-runs/${runId}/events`, {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export function recoverSimulationEvent(
  runId: string,
  payload: {
    eventType?: string | null;
    targetType: "robot" | "path" | "station" | "interface" | "message" | "resource";
    targetId?: string | null;
    recoveryMode?: "manual" | "auto" | "retry" | "reschedule" | "skip_step" | "takeover" | "terminate_task";
    reason?: string;
    operatorId?: string;
  }
) {
  return request<Observation>(`/api/v1/simulation-runs/${runId}/events/recover`, {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export function createSimulationSnapshot(runId: string, reason: string) {
  return request<Snapshot>(`/api/v1/simulation-runs/${runId}/snapshots`, {
    method: "POST",
    body: JSON.stringify({ reason })
  });
}

export function getTrace(traceId: string) {
  return request<TraceResponse>(`/api/v1/traces/${traceId}`);
}

export function getTaskTrace(taskId: string) {
  return request<TraceResponse>(`/api/v1/tasks/${encodeURIComponent(taskId)}/trace`);
}

export function getActionTrace(actionId: string) {
  return request<TraceResponse>(`/api/v1/actions/${encodeURIComponent(actionId)}/trace`);
}

export function getTraceGraph(traceId: string) {
  return request<TraceGraph>(`/api/v1/traces/${encodeURIComponent(traceId)}/graph`);
}

export function exportSimulationRun(runId: string) {
  return request<Record<string, unknown>>(`/api/v1/simulation-runs/${runId}/export`);
}

export function getSimulationSocketUrl(workspaceId: string, runId: string) {
  const path = `/ws/v1/workspaces/${workspaceId}/runs/${runId}`;
  const base = import.meta.env.VITE_WS_BASE ?? API_BASE;
  const url = new URL(path, base || window.location.origin);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}
