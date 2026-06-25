import { motion } from "framer-motion";
import {
  AlertTriangle,
  ArrowLeft,
  Bot,
  Box,
  CheckCircle2,
  ClipboardList,
  Copy,
  Download,
  Eye,
  FileDown,
  Gauge,
  ListChecks,
  Map,
  MessageSquareText,
  Pause,
  Play,
  Plus,
  Radio,
  RefreshCw,
  RotateCcw,
  Route,
  Send,
  SlidersHorizontal,
  Square,
  Workflow,
  Zap
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Badge, Button, Panel } from "./components/ui";
import {
  createRobotConfig,
  createSimulationAction,
  createSimulationRun,
  createSimulationSnapshot,
  createSimulationTask,
  createSimulationTasksBatch,
  createSimulationTaskFromTemplate,
  exportSimulationRun,
  getActionCommandSpecs,
  getActionTrace,
  getCurrentState,
  getExecutors,
  getRobotConfigs,
  getRunMessages,
  getRunMessageMetrics,
  getRunObservations,
  getScenarios,
  getSimulationActions,
  getSimulationRuns,
  getSimulationSocketUrl,
  getSimulationTasks,
  getTaskTrace,
  getTaskTemplates,
  getTargets,
  getTrace,
  getTraceGraph,
  injectSimulationEvent,
  pauseSimulationRun,
  recoverSimulationEvent,
  replayRunMessage,
  resumeSimulationRun,
  startSimulationRun,
  stopSimulationRun,
  validateScenario
} from "./lib/api";
import type {
  ActionCommandSpec,
  CurrentState,
  ExecutorInstance,
  MapObject,
  MessageRecord,
  Observation,
  RobotConfig,
  RobotState,
  RunMessageMetrics,
  ScenarioSummary,
  ScenarioValidationResponse,
  SimulationAction,
  SimulationRun,
  SimulationSnapshot,
  SimulationTask,
  SiteMap,
  TaskTemplate,
  TargetRegistryItem,
  TraceGraph,
  TraceResponse
} from "./lib/types";

const WORKSPACE_ID = "00000000-0000-0000-0000-000000000001";

const exceptionOptions = [
  { value: "robot.offline", label: "机器人离线", targetType: "robot" },
  { value: "action.failed", label: "动作失败", targetType: "robot" },
  { value: "path.blocked", label: "路径阻塞", targetType: "path" },
  { value: "interface.timeout", label: "接口超时", targetType: "interface" },
  { value: "message.dropped", label: "消息丢失", targetType: "message" },
  { value: "station.unavailable", label: "工位不可用", targetType: "station" },
  { value: "resource.locked", label: "资源锁定", targetType: "resource" }
] as const;

const messageFilters = ["All", "Command", "Ack", "Telemetry", "Event", "Alert", "Interface", "AgentDecision"] as const;
const sandboxReplayEnabled = false;

const recoveryModes = [
  { value: "manual", label: "手动恢复" },
  { value: "retry", label: "重试恢复" },
  { value: "reschedule", label: "重新调度" },
  { value: "takeover", label: "人工接管" },
  { value: "terminate_task", label: "终止任务" }
] as const;

export function SimulationDashboard() {
  const [scenarios, setScenarios] = useState<ScenarioSummary[]>([]);
  const [templates, setTemplates] = useState<TaskTemplate[]>([]);
  const [actionSpecs, setActionSpecs] = useState<ActionCommandSpec[]>([]);
  const [targets, setTargets] = useState<TargetRegistryItem[]>([]);
  const [robotConfigs, setRobotConfigs] = useState<RobotConfig[]>([]);
  const [executors, setExecutors] = useState<ExecutorInstance[]>([]);
  const [runs, setRuns] = useState<SimulationRun[]>([]);
  const [run, setRun] = useState<SimulationRun | null>(null);
  const [tasks, setTasks] = useState<SimulationTask[]>([]);
  const [actions, setActions] = useState<SimulationAction[]>([]);
  const [currentState, setCurrentState] = useState<CurrentState | null>(null);
  const [messages, setMessages] = useState<MessageRecord[]>([]);
  const [observations, setObservations] = useState<Observation[]>([]);
  const [trace, setTrace] = useState<TraceResponse | null>(null);
  const [traceGraph, setTraceGraph] = useState<TraceGraph | null>(null);
  const [messageMetrics, setMessageMetrics] = useState<RunMessageMetrics | null>(null);
  const [scenarioValidation, setScenarioValidation] = useState<ScenarioValidationResponse | null>(null);
  const [socketState, setSocketState] = useState<"idle" | "open" | "fallback">("idle");
  const [selectedTaskId, setSelectedTaskId] = useState("");
  const [selectedActionId, setSelectedActionId] = useState("");
  const [selectedPlanStepId, setSelectedPlanStepId] = useState("");
  const [selectedScenarioId, setSelectedScenarioId] = useState("default-site-a");
  const [templateId, setTemplateId] = useState("");
  const [taskMode, setTaskMode] = useState<"manual" | "template" | "batch">("manual");
  const [taskGoal, setTaskGoal] = useState("搬运到分拣工位");
  const [batchCount, setBatchCount] = useState(5);
  const [commandMode, setCommandMode] = useState<"quick" | "advanced">("quick");
  const [command, setCommand] = useState("goto_pose");
  const [actionParams, setActionParams] = useState<Record<string, string | number>>({});
  const [selectedRobotCode, setSelectedRobotCode] = useState("");
  const [targetX, setTargetX] = useState(760);
  const [targetY, setTargetY] = useState(420);
  const [targetZ, setTargetZ] = useState(0);
  const [targetYaw, setTargetYaw] = useState(0);
  const [timeoutMs, setTimeoutMs] = useState(60000);
  const [messageFilter, setMessageFilter] = useState<(typeof messageFilters)[number]>("All");
  const [selectedMessageId, setSelectedMessageId] = useState("");
  const [exceptionType, setExceptionType] = useState<(typeof exceptionOptions)[number]["value"]>("robot.offline");
  const [exceptionTarget, setExceptionTarget] = useState("robot-001");
  const [exceptionDurationMs, setExceptionDurationMs] = useState(0);
  const [exceptionAutoRecover, setExceptionAutoRecover] = useState(false);
  const [recoveryMode, setRecoveryMode] = useState<(typeof recoveryModes)[number]["value"]>("manual");
  const [newRobotCode, setNewRobotCode] = useState("");
  const [newRobotType, setNewRobotType] = useState("machine-dog");
  const [newRobotX, setNewRobotX] = useState(520);
  const [newRobotY, setNewRobotY] = useState(360);
  const [newRobotMode, setNewRobotMode] = useState<"config_only" | "start_virtual_executor" | "bind_real_gateway">("config_only");
  const [newRobotGateway, setNewRobotGateway] = useState("");
  const [status, setStatus] = useState("仿真驾驶舱待连接");

  const selectedScenario = useMemo(
    () => scenarios.find((item) => item.scenarioId === selectedScenarioId) ?? scenarios[0],
    [scenarios, selectedScenarioId]
  );

  const activeTask = tasks.find((task) => task.taskId === selectedTaskId) ?? tasks[0] ?? null;
  const taskActions = useMemo(
    () => actions.filter((action) => !activeTask?.taskId || action.taskId === activeTask.taskId),
    [actions, activeTask?.taskId]
  );
  const selectedAction = actions.find((action) => action.actionId === selectedActionId) ?? taskActions[0] ?? actions[0] ?? null;
  const selectedCommandSpec = actionSpecs.find((spec) => spec.command === command) ?? actionSpecs[0] ?? null;
  const currentPlanStep = activeTask?.activePlan?.steps.find((step) => step.planStepId === selectedPlanStepId)
    ?? activeTask?.activePlan?.steps.find((step) => step.planStepId === selectedAction?.planStepId)
    ?? activeTask?.activePlan?.steps[0]
    ?? null;
  const robots = useMemo(() => robotsFromState(currentState, selectedScenario), [currentState, selectedScenario]);
  const availableRobotCodes = useMemo(() => {
    const scenarioRobotCodes = selectedScenario?.robotCodes ?? [];
    const robotCodes = scenarioRobotCodes.length ? scenarioRobotCodes : robots.map((robot) => robot.robotId);
    return Array.from(new Set(robotCodes.length ? robotCodes : ["robot-001"]));
  }, [robots, selectedScenario]);
  const effectiveRobotCode = selectedRobotCode || availableRobotCodes[0] || robots[0]?.robotId || "robot-001";
  const filteredMessages = useMemo(() => {
    const categoryMessages =
      messageFilter === "All"
        ? messages
        : messages.filter((message) => messageCategory(message) === messageFilter);
    if (!effectiveRobotCode) {
      return categoryMessages;
    }
    return categoryMessages.filter((message) => {
      const payloadRobot = String(message.payload.robotCode ?? message.payload.robotId ?? "");
      const topic = String(message.topic ?? "");
      return payloadRobot === effectiveRobotCode || topic.includes(`/${effectiveRobotCode}/`);
    });
  }, [effectiveRobotCode, messageFilter, messages]);
  const selectedMessage = useMemo(
    () => messages.find((message) => message.messageId === selectedMessageId) ?? filteredMessages[0] ?? null,
    [filteredMessages, messages, selectedMessageId]
  );
  const selectedRobot = robots.find((robot) => robot.robotId === effectiveRobotCode) ?? robots[0] ?? null;
  const selectedExceptionOption = exceptionOptions.find((item) => item.value === exceptionType) ?? exceptionOptions[0];
  const visibleActionQueue = (taskActions.length ? taskActions : actions).filter(
    (action) => !effectiveRobotCode || action.robotCode === effectiveRobotCode
  );
  const visibleObservations = observations.filter((observation) => !effectiveRobotCode || observation.robotCode === effectiveRobotCode);
  const targetOptions = targets.filter((target) => target.status === "active");
  const suggestedRobotCode = useMemo(() => nextRobotCode(availableRobotCodes), [availableRobotCodes]);
  const scenarioCheckSummary = useMemo(() => {
    const checks = scenarioValidation?.checks ?? [];
    return {
      passed: checks.filter((check) => check.status === "passed").length,
      warnings: checks.filter((check) => check.status === "warning").length,
      failed: checks.filter((check) => check.status === "failed").length
    };
  }, [scenarioValidation]);

  async function bootstrap() {
    const [nextScenarios, nextTemplates, nextSpecs, nextRuns, nextTargets, nextRobotConfigs, nextExecutors] = await Promise.all([
      getScenarios(),
      getTaskTemplates(),
      getActionCommandSpecs(),
      getSimulationRuns(),
      getTargets(),
      getRobotConfigs(),
      getExecutors()
    ]);
    setScenarios(nextScenarios);
    setTemplates(nextTemplates);
    setActionSpecs(nextSpecs);
    setTargets(nextTargets);
    setRobotConfigs(nextRobotConfigs);
    setExecutors(nextExecutors);
    setActionParams(defaultActionParams(nextSpecs.find((spec) => spec.command === "goto_pose") ?? nextSpecs[0]));
    setCommand(nextSpecs.find((spec) => spec.command === "goto_pose")?.command ?? nextSpecs[0]?.command ?? "goto_pose");
    setRuns(nextRuns);
    setSelectedScenarioId(nextScenarios[0]?.scenarioId ?? "default-site-a");
    setSelectedRobotCode(nextScenarios[0]?.robotCodes[0] ?? "");
    setTemplateId(nextTemplates[0]?.templateId ?? "");
    if (nextScenarios[0]) {
      await handleValidateScenario(nextScenarios[0].scenarioId, false);
    }
    if (nextRuns[0]) {
      setRun(nextRuns[0]);
      await refreshRun(nextRuns[0].runId);
    }
    setStatus("已连接平台 API");
  }

  async function handleValidateScenario(scenarioId = selectedScenarioId, announce = true) {
    try {
      const validation = await validateScenario(scenarioId);
      setScenarioValidation(validation);
      if (announce) {
        setStatus(validation.ok ? "场景完整性校验通过" : `场景校验存在 ${validation.issues.length} 个阻断问题`);
      }
      return validation;
    } catch {
      setScenarioValidation(null);
      if (announce) {
        setStatus("场景校验接口暂不可用");
      }
      return null;
    }
  }

  async function refreshRun(runId: string) {
    const [nextTasks, nextActions, nextState, nextMessages, nextObservations, nextMetrics, nextTargets, nextRobotConfigs, nextExecutors] = await Promise.all([
      getSimulationTasks(runId),
      getSimulationActions(runId),
      getCurrentState(runId),
      getRunMessages(runId),
      getRunObservations(runId),
      getRunMessageMetrics(runId),
      getTargets(),
      getRobotConfigs(),
      getExecutors()
    ]);
    setTasks(nextTasks);
    setActions(nextActions);
    setCurrentState(nextState);
    setMessages(nextMessages);
    setObservations(nextObservations);
    setMessageMetrics(nextMetrics);
    setTargets(nextTargets);
    setRobotConfigs(nextRobotConfigs);
    setExecutors(nextExecutors);
    const nextTask = nextTasks.find((task) => task.taskId === selectedTaskId) ?? nextTasks[0] ?? null;
    const nextAction = nextActions.find((action) => action.actionId === selectedActionId)
      ?? nextActions.find((action) => action.taskId === nextTask?.taskId)
      ?? nextActions[0]
      ?? null;
    if (nextTask && !selectedTaskId) {
      setSelectedTaskId(nextTask.taskId);
    }
    if (nextAction && !selectedActionId) {
      setSelectedActionId(nextAction.actionId);
    }
    const traceId = nextAction?.traceId ?? nextTask?.traceId;
    if (traceId) {
      const [nextTrace, nextGraph] = await Promise.all([getTrace(traceId), getTraceGraph(traceId)]);
      setTrace(nextTrace);
      setTraceGraph(nextGraph);
    }
  }

  useEffect(() => {
    void bootstrap().catch((error) => setStatus(error instanceof Error ? error.message : "平台连接失败"));
  }, []);

  useEffect(() => {
    if (!selectedScenarioId) {
      return;
    }
    const scenario = scenarios.find((item) => item.scenarioId === selectedScenarioId);
    setSelectedRobotCode(scenario?.robotCodes[0] ?? "");
    void handleValidateScenario(selectedScenarioId, false);
  }, [selectedScenarioId, scenarios]);

  useEffect(() => {
    if (!tasks.length) {
      setSelectedTaskId("");
      return;
    }
    if (!tasks.some((task) => task.taskId === selectedTaskId)) {
      setSelectedTaskId(tasks[0].taskId);
    }
  }, [tasks, selectedTaskId]);

  useEffect(() => {
    if (!actions.length) {
      setSelectedActionId("");
      return;
    }
    if (!actions.some((action) => action.actionId === selectedActionId)) {
      const nextAction = taskActions[0] ?? actions[0];
      setSelectedActionId(nextAction.actionId);
    }
  }, [actions, selectedActionId, taskActions]);

  useEffect(() => {
    if (selectedAction?.planStepId) {
      setSelectedPlanStepId(selectedAction.planStepId);
      return;
    }
    setSelectedPlanStepId(activeTask?.activePlan?.steps[0]?.planStepId ?? "");
  }, [activeTask?.activePlan?.steps, selectedAction?.planStepId]);

  useEffect(() => {
    setActionParams(defaultActionParams(selectedCommandSpec));
  }, [selectedCommandSpec?.command]);

  useEffect(() => {
    if (!newRobotCode) {
      setNewRobotCode(suggestedRobotCode);
    }
  }, [newRobotCode, suggestedRobotCode]);

  useEffect(() => {
    if (selectedExceptionOption.targetType === "robot" && effectiveRobotCode) {
      setExceptionTarget(effectiveRobotCode);
    }
  }, [effectiveRobotCode, selectedExceptionOption.targetType]);

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
    const validation = await handleValidateScenario(selectedScenarioId, false);
    if (validation && !validation.ok) {
      setStatus(`场景校验未通过：${validation.issues[0]}`);
      return;
    }
    const nextRun = await createSimulationRun(selectedScenarioId, `${selectedScenario?.name ?? "Scenario"} Run`);
    const started = await startSimulationRun(nextRun.runId);
    setRun(started);
    setRuns([started, ...runs]);
    setStatus(`已创建运行 ${started.runId}`);
    await refreshRun(started.runId);
  }

  async function handleCreateRobot() {
    const robotCode = newRobotCode.trim();
    const robotType = newRobotType.trim() || "machine-dog";
    if (!robotCode) {
      setStatus("请输入机器人编号");
      return;
    }
    try {
      const config = await createRobotConfig({
        robotCode,
        robotName: robotCode,
        robotType,
        initialPose: { x: newRobotX, y: newRobotY, z: 0, yaw: 0 },
        createMode: newRobotMode,
        executorEndpoint: newRobotMode === "bind_real_gateway" ? newRobotGateway.trim() || null : null
      });
      const [nextScenarios, nextConfigs, nextExecutors] = await Promise.all([getScenarios(), getRobotConfigs(), getExecutors()]);
      setScenarios(nextScenarios);
      setRobotConfigs(nextConfigs);
      setExecutors(nextExecutors);
      setSelectedRobotCode(config.robotCode);
      setExceptionTarget(config.robotCode);
      setNewRobotCode(nextRobotCode([...availableRobotCodes, config.robotCode]));
      setNewRobotX((current) => current + 100);
      setStatus(`已添加机器人配置 ${config.robotCode}`);
      await handleValidateScenario(selectedScenarioId, false);
      if (run) {
        await refreshRun(run.runId);
      }
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "新增机器人失败");
    }
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

  async function handlePauseRun() {
    if (!run) {
      return;
    }
    const paused = await pauseSimulationRun(run.runId);
    setRun(paused);
    setStatus(`已暂停运行 ${paused.runId}`);
    await refreshRun(paused.runId);
  }

  async function handleResumeRun() {
    if (!run) {
      return;
    }
    const resumed = await resumeSimulationRun(run.runId);
    setRun(resumed);
    setStatus(`已恢复运行 ${resumed.runId}`);
    await refreshRun(resumed.runId);
  }

  async function handleCreateTask(useTemplate: boolean) {
    if (!run) {
      return;
    }
    const nextTask = useTemplate
      ? await createSimulationTaskFromTemplate(run.runId, templateId, {
          goal: taskGoal,
          target: commandTargetParams(targetX, targetY, targetZ, targetYaw)
        })
      : await createSimulationTask(run.runId, {
          goal: taskGoal,
          input: { command: "goto_pose", target: commandTargetParams(targetX, targetY, targetZ, targetYaw) },
          priority: 5
        });
    setStatus(`已创建任务 ${nextTask.taskId}`);
    await refreshRun(run.runId);
  }

  async function handleCreateBatchTasks() {
    if (!run) {
      return;
    }
    const response = await createSimulationTasksBatch(run.runId, {
      templateId: templateId || null,
      goal: taskGoal,
      count: batchCount,
      intervalMs: 0,
      priority: 5,
      targetRange: { x: [Math.max(0, targetX - 80), targetX + 80], y: [Math.max(0, targetY - 80), targetY + 80] },
      parameters: {
        target: commandTargetParams(targetX, targetY, targetZ, targetYaw)
      },
      randomSeed: Date.now(),
      randomizeRobot: true,
      randomizeTaskType: false,
      autoRun: false
    });
    setStatus(`批量任务已创建 ${response.createdCount}/${response.requestedCount}，批次 ${response.batchId}`);
    await refreshRun(run.runId);
  }

  async function handleSendAction() {
    if (!run) {
      return;
    }
    const params = normalizedActionParams(actionParams, selectedCommandSpec);
    const nextAction = await createSimulationAction({
      runId: run.runId,
      taskId: activeTask?.taskId,
      planId: activeTask?.activePlan?.planId,
      planStepId: currentPlanStep?.planStepId,
      robotCode: effectiveRobotCode,
      command,
      params,
      timeoutMs,
      operatorId: "simulation-console"
    });
    setSelectedActionId(nextAction.actionId);
    setSelectedPlanStepId(nextAction.planStepId ?? currentPlanStep?.planStepId ?? "");
    setStatus(`已下发 Action ${nextAction.actionId}`);
    await refreshRun(run.runId);
  }

  async function handleQuickAction(nextCommand: "goto_pose" | "where" | "stop", target?: { x: number; y: number }) {
    if (target) {
      setTargetX(Math.round(target.x));
      setTargetY(Math.round(target.y));
    }
    setCommand(nextCommand);
    setActionParams(defaultActionParams(actionSpecs.find((spec) => spec.command === nextCommand) ?? null));
    if (!run) {
      return;
    }
    const params =
      nextCommand === "goto_pose"
        ? {
            ...defaultActionParams(actionSpecs.find((spec) => spec.command === nextCommand) ?? null),
            ...commandTargetParams(target?.x ?? targetX, target?.y ?? targetY, targetZ, targetYaw)
          }
        : defaultActionParams(actionSpecs.find((spec) => spec.command === nextCommand) ?? null);
    const nextAction = await createSimulationAction({
      runId: run.runId,
      taskId: activeTask?.taskId,
      planId: activeTask?.activePlan?.planId,
      planStepId: currentPlanStep?.planStepId,
      robotCode: effectiveRobotCode,
      command: nextCommand,
      params,
      timeoutMs,
      operatorId: "simulation-console"
    });
    setSelectedActionId(nextAction.actionId);
    setSelectedPlanStepId(nextAction.planStepId ?? currentPlanStep?.planStepId ?? "");
    setStatus(`快捷指令已下发 ${nextAction.command}`);
    await refreshRun(run.runId);
  }

  async function handleSelectTask(taskId: string) {
    setSelectedTaskId(taskId);
    const task = tasks.find((item) => item.taskId === taskId);
    setSelectedPlanStepId(task?.activePlan?.steps[0]?.planStepId ?? "");
    if (!task) {
      return;
    }
    const [nextTrace, nextGraph] = await Promise.all([getTaskTrace(task.taskId), getTraceGraph(task.traceId)]);
    setTrace(nextTrace);
    setTraceGraph(nextGraph);
    setStatus(`Selected task ${task.taskId}`);
  }

  async function handleSelectAction(actionId: string) {
    setSelectedActionId(actionId);
    const action = actions.find((item) => item.actionId === actionId);
    if (!action) {
      return;
    }
    setSelectedPlanStepId(action.planStepId ?? "");
    const relatedMessage = messages.find((message) => message.payload.commandId === action.commandId || message.messageId === action.commandId);
    if (relatedMessage) {
      setSelectedMessageId(relatedMessage.messageId);
    }
    const [nextTrace, nextGraph] = await Promise.all([getActionTrace(action.actionId), getTraceGraph(action.traceId)]);
    setTrace(nextTrace);
    setTraceGraph(nextGraph);
    setStatus(`Selected action ${action.actionId}`);
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
      durationMs: exceptionDurationMs || null,
      autoRecover: exceptionAutoRecover
    });
    setStatus(`已注入异常 ${option.label}`);
    await refreshRun(run.runId);
  }

  async function handleRecoverException() {
    if (!run) {
      return;
    }
    const option = exceptionOptions.find((item) => item.value === exceptionType) ?? exceptionOptions[0];
    await recoverSimulationEvent(run.runId, {
      eventType: option.value,
      targetType: option.targetType,
      targetId: exceptionTarget,
      recoveryMode,
      reason: "operator recovery",
      operatorId: "simulation-console"
    });
    setStatus(`已执行异常恢复 ${option.label}`);
    await refreshRun(run.runId);
  }

  async function handleReplayMessage() {
    if (!run || !selectedMessage) {
      return;
    }
    const response = await replayRunMessage(run.runId, selectedMessage.messageId, {
      replayMode: "single",
      sandbox: true,
      reason: "simulation cockpit replay"
    });
    setSelectedMessageId(response.message.messageId);
    setStatus(`已沙箱重放消息 ${selectedMessage.messageId}`);
    await refreshRun(run.runId);
  }

  function handleExportMessages() {
    const payload = {
      exportType: "simulation_messages_filtered",
      runId: run?.runId,
      filter: messageFilter,
      createdAt: new Date().toISOString(),
      messages: filteredMessages
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${run?.runId ?? "simulation"}-${messageFilter.toLowerCase()}-messages.json`;
    link.click();
    URL.revokeObjectURL(url);
  }

  async function handleCopyMessagePayload() {
    if (!selectedMessage) {
      return;
    }
    await navigator.clipboard.writeText(JSON.stringify(selectedMessage.payload, null, 2));
    setStatus(`已复制消息 ${selectedMessage.messageId}`);
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
            <Button variant="secondary" onClick={handlePauseRun} disabled={!run || run.status !== "Running"}>
              <Pause size={16} />
              暂停
            </Button>
            <Button variant="secondary" onClick={handleResumeRun} disabled={!run || run.status !== "Paused"}>
              <RefreshCw size={16} />
              恢复
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
                <Button variant="secondary" onClick={() => void handleValidateScenario()}>
                  <CheckCircle2 size={15} />
                  校验场景
                </Button>
              </div>
              <div className="mt-4 rounded-lg border border-neutral-200 bg-neutral-50 p-3">
                <div className="mb-3 flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2 text-sm font-medium text-neutral-800">
                    <Bot size={15} />
                    新增机器人
                  </div>
                  <Badge tone="neutral">{suggestedRobotCode}</Badge>
                </div>
                <div className="grid gap-2">
                  <input
                    className="h-9 rounded-lg border border-neutral-200 bg-white px-3 text-sm outline-none focus:border-neutral-400"
                    value={newRobotCode}
                    onChange={(event) => setNewRobotCode(event.currentTarget.value)}
                    placeholder="robot-004"
                  />
                  <input
                    className="h-9 rounded-lg border border-neutral-200 bg-white px-3 text-sm outline-none focus:border-neutral-400"
                    value={newRobotType}
                    onChange={(event) => setNewRobotType(event.currentTarget.value)}
                    placeholder="machine-dog"
                  />
                  <div className="grid grid-cols-2 gap-2">
                    <NumberInput label="初始 X" value={newRobotX} onChange={setNewRobotX} />
                    <NumberInput label="初始 Y" value={newRobotY} onChange={setNewRobotY} />
                  </div>
                  <select
                    className="h-9 rounded-lg border border-neutral-200 bg-white px-3 text-sm outline-none focus:border-neutral-400"
                    value={newRobotMode}
                    onChange={(event) => setNewRobotMode(event.currentTarget.value as typeof newRobotMode)}
                  >
                    <option value="config_only">仅登记配置</option>
                    <option value="start_virtual_executor">同步启动虚拟执行体</option>
                    <option value="bind_real_gateway">绑定真实机器人网关</option>
                  </select>
                  {newRobotMode === "bind_real_gateway" && (
                    <input
                      className="h-9 rounded-lg border border-neutral-200 bg-white px-3 text-sm outline-none focus:border-neutral-400"
                      value={newRobotGateway}
                      onChange={(event) => setNewRobotGateway(event.currentTarget.value)}
                      placeholder="mqtt-gateway://robot-004"
                    />
                  )}
                  <Button variant="secondary" onClick={() => void handleCreateRobot()}>
                    <Plus size={15} />
                    添加机器人
                  </Button>
                </div>
              </div>
              <div className="mt-4 grid gap-2">
                <div className="flex items-center justify-between text-xs text-neutral-500">
                  <span>执行体</span>
                  <span>{executors.filter((executor) => executor.status === "active").length} active / {executors.length}</span>
                </div>
                <CompactRow label="机器人配置" value={`${robotConfigs.filter((robot) => robot.enabled).length} enabled / ${robotConfigs.length}`} tone="blue" />
                {executors.slice(0, 4).map((executor) => (
                  <CompactRow
                    key={executor.executorId}
                    label={executor.robotCode}
                    value={`${executor.executorType} / ${executor.status}`}
                    tone={executor.status === "active" ? "green" : executor.status === "error" ? "red" : "amber"}
                  />
                ))}
              </div>
              {selectedScenario && (
                <div className="mt-4 grid gap-2 text-xs text-neutral-500">
                  <InfoRow label="地图" value={`${selectedScenario.siteMapId} / ${selectedScenario.siteMapVersion}`} />
                  <InfoRow label="机器人数量" value={String(selectedScenario.robotCodes.length)} />
                  <InfoRow label="机器人" value={selectedScenario.robotCodes.join(", ") || "-"} />
                  <InfoRow label="动作集" value={(selectedScenario.actionSet.commands ?? []).join(", ")} />
                  <InfoRow
                    label="完整性"
                    value={
                      scenarioValidation
                        ? `${scenarioValidation.ok ? "通过" : "阻断"} / P${scenarioCheckSummary.passed} W${scenarioCheckSummary.warnings} F${scenarioCheckSummary.failed}`
                        : "未校验"
                    }
                  />
                </div>
              )}
              {scenarioValidation && (
                <div className="mt-3 grid gap-2">
                  {scenarioValidation.checks.slice(0, 4).map((check) => (
                    <CompactRow
                      key={check.code}
                      label={check.label}
                      value={check.status}
                      tone={check.status === "passed" ? "green" : check.status === "warning" ? "amber" : "red"}
                    />
                  ))}
                </div>
              )}
            </Panel>

            <Panel className="p-4">
              <PanelTitle icon={ClipboardList} title="建任务" subtitle="手动创建或从模板生成" />
              <div className="mt-4 grid gap-3">
                <div className="grid grid-cols-3 gap-1 rounded-lg border border-neutral-200 bg-neutral-50 p-1">
                  {(["manual", "template", "batch"] as const).map((mode) => (
                    <button
                      key={mode}
                      className={`h-8 rounded-md text-xs font-medium transition ${
                        taskMode === mode ? "bg-white text-neutral-950 shadow-sm" : "text-neutral-500 hover:text-neutral-900"
                      }`}
                      onClick={() => setTaskMode(mode)}
                    >
                      {mode === "manual" ? "手动" : mode === "template" ? "模板" : "批量"}
                    </button>
                  ))}
                </div>
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
                {taskMode === "batch" && (
                  <NumberInput label="批量数量" value={batchCount} onChange={(value) => setBatchCount(Math.max(1, Math.min(50, value)))} />
                )}
                <Button
                  disabled={!run || (taskMode === "template" && !templateId)}
                  onClick={() =>
                    taskMode === "batch"
                      ? void handleCreateBatchTasks()
                      : void handleCreateTask(taskMode === "template")
                  }
                >
                  <ListChecks size={15} />
                  {taskMode === "manual" ? "创建手动任务" : taskMode === "template" ? "模板生成任务" : "批量生成任务"}
                </Button>
              </div>
              <div className="mt-4 space-y-2">
                {tasks.slice(0, 6).map((task) => (
                  <button
                    key={task.taskId}
                    className={`w-full rounded-lg border px-3 py-2 text-left transition ${
                      activeTask?.taskId === task.taskId
                        ? "border-neutral-950 bg-white shadow-sm"
                        : "border-neutral-200 bg-neutral-50 hover:border-neutral-300"
                    }`}
                    onClick={() => void handleSelectTask(task.taskId)}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-sm font-medium text-neutral-800">{task.goal}</span>
                      <Badge tone={statusTone(task.status)}>{task.status}</Badge>
                    </div>
                    <div className="mt-1 truncate font-mono text-[11px] text-neutral-500">{task.taskId}</div>
                  </button>
                ))}
                {tasks.length === 0 && <EmptyState text="鏆傛棤浠诲姟" />}
              </div>
            </Panel>

            <Panel className="p-4">
              <PanelTitle icon={Workflow} title="Plan" subtitle="版本与步骤" />
              <div className="mt-4 space-y-2">
                {activeTask?.activePlan?.steps.map((step) => (
                  <button
                    key={step.planStepId}
                    className={`w-full rounded-lg border p-3 text-left transition ${
                      currentPlanStep?.planStepId === step.planStepId
                        ? "border-neutral-950 bg-white shadow-sm"
                        : "border-neutral-200 bg-neutral-50 hover:border-neutral-300"
                    }`}
                    onClick={() => setSelectedPlanStepId(step.planStepId)}
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-medium">{step.actionType}</span>
                      <Badge tone="neutral">Step {step.sequence}</Badge>
                    </div>
                    <div className="mt-2 text-xs text-neutral-500">{step.successCondition}</div>
                  </button>
                )) ?? <EmptyState text="暂无任务计划" />}
              </div>
              {currentPlanStep && (
                <div className="mt-3 rounded-lg border border-neutral-200 bg-white px-3 py-2 text-xs text-neutral-500">
                  Current Step: <span className="font-medium text-neutral-900">{currentPlanStep.actionType}</span>
                </div>
              )}
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
                <div className="grid grid-cols-2 gap-1 rounded-lg border border-neutral-200 bg-neutral-50 p-1">
                  {(["quick", "advanced"] as const).map((mode) => (
                    <button
                      key={mode}
                      className={`h-8 rounded-md text-xs font-medium transition ${
                        commandMode === mode ? "bg-white text-neutral-950 shadow-sm" : "text-neutral-500 hover:text-neutral-900"
                      }`}
                      onClick={() => setCommandMode(mode)}
                    >
                      {mode === "quick" ? "快捷指令" : "高级指令"}
                    </button>
                  ))}
                </div>
                <select
                  className="h-9 rounded-lg border border-neutral-200 bg-white px-3 text-sm outline-none focus:border-neutral-400"
                  value={effectiveRobotCode}
                  onChange={(event) => setSelectedRobotCode(event.currentTarget.value)}
                >
                  {availableRobotCodes.map((robotCode) => (
                    <option key={robotCode} value={robotCode}>
                      {robotCode}
                    </option>
                  ))}
                </select>
                {commandMode === "quick" ? (
                  <div className="grid grid-cols-2 gap-2">
                    <Button variant="secondary" disabled={!run} onClick={() => void handleQuickAction("goto_pose", stationTarget(selectedScenario?.map, "station-1"))}>
                      <Route size={15} />
                      到装载
                    </Button>
                    <Button variant="secondary" disabled={!run} onClick={() => void handleQuickAction("goto_pose", stationTarget(selectedScenario?.map, "station-2"))}>
                      <Route size={15} />
                      到分拣
                    </Button>
                    <Button variant="secondary" disabled={!run} onClick={() => void handleQuickAction("where")}>
                      <Eye size={15} />
                      查询位置
                    </Button>
                    <Button variant="secondary" disabled={!run} onClick={() => void handleQuickAction("stop")}>
                      <Square size={15} />
                      停止动作
                    </Button>
                  </div>
                ) : (
                  <>
                    <select
                      className="h-9 rounded-lg border border-neutral-200 bg-white px-3 text-sm outline-none focus:border-neutral-400"
                      value={command}
                      onChange={(event) => setCommand(event.currentTarget.value)}
                    >
                      {actionSpecs.map((spec) => (
                        <option key={spec.command} value={spec.command}>
                          {spec.command}
                        </option>
                      ))}
                    </select>
                    <ActionParamForm
                      spec={selectedCommandSpec}
                      values={actionParams}
                      targetOptions={targetOptions}
                      onChange={(name, value) => setActionParams((current) => ({ ...current, [name]: value }))}
                    />
                    <NumberInput label="超时 ms" value={timeoutMs} onChange={(value) => setTimeoutMs(Math.max(1000, value))} />
                    <Button disabled={!run} onClick={handleSendAction}>
                      <SlidersHorizontal size={15} />
                      下发高级 Action
                    </Button>
                  </>
                )}
              </div>
              <div className="mt-4 space-y-2">
                <div className="flex items-center justify-between text-xs text-neutral-500">
                  <span>Action 队列</span>
                  <span>{taskActions.length} / {actions.length}</span>
                </div>
                {visibleActionQueue.slice(0, 6).map((action) => (
                  <button
                    key={action.actionId}
                    className={`w-full rounded-lg border px-3 py-2 text-left transition ${
                      selectedAction?.actionId === action.actionId
                        ? "border-neutral-950 bg-white shadow-sm"
                        : "border-neutral-200 bg-neutral-50 hover:border-neutral-300"
                    }`}
                    onClick={() => void handleSelectAction(action.actionId)}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-sm font-medium text-neutral-800">{action.robotCode} / {action.command}</span>
                      <Badge tone={statusTone(action.status)}>{action.status}</Badge>
                    </div>
                    <div className="mt-1 truncate font-mono text-[11px] text-neutral-500">{action.commandId ?? action.actionId} / {action.actionId}</div>
                  </button>
                ))}
                {actions.length === 0 && <EmptyState text="暂无 Action" />}
              </div>
            </Panel>

            <Panel className="p-4">
              <PanelTitle icon={Gauge} title="看状态" subtitle="机器人 / 任务 / 资源" />
              <div className="mt-4 space-y-3">
                {robots.map((robot) => {
                  const isSelected = robot.robotId === effectiveRobotCode;
                  return (
                    <button
                      key={robot.robotId}
                      className={`w-full rounded-lg border p-3 text-left transition ${
                        isSelected
                          ? "border-neutral-950 bg-white shadow-sm"
                          : "border-neutral-200 bg-neutral-50 hover:border-neutral-300"
                      }`}
                      onClick={() => {
                        setSelectedRobotCode(robot.robotId);
                        if (selectedExceptionOption.targetType === "robot") {
                          setExceptionTarget(robot.robotId);
                        }
                      }}
                    >
                      <div className="flex items-center justify-between">
                        <div className="flex min-w-0 items-center gap-2 text-sm font-medium">
                          <Bot size={15} />
                          <span className="truncate">{robot.robotId}</span>
                        </div>
                        <Badge tone={statusTone(robot.state)}>{robot.state}</Badge>
                      </div>
                      <div className="mt-2 grid grid-cols-2 gap-2 text-xs text-neutral-500">
                        <span>X {Math.round(robot.x)}</span>
                        <span>Y {Math.round(robot.y)}</span>
                        <span>进度 {robot.progress}%</span>
                        <span className="truncate">{robot.currentAction}</span>
                      </div>
                    </button>
                  );
                })}
                {selectedRobot && (
                  <CompactRow
                    label="选中机器人"
                    value={`${selectedRobot.robotId} / ${selectedRobot.state}`}
                    tone={statusTone(selectedRobot.state)}
                  />
                )}
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
                {selectedExceptionOption.targetType === "robot" ? (
                  <select
                    className="h-9 rounded-lg border border-neutral-200 bg-white px-3 text-sm outline-none focus:border-neutral-400"
                    value={exceptionTarget || effectiveRobotCode}
                    onChange={(event) => {
                      setExceptionTarget(event.currentTarget.value);
                      setSelectedRobotCode(event.currentTarget.value);
                    }}
                  >
                    {availableRobotCodes.map((robotCode) => (
                      <option key={robotCode} value={robotCode}>
                        {robotCode}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    className="h-9 rounded-lg border border-neutral-200 bg-white px-3 text-sm outline-none focus:border-neutral-400"
                    value={exceptionTarget}
                    onChange={(event) => setExceptionTarget(event.currentTarget.value)}
                    placeholder="edge-2 / api / message-id"
                  />
                )}
                <div className="grid grid-cols-2 gap-2">
                  <NumberInput label="持续 ms" value={exceptionDurationMs} onChange={(value) => setExceptionDurationMs(Math.max(0, value))} />
                  <label className="flex items-end gap-2 rounded-lg border border-neutral-200 bg-white px-3 py-2 text-xs font-medium text-neutral-500">
                    <input
                      type="checkbox"
                      checked={exceptionAutoRecover}
                      onChange={(event) => setExceptionAutoRecover(event.currentTarget.checked)}
                    />
                    自动恢复
                  </label>
                </div>
                <select
                  className="h-9 rounded-lg border border-neutral-200 bg-white px-3 text-sm outline-none focus:border-neutral-400"
                  value={recoveryMode}
                  onChange={(event) => setRecoveryMode(event.currentTarget.value as typeof recoveryMode)}
                >
                  {recoveryModes.map((mode) => (
                    <option key={mode.value} value={mode.value}>
                      {mode.label}
                    </option>
                  ))}
                </select>
                <div className="grid grid-cols-2 gap-2">
                  <Button variant="danger" disabled={!run} onClick={handleInjectException}>
                    <Zap size={15} />
                    注入异常
                  </Button>
                  <Button variant="secondary" disabled={!run} onClick={handleRecoverException}>
                    <RotateCcw size={15} />
                    恢复
                  </Button>
                </div>
              </div>
            </Panel>
          </div>
        </section>

        <Panel className="p-4">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <PanelTitle icon={MessageSquareText} title="看消息" subtitle="Command / Telemetry / Event / Ack / Alert / Interface / AgentDecision" />
            <div className="flex flex-wrap items-center gap-1.5">
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
              <Button variant="secondary" onClick={handleExportMessages} disabled={!run}>
                <FileDown size={15} />
                导出消息
              </Button>
              <Button
                variant="secondary"
                onClick={handleReplayMessage}
                disabled={!sandboxReplayEnabled || !run || !selectedMessage}
                title="当前 sandbox replay 不触发 MQTT，但会写入当前 Workspace 调试数据，暂时禁用"
              >
                <RotateCcw size={15} />
                沙箱重放（禁用）
              </Button>
              <Badge tone="amber">sandbox 会写入当前 Workspace 数据</Badge>
            </div>
          </div>
          {messageMetrics && (
            <div className="mb-4 grid gap-2 md:grid-cols-5">
              <CompactRow label="Messages" value={String(messageMetrics.messageCount)} tone="blue" />
              <CompactRow label="Errors" value={String(messageMetrics.errorCount)} tone={messageMetrics.errorCount ? "red" : "green"} />
              <CompactRow label="Timeouts" value={String(messageMetrics.timeoutCount)} tone={messageMetrics.timeoutCount ? "amber" : "green"} />
              <CompactRow label="Duplicates" value={String(messageMetrics.duplicateCount)} tone={messageMetrics.duplicateCount ? "amber" : "green"} />
              <CompactRow label="Ack Avg" value={messageMetrics.ackDelayMs.avg === null ? "-" : `${messageMetrics.ackDelayMs.avg}ms`} tone="neutral" />
            </div>
          )}
          <div className="grid gap-4 lg:grid-cols-[1fr_0.75fr_0.75fr_0.75fr_0.95fr]">
            <DiagnosticList
              title="Messages"
              items={filteredMessages.slice(0, 10).map((message) => ({
                id: message.messageId,
                label: String(message.payload.event ?? message.messageType),
                value: messageCategory(message),
                meta: message.topic
              }))}
              selectedId={selectedMessage?.messageId}
              onSelect={setSelectedMessageId}
            />
            <DiagnosticList
              title="Observations"
              items={visibleObservations.slice(0, 10).map((item) => ({
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
            <DiagnosticList
              title="Trace Graph"
              items={(traceGraph?.nodes ?? []).slice(-10).map((node) => ({
                id: node.id,
                label: node.label,
                value: node.status,
                meta: `${node.type}:${node.entityId}`
              }))}
            />
            <MessageDetail message={selectedMessage} onCopy={handleCopyMessagePayload} />
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
  const axisLabels = [];
  for (let x = 0; x <= map.width; x += map.gridSize * 5) {
    axisLabels.push(
      <text key={`xl-${x}`} x={x + 4} y={18} fill="#737373" fontSize="12">
        {x}
      </text>
    );
  }
  for (let y = 0; y <= map.height; y += map.gridSize * 5) {
    axisLabels.push(
      <text key={`yl-${y}`} x={6} y={y - 4} fill="#737373" fontSize="12">
        {y}
      </text>
    );
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
        <g>{axisLabels}</g>
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
        {activeEvents.map((event, index) => {
          const position = eventPosition(map, robots, event);
          if (!position) {
            return null;
          }
          return (
            <g key={`${String(event.event)}-${String(event.targetId)}-${index}`}>
              <circle cx={position.x} cy={position.y} r={28} fill="#fee2e2" stroke="#ef4444" strokeWidth={2} opacity={0.9} />
              <text x={position.x + 34} y={position.y + 5} fill="#b91c1c" fontSize="13" fontWeight="600">
                {String(event.event)}
              </text>
            </g>
          );
        })}
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

function eventPosition(map: SiteMap, robots: RobotState[], event: Record<string, unknown>) {
  const targetId = String(event.targetId ?? "");
  const object = map.objects.find((item) => item.id === targetId);
  if (object) {
    return { x: object.x, y: object.y };
  }
  const edge = map.pathEdges.find((item) => item.id === targetId);
  if (edge) {
    const from = map.objects.find((item) => item.id === edge.from);
    const to = map.objects.find((item) => item.id === edge.to);
    if (from && to) {
      return { x: (from.x + to.x) / 2, y: (from.y + to.y) / 2 };
    }
  }
  const robot = robots.find((item) => item.robotId === targetId);
  if (robot) {
    return { x: robot.x, y: robot.y };
  }
  return null;
}

function DiagnosticList({
  title,
  items,
  selectedId,
  onSelect
}: {
  title: string;
  items: Array<{ id: string; label: string; value: string; meta: string }>;
  selectedId?: string;
  onSelect?: (id: string) => void;
}) {
  return (
    <div>
      <div className="mb-2 text-sm font-semibold">{title}</div>
      <div className="max-h-[300px] space-y-2 overflow-auto pr-1">
        {items.length === 0 ? (
          <EmptyState text="暂无数据" />
        ) : (
          items.map((item) => (
            <button
              key={item.id}
              className={`w-full rounded-lg border p-3 text-left transition ${
                selectedId === item.id
                  ? "border-neutral-950 bg-white shadow-sm"
                  : "border-neutral-200 bg-neutral-50 hover:border-neutral-300"
              }`}
              onClick={() => onSelect?.(item.id)}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-sm font-medium">{item.label}</span>
                <Badge tone={statusTone(item.value)}>{item.value}</Badge>
              </div>
              <div className="mt-1 truncate font-mono text-[11px] text-neutral-500">{item.meta}</div>
            </button>
          ))
        )}
      </div>
    </div>
  );
}

function MessageDetail({ message, onCopy }: { message: MessageRecord | null; onCopy: () => void }) {
  if (!message) {
    return (
      <div>
        <div className="mb-2 text-sm font-semibold">Message Detail</div>
        <EmptyState text="请选择一条消息" />
      </div>
    );
  }
  const payload = message.payload;
  return (
    <div>
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="text-sm font-semibold">Message Detail</div>
        <Button variant="secondary" onClick={onCopy}>
          <Copy size={14} />
          复制
        </Button>
      </div>
      <div className="max-h-[300px] overflow-auto rounded-lg border border-neutral-200 bg-neutral-50 p-3">
        <div className="space-y-2 text-xs text-neutral-600">
          <InfoRow label="messageId" value={message.messageId} />
          <InfoRow label="traceId" value={String(payload.traceId ?? "-")} />
          <InfoRow label="taskId" value={String(payload.taskId ?? "-")} />
          <InfoRow label="robotCode" value={String(payload.robotCode ?? payload.robotId ?? "-")} />
          <InfoRow label="source" value={message.source} />
          <InfoRow label="topic" value={message.topic} />
        </div>
        <pre className="mt-3 whitespace-pre-wrap break-words rounded-lg bg-white p-3 font-mono text-[11px] leading-5 text-neutral-600">
          {JSON.stringify(payload, null, 2)}
        </pre>
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

function ActionParamForm({
  spec,
  values,
  targetOptions,
  onChange
}: {
  spec: ActionCommandSpec | null;
  values: Record<string, string | number>;
  targetOptions: TargetRegistryItem[];
  onChange: (name: string, value: string | number) => void;
}) {
  if (!spec) {
    return <EmptyState text="暂无指令参数规格" />;
  }
  const fields = Object.entries(spec.fields);
  if (fields.length === 0) {
    return (
      <div className="rounded-lg border border-neutral-200 bg-neutral-50 px-3 py-2 text-xs text-neutral-500">
        {spec.command} 当前无需参数。
      </div>
    );
  }
  return (
    <div className="rounded-lg border border-neutral-200 bg-neutral-50 p-3">
      <div className="mb-3 flex items-center justify-between">
        <span className="text-xs font-semibold text-neutral-700">{spec.command}</span>
        <Badge tone="blue">{spec.required.length ? "required" : "optional"}</Badge>
      </div>
      <div className="grid grid-cols-2 gap-2">
        {fields.map(([name, field]) => {
          const value = values[name] ?? spec.defaults[name] ?? "";
          if (field.type === "target") {
            const allowedTypes = new Set(field.targetTypes ?? []);
            const options = allowedTypes.size
              ? targetOptions.filter((target) => allowedTypes.has(target.targetType))
              : targetOptions;
            return (
              <label key={name} className="block text-xs font-medium text-neutral-500">
                {field.label ?? name}
                <select
                  className="mt-1 h-9 w-full rounded-lg border border-neutral-200 bg-white px-3 text-sm text-neutral-950 outline-none focus:border-neutral-400"
                  value={String(value)}
                  onChange={(event) => onChange(name, event.currentTarget.value)}
                >
                  <option value="">选择目标对象</option>
                  {options.map((target) => (
                    <option key={target.targetId} value={target.targetId}>
                      {target.displayName} / {target.targetId}
                    </option>
                  ))}
                </select>
              </label>
            );
          }
          if (field.type === "select") {
            return (
              <label key={name} className="block text-xs font-medium text-neutral-500">
                {field.label ?? name}
                <select
                  className="mt-1 h-9 w-full rounded-lg border border-neutral-200 bg-white px-3 text-sm text-neutral-950 outline-none focus:border-neutral-400"
                  value={String(value)}
                  onChange={(event) => onChange(name, event.currentTarget.value)}
                >
                  {(field.options ?? []).map((option) => (
                    <option key={option} value={option}>
                      {option}
                    </option>
                  ))}
                </select>
              </label>
            );
          }
          return (
            <label key={name} className="block text-xs font-medium text-neutral-500">
              {field.label ?? name}
              <input
                className="mt-1 h-9 w-full rounded-lg border border-neutral-200 bg-white px-3 text-sm text-neutral-950 outline-none focus:border-neutral-400"
                type={field.type === "number" ? "number" : "text"}
                value={value}
                onChange={(event) => onChange(name, field.type === "number" ? Number(event.currentTarget.value) : event.currentTarget.value)}
              />
            </label>
          );
        })}
      </div>
      <div className="mt-3 rounded-md bg-white px-3 py-2 font-mono text-[11px] text-neutral-500">
        {JSON.stringify(normalizedActionParams(values, spec))}
      </div>
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
  const robots = (state?.robotStates ?? []).map((robot) => ({
    robotId: String(robot.robotId ?? robot.robotCode ?? "robot-001"),
    robotType: String(robot.robotType ?? "machine-dog"),
    state: String(robot.state ?? "Idle"),
    x: Number(robot.x ?? 0),
    y: Number(robot.y ?? 0),
    progress: Number(robot.progress ?? 0),
    currentAction: String(robot.currentAction ?? "Waiting"),
    updatedAt: String(robot.updatedAt ?? new Date().toISOString())
  }));
  const existingRobotIds = new Set(robots.map((robot) => robot.robotId));
  const fallbackRobots = (scenario?.robotCodes ?? (robots.length ? [] : ["robot-001"]))
    .filter((robotId) => !existingRobotIds.has(robotId))
    .map((robotId, index) => {
      const profile = scenario?.robots?.find((item) => item.robotCode === robotId);
      return {
        robotId,
        robotType: profile?.robotType ?? "machine-dog",
        state: profile?.state ?? "Idle",
        x: Number(profile?.initialPose?.x ?? 220 + index * 100),
        y: Number(profile?.initialPose?.y ?? 360),
        progress: 0,
        currentAction: "Waiting",
        updatedAt: new Date().toISOString()
      };
    });
  return [...robots, ...fallbackRobots];
}

function stationTarget(map: SiteMap | undefined, stationId: string) {
  const station = map?.objects.find((item) => item.id === stationId) ?? map?.objects.find((item) => item.type === "station");
  return {
    x: station?.x ?? 760,
    y: station?.y ?? 420
  };
}

function nextRobotCode(robotCodes: string[]) {
  const maxIndex = robotCodes.reduce((currentMax, robotCode) => {
    const match = /^robot-(\d+)$/.exec(robotCode);
    return match ? Math.max(currentMax, Number(match[1])) : currentMax;
  }, 0);
  return `robot-${String(maxIndex + 1).padStart(3, "0")}`;
}

function commandTargetParams(x: number, y: number, z: number, yaw: number) {
  return {
    x,
    y,
    z,
    yaw
  };
}

function defaultActionParams(spec: ActionCommandSpec | null | undefined): Record<string, string | number> {
  if (!spec) {
    return {};
  }
  const defaults = { ...spec.defaults } as Record<string, string | number>;
  if (spec.command === "goto_pose") {
    return {
      x: Number(defaults.x ?? 760),
      y: Number(defaults.y ?? 420),
      z: Number(defaults.z ?? 0),
      yaw: Number(defaults.yaw ?? 0),
      speed: Number(defaults.speed ?? 1),
      tolerance: Number(defaults.tolerance ?? 50)
    };
  }
  return defaults;
}

function normalizedActionParams(
  values: Record<string, string | number>,
  spec: ActionCommandSpec | null
): Record<string, unknown> {
  if (!spec) {
    return values;
  }
  const normalized: Record<string, unknown> = {};
  for (const [name, field] of Object.entries(spec.fields)) {
    const rawValue = values[name] ?? spec.defaults[name];
    if (rawValue === "" || rawValue === null || rawValue === undefined) {
      continue;
    }
    normalized[name] = field.type === "number" ? Number(rawValue) : rawValue;
  }
  return normalized;
}

function messageCategory(message: MessageRecord) {
  if (message.messageType === "command") {
    return "Command";
  }
  const event = String(message.payload.event ?? message.messageType);
  if (["command.accepted", "command.rejected"].includes(event)) {
    return "Ack";
  }
  if (["pose.updated", "where.result", "action.progress"].includes(event)) {
    return "Telemetry";
  }
  if (event.startsWith("interface.")) {
    return "Interface";
  }
  if (event.startsWith("agent.")) {
    return "AgentDecision";
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
  if (
    [
      "Succeeded",
      "Running",
      "Accepted",
      "Issued",
      "Moving",
      "Picking",
      "Placing",
      "Loading",
      "Unloading",
      "Inspecting",
      "Charging",
      "Waiting",
      "ok",
      "Event",
      "Telemetry",
      "passed"
    ].includes(value)
  ) {
    return "green";
  }
  if (["Alert", "Failed", "Rejected", "Timeout", "Error", "Offline", "critical", "failed"].includes(value)) {
    return "red";
  }
  if (["Paused", "Draft", "Ready", "Pending", "Ack", "warning"].includes(value)) {
    return "amber";
  }
  if (["Command", "Interface", "AgentDecision"].includes(value)) {
    return "blue";
  }
  return "neutral";
}
