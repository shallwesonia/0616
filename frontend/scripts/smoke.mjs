const frontendBase = process.env.FRONTEND_BASE ?? "http://localhost:5173";
const apiBase = process.env.API_BASE ?? "http://localhost:8000";

async function checkHttp(url, label) {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`${label} failed: HTTP ${response.status}`);
  }
  return response;
}

async function api(path, options) {
  const response = await fetch(`${apiBase}${path}`, {
    headers: { "Content-Type": "application/json", ...(options?.headers ?? {}) },
    ...options,
  });
  if (!response.ok) {
    throw new Error(`API ${path} failed: HTTP ${response.status} ${await response.text()}`);
  }
  return response.json();
}

async function main() {
  await checkHttp(`${frontendBase}/`, "open /");
  await checkHttp(`${frontendBase}/simulation`, "open /simulation");

  const scenarios = await api("/api/v1/scenarios");
  if (!Array.isArray(scenarios) || scenarios.length === 0) {
    throw new Error("scenario list is empty");
  }

  const run = await api("/api/v1/simulation-runs", {
    method: "POST",
    body: JSON.stringify({ scenarioId: scenarios[0].scenarioId, name: "smoke-baseline-run" }),
  });

  await api(`/api/v1/simulation-runs/${run.runId}/start`, { method: "POST" });

  const command = await api("/api/v1/actions", {
    method: "POST",
    body: JSON.stringify({
      runId: run.runId,
      robotCode: scenarios[0].robotCodes?.[0] ?? "robot-001",
      command: "where",
      params: { queryMode: "pose" },
      timeoutMs: 60000,
      operatorId: "smoke-test",
    }),
  });
  if (!command.commandId) {
    throw new Error("where action did not produce commandId");
  }

  const messages = await api(`/api/v1/simulation-runs/${run.runId}/messages`);
  if (!Array.isArray(messages)) {
    throw new Error("message flow response is not a list");
  }

  console.log(
    JSON.stringify(
      {
        ok: true,
        frontendBase,
        apiBase,
        scenarioId: scenarios[0].scenarioId,
        runId: run.runId,
        actionId: command.actionId,
        commandId: command.commandId,
        messageCount: messages.length,
      },
      null,
      2,
    ),
  );
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error);
  process.exit(1);
});
