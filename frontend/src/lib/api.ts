import type {
  CommandResponse,
  CommandTrace,
  ConnectionInfo,
  DraftResponse,
  HealthResponse,
  MessageRecord,
  MqttContract,
  RobotState,
  SiteMap
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
