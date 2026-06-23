import { motion } from "framer-motion";
import {
  AlertTriangle,
  ArrowLeft,
  Bot,
  Box,
  ClipboardList,
  Download,
  Gauge,
  Map,
  MessageSquareText,
  Play,
  Radio,
  Route,
  Send,
  Square,
  Workflow,
  Zap
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Badge, Button, Panel } from "./components/ui";
import {
  createSimulationAction,
  createSimulationRun,
  createSimulationSnapshot,
  createSimulationTask,
  createSimulationTaskFromTemplate,
  exportSimulationRun,
  getCurrentState,
  getRunMessages,
  getRunObservations,
  getScenarios,
  getSimulationActions,
  getSimulationRuns,
  getSimulationSocketUrl,
  getSimulationTasks,
  getTaskTemplates,
  getTrace,
  injectSimulationEvent,
  startSimulationRun,
  stopSimulationRun
} from "./lib/api";
import type {
  CurrentState,
  MapObject,
  MessageRecord,
  Observation,
  RobotState,
  ScenarioSummary,
  SimulationAction,
  SimulationRun,
  SimulationSnapshot,
  SimulationTask,
  SiteMap,
  TaskTemplate,
  TraceResponse
} from "./lib/types";

const WORKSPACE_ID = "00000000-0000-0000-0000-000000000001";

const exceptionOptions = [
  { value: "robot.offline", label: "机器人离线", targetType: "robot" },
  { value: "action.failed", label: "动作失败", targetType: "robot" },
  { value: "path.blocked", label: "路径阻塞", targetType: "path" },
  { value: "interface.timeout", label: "接口超时", targetType: "interface" },
  { value: "message.dropped", label: "消息丢失", targetType: "message" }
] as const;

const messageFilters = ["All", "Command", "Ack", "Telemetry", "Event", "Alert"] as const;

export function SimulationDashboard() {
  const [scenarios, setScenarios] = useState<ScenarioSummary[]>([]);
  const [templates, setTemplates] = useState<TaskTemplate[]>([]);
  const [runs, setRuns] = useState<SimulationRun[]>([]);
  const [run, setRun] = useState<SimulationRun | null>(null);
  const [tasks, setTasks] = useState<SimulationTask[]>([]);
  const [actions, setActions] = useState<SimulationAction[]>([]);
  const [currentState, setCurrentState] = useState<CurrentState | null>(null);
  const [messages, setMessages] = useState<MessageRecord[]>([]);
  const [observations, setObservations] = useState<Observation[]>([]);
  const [trace, setTrace] = useState<TraceResponse | null>(null);
  const [socketState, setSocketState] = useState<"idle" | "open" | "fallback">("idle");
  const [selectedScenarioId, setSelectedScenarioId] = useState("default-site-a");
  const [templateId, setTemplateId] = useState("");
  const [taskGoal, setTaskGoal] = useState("搬运到分拣工位");
  const [command, setCommand] = useState<"goto_pose" | "where" | "stop">("goto_pose");
  const [targetX, setTargetX] = useState(760);
  const [targetY, setTargetY] = useState(420);
  const [messageFilter, setMessageFilter] = useState<(typeof messageFilters)[number]>("All");
  const [exceptionType, setExceptionType] = useState<(typeof exceptionOptions)[number]["value"]>("robot.offline");
  const [exceptionTarget, setExceptionTarget] = useState("robot-001");
  const [status, setStatus] = useState("仿真驾驶舱待连接");

  const selectedScenario = useMemo(
    () => scenarios.find((item) => item.scenarioId === selectedScenarioId) ?? scenarios[0],
    [scenarios, selectedScenarioId]
  );

  const activeTask = tasks[0] ?? null;
  const robots = useMemo(() => robotsFromState(currentState, selectedScenario), [currentState, selectedScenario]);
  const filteredMessages = useMemo(() => {
    if (messageFilter === "All") {
      return messages;
    }
    return messages.filter((message) => messageCategory(message) === messageFilter);
  }, [messageFilter, messages]);

  async function bootstrap() {
    const [nextScenarios, nextTemplates, nextRuns] = await Promise.all([
      getScenarios(),
      getTaskTemplates(),
      getSimulationRuns()
    ]);
    setScenarios(nextScenarios);
    setTemplates(nextTemplates);
    setRuns(nextRuns);
    setSelectedScenarioId(nextScenarios[0]?.scenarioId ?? "default-site-a");
    setTemplateId(nextTemplates[0]?.templateId ?? "");
    if (nextRuns[0]) {
      setRun(nextRuns[0]);
      await refreshRun(nextRuns[0].runId);
    }
    setStatus("已连接平台 API");
  }

  async function refreshRun(runId: string) {
    const [nextTasks, nextActions, nextState, nextMessages, nextObservations] = await Promise.all([
      getSimulationTasks(runId),
      getSimulationActions(runId),
      getCurrentState(runId),
      getRunMessages(runId),
      getRunObservations(runId)
    ]);
    setTasks(nextTasks);
    setActions(nextActions);
    setCurrentState(nextState);
    setMessages(nextMessages);
    setObservations(nextObservations);
    const traceId = nextTasks[0]?.traceId ?? nextActions[0]?.traceId;
    if (traceId) {
      setTrace(await getTrace(traceId));
    }
  }

  useEffect(() => {
    void bootstrap().catch((error) => setStatus(error instanceof Error ? error.message : "平台连接失败"));
  }, []);

  useEffect(() => {
    if (!run) {
      return;
    }
    let disposed = false;
    const socket = new WebSocket(getSimulationSocketUrl(WORKSPACE_ID, run.runId));
    socket.onopen = () => setSocketState("open");
    socket.onmessage = (event) => {
      if (disposed) {
        return;
      }
      const snapshot = JSON.parse(event.data) as SimulationSnapshot;
      if (snapshot.type !== "simulation.snapshot") {
        return;
      }
      setRun(snapshot.data.run);
      setCurrentState(snapshot.data.currentState);
      setTasks(snapshot.data.tasks);
      setActions(snapshot.data.actions);
      setMessages(snapshot.data.messages);
      setObservations(snapshot.data.observations);
    };
    socket.onerror = () => socket.close();
    socket.onclose = () => {
      if (!disposed) {
        setSocketState("fallback");
      }
    };
    return () => {
      disposed = true;
      socket.close();
    };
  }, [run?.runId]);

  useEffect(() => {
    if (!run || socketState === "open") {
      return;
    }
    const timer = window.setInterval(() => {
      void refreshRun(run.runId).catch(() => undefined);
    }, 3000);
    return () => window.clearInterval(timer);
  }, [run, socketState]);

  async function handleCreateRun() {
    const nextRun = await createSimulationRun(selectedScenarioId, `${selectedScenario?.name ?? "Scenario"} Run`);
    const started = await startSimulationRun(nextRun.runId);
    setRun(started);
    setRuns([started, ...runs]);
    setStatus(`已创建运行 ${started.runId}`);
    await refreshRun(started.runId);
  }

  async function handleStopRun() {
    if (!run) {
      return;
    }
    const stopped = await stopSimulationRun(run.runId);
    setRun(stopped);
    setStatus(`已停止运行 ${stopped.runId}`);
    await refreshRun(stopped.runId);
  }

  async function handleCreateTask(useTemplate: boolean) {
    if (!run) {
      return;
    }
    const nextTask = useTemplate
      ? await createSimulationTaskFromTemplate(run.runId, templateId, {
          goal: taskGoal,
          target: { x: targetX, y: targetY, z: 0, yaw: 0 }
        })
      : await createSimulationTask(run.runId, {
          goal: taskGoal,
          input: { command: "goto_pose", target: { x: targetX, y: targetY, z: 0, yaw: 0 } },
          priority: 5
        });
    setStatus(`已创建任务 ${nextTask.taskId}`);
    await refreshRun(run.runId);
  }

  async function handleSendAction() {
    if (!run) {
      return;
    }
    const robotCode = selectedScenario?.robotCodes[0] ?? robots[0]?.robotId ?? "robot-001";
    const params = command === "goto_pose" ? { x: targetX, y: targetY, z: 0, yaw: 0 } : {};
    const nextAction = await createSimulationAction({
      runId: run.runId,
      taskId: activeTask?.taskId,
      planId: activeTask?.activePlan?.planId,
      planStepId: activeTask?.activePlan?.steps[0]?.planStepId,
      robotCode,
      command,
      params,
      timeoutMs: 60000,
      operatorId: "simulation-console"
    });
    setStatus(`已下发 Action ${nextAction.actionId}`);
    await refreshRun(run.runId);
  }

  async function handleInjectException() {
    if (!run) {
      return;
    }
    const option = exceptionOptions.find((item) => item.value === exceptionType) ?? exceptionOptions[0];
    await injectSimulationEvent(run.runId, {
      eventType: option.value,
      targetType: option.targetType,
      targetId: exceptionTarget,
      severity: option.value === "robot.offline" ? "critical" : "error",
      data: { source: "simulation-dashboard" },
      autoRecover: false
    });
    setStatus(`已注入异常 ${option.label}`);
    await refreshRun(run.runId);
  }

  async function handleSnapshot() {
    if (!run) {
      return;
    }
    const snapshot = await createSimulationSnapshot(run.runId, "operator-checkpoint");
    setStatus(`已创建快照 ${snapshot.snapshotId}`);
  }

  async function handleExport() {
    if (!run) {
      return;
    }
    const payload = await exportSimulationRun(run.runId);
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${run.runId}-simulation-run.json`;
    link.click();
    URL.revokeObjectURL(url);
  }

  return (
    <main className="min-h-screen bg-[#f5f5f7] text-neutral-950">
      <div className="mx-auto flex max-w-[1800px] flex-col gap-4 p-4 lg:p-6">
        <motion.header
          initial={{ opacity: 0, y: -8 }}
          animate={{ opacity: 1, y: 0 }}
          className="flex flex-col gap-3 border-b border-neutral-200 bg-white/70 px-1 pb-4 backdrop-blur lg:flex-row lg:items-center lg:justify-between"
        >
          <div>
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <Badge tone="blue">仿真驾驶舱</Badge>
              <Badge tone={run?.status === "Running" ? "green" : "neutral"}>{run?.status ?? "No Run"}</Badge>
              <Badge tone={socketState === "open" ? "green" : "amber"}>
                {socketState === "open" ? "实时" : "轮询"}
              </Badge>
              <Badge tone="neutral">{status}</Badge>
            </div>
            <h1 className="text-2xl font-semibold tracking-tight lg:text-3xl">Simulation Cockpit</h1>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" onClick={() => (window.location.href = "/")}>
              <ArrowLeft size={16} />
              地图配置
            </Button>
            <Button variant="secondary" onClick={handleSnapshot} disabled={!run}>
              <Box size={16} />
              快照
            </Button>
            <Button variant="secondary" onClick={handleExport} disabled={!run}>
              <Download size={16} />
              导出 Run
            </Button>
            <Button variant="danger" onClick={handleStopRun} disabled={!run || run.status !== "Running"}>
              <Square size={16} />
              停止
            </Button>
          </div>
        </motion.header>

        <section className="grid gap-4 xl:grid-cols-[340px_minmax(520px,1fr)_380px]">
          <div className="flex flex-col gap-4">
            <Panel className="p-4">
              <PanelTitle icon={Map} title="选场景" subtitle="二维场地 / 机器人 / 动作集 / 任务流" />
              <div className="mt-4 grid gap-3">
                <select
                  className="h-9 rounded-lg border border-neutral-200 bg-white px-3 text-sm outline-none focus:border-neutral-400"
                  value={selectedScenarioId}
                  onChange={(event) => setSelectedScenarioId(event.currentTarget.value)}
                >
                  {scenarios.map((scenario) => (
                    <option key={scenario.scenarioId} value={scenario.scenarioId}>
                      {scenario.name}
                    </option>
                  ))}
                </select>
                <Button onClick={handleCreateRun}>
                  <Play size={15} />
                  创建并启动 Run
                </Button>
              </div>
              {selectedScenario && (
                <div className="mt-4 grid gap-2 text-xs text-neutral-500">
                  <InfoRow label="地图" value={`${selectedScenario.siteMapId} / ${selectedScenario.siteMapVersion}`} />
                  <InfoRow label="机器人" value={selectedScenario.robotCodes.join(", ") || "-"} />
                  <InfoRow label="动作集" value={(selectedScenario.actionSet.commands ?? []).join(", ")} />
                </div>
              )}
            </Panel>

            <Panel className="p-4">
              <PanelTitle icon={ClipboardList} title="建任务" subtitle="手动创建或从模板生成" />
              <div className="mt-4 grid gap-3">
                <input
                  className="h-9 rounded-lg border border-neutral-200 bg-white px-3 text-sm outline-none focus:border-neutral-400"
                  value={taskGoal}
                  onChange={(event) => setTaskGoal(event.currentTarget.value)}
                />
                <select
                  className="h-9 rounded-lg border border-neutral-200 bg-white px-3 text-sm outline-none focus:border-neutral-400"
                  value={templateId}
                  onChange={(event) => setTemplateId(event.currentTarget.value)}
                >
                  {templates.map((template) => (
                    <option key={template.templateId} value={template.templateId}>
                      {template.name}
                    </option>
                  ))}
                </select>
                <div className="grid grid-cols-2 gap-2">
                  <NumberInput label="目标 X" value={targetX} onChange={setTargetX} />
                  <NumberInput label="目标 Y" value={targetY} onChange={setTargetY} />
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <Button variant="secondary" disabled={!run} onClick={() => handleCreateTask(false)}>
                    手动任务
                  </Button>
                  <Button disabled={!run || !templateId} onClick={() => handleCreateTask(true)}>
                    模板生成
                  </Button>
                </div>
              </div>
              <div className="mt-4 space-y-2">
                {tasks.slice(0, 3).map((task) => (
                  <CompactRow key={task.taskId} label={task.goal} value={task.status} tone={statusTone(task.status)} />
                ))}
              </div>
            </Panel>

            <Panel className="p-4">
              <PanelTitle icon={Workflow} title="Plan" subtitle="版本与步骤" />
              <div className="mt-4 space-y-2">
                {activeTask?.activePlan?.steps.map((step) => (
                  <div key={step.planStepId} className="rounded-lg border border-neutral-200 bg-neutral-50 p-3">
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-medium">{step.actionType}</span>
                      <Badge tone="neutral">Step {step.sequence}</Badge>
                    </div>
                    <div className="mt-2 text-xs text-neutral-500">{step.successCondition}</div>
                  </div>
                )) ?? <EmptyState text="暂无任务计划" />}
              </div>
            </Panel>
          </div>

          <Panel className="min-h-[720px] overflow-hidden p-3">
            <div className="mb-3 flex items-center justify-between px-1">
              <PanelTitle icon={Route} title="二维 CurrentState" subtitle="只读场地、机器人、路径和异常" />
              <Badge tone="blue">stateVersion {currentState?.stateVersion ?? "-"}</Badge>
            </div>
            <ReadOnlyMap map={selectedScenario?.map} robots={robots} activeEvents={currentState?.activeEvents ?? []} />
          </Panel>

          <div className="flex flex-col gap-4">
            <Panel className="p-4">
              <PanelTitle icon={Send} title="发指令" subtitle="通过消息总成创建 Action" />
              <div className="mt-4 grid gap-3">
                <select
                  className="h-9 rounded-lg border border-neutral-200 bg-white px-3 text-sm outline-none focus:border-neutral-400"
                  value={command}
                  onChange={(event) => setCommand(event.currentTarget.value as "goto_pose" | "where" | "stop")}
                >
                  <option value="goto_pose">goto_pose</option>
                  <option value="where">where</option>
                  <option value="stop">stop</option>
                </select>
                <Button disabled={!run} onClick={handleSendAction}>
                  <Send size={15} />
                  下发 Action
                </Button>
              </div>
              <div className="mt-4 space-y-2">
                {actions.slice(0, 4).map((action) => (
                  <CompactRow
                    key={action.actionId}
                    label={action.commandId ?? action.actionId}
                    value={action.status}
                    tone={statusTone(action.status)}
                  />
                ))}
              </div>
            </Panel>

            <Panel className="p-4">
              <PanelTitle icon={Gauge} title="看状态" subtitle="机器人 / 任务 / 资源" />
              <div className="mt-4 space-y-3">
                {robots.map((robot) => (
                  <div key={robot.robotId} className="rounded-lg border border-neutral-200 bg-neutral-50 p-3">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2 text-sm font-medium">
                        <Bot size={15} />
                        {robot.robotId}
                      </div>
                      <Badge tone={statusTone(robot.state)}>{robot.state}</Badge>
                    </div>
                    <div className="mt-2 grid grid-cols-2 gap-2 text-xs text-neutral-500">
                      <span>X {Math.round(robot.x)}</span>
                      <span>Y {Math.round(robot.y)}</span>
                      <span>进度 {robot.progress}%</span>
                      <span>{robot.currentAction}</span>
                    </div>
                  </div>
                ))}
                <CompactRow
                  label="当前任务"
                  value={String(currentState?.taskState.status ?? "NoTask")}
                  tone={statusTone(String(currentState?.taskState.status ?? ""))}
                />
                <CompactRow
                  label="阻塞路径"
                  value={String((currentState?.resourceStates.blockedPaths as unknown[] | undefined)?.length ?? 0)}
                  tone={(currentState?.resourceStates.blockedPaths as unknown[] | undefined)?.length ? "amber" : "green"}
                />
              </div>
            </Panel>

            <Panel className="p-4">
              <PanelTitle icon={AlertTriangle} title="注入异常" subtitle="事件先进入 Observation" />
              <div className="mt-4 grid gap-3">
                <select
                  className="h-9 rounded-lg border border-neutral-200 bg-white px-3 text-sm outline-none focus:border-neutral-400"
                  value={exceptionType}
                  onChange={(event) => setExceptionType(event.currentTarget.value as typeof exceptionType)}
                >
                  {exceptionOptions.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
                <input
                  className="h-9 rounded-lg border border-neutral-200 bg-white px-3 text-sm outline-none focus:border-neutral-400"
                  value={exceptionTarget}
                  onChange={(event) => setExceptionTarget(event.currentTarget.value)}
                  placeholder="robot-001 / edge-2 / api"
                />
                <Button variant="danger" disabled={!run} onClick={handleInjectException}>
                  <Zap size={15} />
                  注入异常
                </Button>
              </div>
            </Panel>
          </div>
        </section>

        <Panel className="p-4">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <PanelTitle icon={MessageSquareText} title="看消息" subtitle="Command / Telemetry / Event / Ack / Alert" />
            <div className="flex flex-wrap gap-1.5">
              {messageFilters.map((filter) => (
                <Button
                  key={filter}
                  variant={messageFilter === filter ? "default" : "secondary"}
                  onClick={() => setMessageFilter(filter)}
                >
                  {filter}
                </Button>
              ))}
            </div>
          </div>
          <div className="grid gap-4 lg:grid-cols-[1.2fr_0.8fr_0.8fr]">
            <DiagnosticList
              title="Messages"
              items={filteredMessages.slice(0, 10).map((message) => ({
                id: message.messageId,
                label: String(message.payload.event ?? message.messageType),
                value: messageCategory(message),
                meta: message.topic
              }))}
            />
            <DiagnosticList
              title="Observations"
              items={observations.slice(0, 10).map((item) => ({
                id: item.observationId,
                label: item.event,
                value: item.category,
                meta: item.traceId ?? "-"
              }))}
            />
            <DiagnosticList
              title="Trace"
              items={(trace?.spans ?? []).slice(-10).map((span) => ({
                id: span.spanId,
                label: span.operation,
                value: span.status,
                meta: `${span.entityType}:${span.entityId}`
              }))}
            />
          </div>
        </Panel>
      </div>
    </main>
  );
}

function PanelTitle({ icon: Icon, title, subtitle }: { icon: typeof Radio; title: string; subtitle: string }) {
  return (
    <div className="flex items-center gap-3">
      <div className="grid h-9 w-9 place-items-center rounded-lg border border-neutral-200 bg-white">
        <Icon size={17} className="text-neutral-600" />
      </div>
      <div>
        <h2 className="text-sm font-semibold">{title}</h2>
        <p className="text-xs text-neutral-500">{subtitle}</p>
      </div>
    </div>
  );
}

function ReadOnlyMap({
  map,
  robots,
  activeEvents
}: {
  map?: SiteMap;
  robots: RobotState[];
  activeEvents: Array<Record<string, unknown>>;
}) {
  if (!map) {
    return <EmptyState text="暂无场景地图" />;
  }
  const gridLines = [];
  for (let x = 0; x <= map.width; x += map.gridSize) {
    gridLines.push(<line key={`x-${x}`} x1={x} x2={x} y1={0} y2={map.height} />);
  }
  for (let y = 0; y <= map.height; y += map.gridSize) {
    gridLines.push(<line key={`y-${y}`} x1={0} x2={map.width} y1={y} y2={y} />);
  }
  return (
    <div className="relative h-full min-h-[660px] overflow-hidden rounded-lg border border-neutral-200 bg-white">
      <svg viewBox={`0 0 ${map.width} ${map.height}`} className="h-full min-h-[660px] w-full">
        <rect width={map.width} height={map.height} fill="#fff" />
        <g stroke="#edf0f3" strokeWidth="0.8">
          {gridLines}
        </g>
        <g stroke="#a3a3a3" strokeWidth="2">
          <line x1={0} y1={0} x2={map.width} y2={0} />
          <line x1={0} y1={0} x2={0} y2={map.height} />
        </g>
        <g stroke="#111827" strokeOpacity="0.32" strokeWidth="3">
          {map.pathEdges.map((edge) => {
            const from = map.objects.find((item) => item.id === edge.from);
            const to = map.objects.find((item) => item.id === edge.to);
            if (!from || !to) {
              return null;
            }
            return <line key={edge.id} x1={from.x} y1={from.y} x2={to.x} y2={to.y} />;
          })}
        </g>
        {map.objects.map((item) => (
          <ReadOnlyShape key={item.id} item={item} />
        ))}
        {robots.map((robot) => (
          <motion.g
            key={robot.robotId}
            animate={{ x: robot.x, y: robot.y }}
            transition={{ type: "spring", stiffness: 90, damping: 18 }}
          >
            <circle r={17} fill={robot.state === "Offline" ? "#ef4444" : "#111827"} />
            <circle r={5} fill={robot.state === "Error" ? "#f59e0b" : "#34d399"} />
            <text x={24} y={5} fill="#111827" fontSize="14" fontWeight="600">
              {robot.robotId}
            </text>
          </motion.g>
        ))}
      </svg>
      <div className="absolute bottom-3 left-3 flex flex-wrap gap-2 rounded-lg border border-neutral-200 bg-white/90 px-3 py-2 text-xs text-neutral-600 shadow-sm backdrop-blur">
        <span>{map.name}</span>
        <span>{map.width} x {map.height} {map.unit}</span>
        <span>异常 {activeEvents.length}</span>
      </div>
    </div>
  );
}

function ReadOnlyShape({ item }: { item: MapObject }) {
  const stroke = item.type === "obstacle" ? "#737373" : item.type === "station" ? "#16a34a" : "#2563eb";
  if (item.type === "zone" || item.type === "obstacle") {
    return (
      <g>
        <rect
          x={item.x - (item.width ?? 100) / 2}
          y={item.y - (item.height ?? 80) / 2}
          width={item.width ?? 100}
          height={item.height ?? 80}
          rx={6}
          fill={item.color}
          stroke={stroke}
          strokeWidth={2}
          opacity={item.type === "zone" ? 0.55 : 0.9}
        />
        <text x={item.x + 8} y={item.y - 8} fill="#111827" fontSize="13">
          {item.name}
        </text>
      </g>
    );
  }
  if (item.type === "resourcePoint") {
    const size = item.width ?? 34;
    const points = `${item.x},${item.y - size / 2} ${item.x + size / 2},${item.y} ${item.x},${item.y + size / 2} ${item.x - size / 2},${item.y}`;
    return (
      <g>
        <polygon points={points} fill={item.color} stroke="#d97706" strokeWidth={2} />
        <text x={item.x + 14} y={item.y + 5} fill="#111827" fontSize="13">
          {item.name}
        </text>
      </g>
    );
  }
  return (
    <g>
      <circle cx={item.x} cy={item.y} r={item.radius ?? (item.type === "station" ? 20 : 7)} fill={item.color} stroke={stroke} strokeWidth={2} />
      <text x={item.x + 14} y={item.y + 5} fill="#111827" fontSize="13">
        {item.name}
      </text>
    </g>
  );
}

function DiagnosticList({
  title,
  items
}: {
  title: string;
  items: Array<{ id: string; label: string; value: string; meta: string }>;
}) {
  return (
    <div>
      <div className="mb-2 text-sm font-semibold">{title}</div>
      <div className="max-h-[300px] space-y-2 overflow-auto pr-1">
        {items.length === 0 ? (
          <EmptyState text="暂无数据" />
        ) : (
          items.map((item) => (
            <div key={item.id} className="rounded-lg border border-neutral-200 bg-neutral-50 p-3">
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-sm font-medium">{item.label}</span>
                <Badge tone={statusTone(item.value)}>{item.value}</Badge>
              </div>
              <div className="mt-1 truncate font-mono text-[11px] text-neutral-500">{item.meta}</div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function CompactRow({ label, value, tone }: { label: string; value: string; tone: "neutral" | "blue" | "green" | "amber" | "red" }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-lg border border-neutral-200 bg-neutral-50 px-3 py-2">
      <span className="truncate text-sm text-neutral-700">{label}</span>
      <Badge tone={tone}>{value}</Badge>
    </div>
  );
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-3 rounded-lg border border-neutral-200 bg-neutral-50 px-3 py-2">
      <span>{label}</span>
      <span className="truncate text-right text-neutral-900">{value}</span>
    </div>
  );
}

function NumberInput({ label, value, onChange }: { label: string; value: number; onChange: (value: number) => void }) {
  return (
    <label className="block text-xs font-medium text-neutral-500">
      {label}
      <input
        className="mt-1 h-9 w-full rounded-lg border border-neutral-200 px-3 text-sm tabular text-neutral-950 outline-none focus:border-neutral-400"
        type="number"
        value={value}
        onChange={(event) => onChange(Number(event.currentTarget.value))}
      />
    </label>
  );
}

function EmptyState({ text }: { text: string }) {
  return <div className="rounded-lg border border-dashed border-neutral-200 p-4 text-sm text-neutral-500">{text}</div>;
}

function robotsFromState(state: CurrentState | null, scenario?: ScenarioSummary): RobotState[] {
  const robots = state?.robotStates ?? [];
  if (robots.length > 0) {
    return robots.map((robot) => ({
      robotId: String(robot.robotId ?? robot.robotCode ?? "robot-001"),
      robotType: String(robot.robotType ?? "machine-dog"),
      state: String(robot.state ?? "Idle"),
      x: Number(robot.x ?? 0),
      y: Number(robot.y ?? 0),
      progress: Number(robot.progress ?? 0),
      currentAction: String(robot.currentAction ?? "Waiting"),
      updatedAt: String(robot.updatedAt ?? new Date().toISOString())
    }));
  }
  return (scenario?.robotCodes ?? ["robot-001"]).map((robotId) => ({
    robotId,
    robotType: "machine-dog",
    state: "Idle",
    x: 220,
    y: 360,
    progress: 0,
    currentAction: "Waiting",
    updatedAt: new Date().toISOString()
  }));
}

function messageCategory(message: MessageRecord) {
  if (message.messageType === "command") {
    return "Command";
  }
  const event = String(message.payload.event ?? message.messageType);
  if (["command.accepted", "command.rejected"].includes(event)) {
    return "Ack";
  }
  if (["pose.updated", "where.result"].includes(event)) {
    return "Telemetry";
  }
  if (
    [
      "task.failed",
      "task.timeout",
      "where.failed",
      "device.offline",
      "robot.offline",
      "action.failed",
      "path.blocked",
      "interface.timeout",
      "message.dropped",
      "resource.locked",
      "station.unavailable",
      "battery.low"
    ].includes(event)
  ) {
    return "Alert";
  }
  return "Event";
}

function statusTone(value: string): "neutral" | "blue" | "green" | "amber" | "red" {
  if (["Succeeded", "Running", "Accepted", "Issued", "Moving", "ok", "Event", "Telemetry"].includes(value)) {
    return "green";
  }
  if (["Alert", "Failed", "Rejected", "Timeout", "Error", "Offline", "critical"].includes(value)) {
    return "red";
  }
  if (["Paused", "Draft", "Ready", "Pending", "Ack"].includes(value)) {
    return "amber";
  }
  if (["Command"].includes(value)) {
    return "blue";
  }
  return "neutral";
}
