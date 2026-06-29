import { motion } from "framer-motion";
import {
  Activity,
  BookOpen,
  Download,
  FileJson,
  Filter,
  RadioTower,
  Rocket,
  Save,
  Send,
  ShieldCheck,
  Terminal,
  Upload,
  Wifi,
  WifiOff,
  Zap
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { MapEditor } from "./components/MapEditor";
import { SimulationDashboard } from "./SimulationDashboard";
import { Badge, Button, Panel } from "./components/ui";
import {
  createCommand,
  createExport,
  getCommandTrace,
  getConnections,
  getHealth,
  getMap,
  getMessages,
  getMqttContract,
  getRobots,
  getSessionSocketUrl,
  importMap,
  publishDraft,
  saveMapDraft,
  triggerConsoleEvent,
  validateDraft
} from "./lib/api";
import type {
  CommandTrace,
  ConnectionInfo,
  HealthResponse,
  MessageRecord,
  MqttContract,
  RobotState,
  SessionSnapshot,
  SiteMap
} from "./lib/types";

const SESSION_ID = "session-local";

const fallbackMap: SiteMap = {
  id: "site-a",
  name: "A 区分拣场地",
  width: 1200,
  height: 760,
  unit: "mm",
  gridSize: 40,
  configVersion: "v0.1-local",
  objects: [
    { id: "zone-1", type: "zone", name: "作业区", x: 360, y: 260, width: 460, height: 280, color: "#dbeafe" },
    { id: "obstacle-1", type: "obstacle", name: "安全围栏", x: 610, y: 220, width: 130, height: 80, color: "#e5e7eb" },
    { id: "station-1", type: "station", name: "抓取工位", x: 220, y: 240, radius: 22, color: "#dcfce7" },
    { id: "station-2", type: "station", name: "分拣工位", x: 760, y: 420, radius: 22, color: "#dcfce7" },
    { id: "pathNode-1", type: "pathNode", name: "P1", x: 220, y: 360, radius: 7, color: "#111827" },
    { id: "pathNode-2", type: "pathNode", name: "P2", x: 520, y: 360, radius: 7, color: "#111827" },
    { id: "pathNode-3", type: "pathNode", name: "P3", x: 760, y: 420, radius: 7, color: "#111827" },
    { id: "resource-1", type: "resourcePoint", name: "充电点", x: 960, y: 160, width: 36, height: 36, color: "#fef3c7" }
  ],
  pathEdges: [
    { id: "edge-1", from: "pathNode-1", to: "pathNode-2", direction: "two_way", capacity: 1, pathGroupId: "path-group-a", sequence: 1 },
    { id: "edge-2", from: "pathNode-2", to: "pathNode-3", direction: "two_way", capacity: 1, pathGroupId: "path-group-b", sequence: 1 }
  ],
  pathGroups: [
    {
      id: "path-group-a",
      name: "Robot A Path",
      edgeIds: ["edge-1"],
      allowedRobotCodes: ["robot-001"],
      color: "#2563eb",
      status: "active",
      priority: 5,
      metadata: { source: "fallback" }
    },
    {
      id: "path-group-b",
      name: "Robot B Path",
      edgeIds: ["edge-2"],
      allowedRobotCodes: ["robot-002"],
      color: "#16a34a",
      status: "active",
      priority: 5,
      metadata: { source: "fallback" }
    }
  ]
};

const fallbackRobots: RobotState[] = [
  {
    robotId: "robot-001",
    robotType: "machine-dog",
    state: "Idle",
    x: 220,
    y: 360,
    progress: 0,
    currentAction: "等待指令",
    updatedAt: new Date().toISOString()
  }
];

export default function App() {
  if (window.location.pathname === "/simulation") {
    return <SimulationDashboard />;
  }

  const [map, setMap] = useState<SiteMap>(fallbackMap);
  const [robots, setRobots] = useState<RobotState[]>(fallbackRobots);
  const [messages, setMessages] = useState<MessageRecord[]>([]);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [connections, setConnections] = useState<ConnectionInfo | null>(null);
  const [mqttContract, setMqttContract] = useState<MqttContract | null>(null);
  const [commandTrace, setCommandTrace] = useState<CommandTrace | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draftId, setDraftId] = useState<string | null>(null);
  const [status, setStatus] = useState("本地草稿");
  const [socketState, setSocketState] = useState<"connecting" | "open" | "fallback">("connecting");
  const [commandType, setCommandType] = useState("goto_pose");
  const [targetX, setTargetX] = useState(760);
  const [targetY, setTargetY] = useState(420);
  const [messageRobotCode, setMessageRobotCode] = useState("");
  const [messageKeyword, setMessageKeyword] = useState("");
  const [messageEvent, setMessageEvent] = useState("");
  const importInputRef = useRef<HTMLInputElement | null>(null);

  async function refreshAll() {
    try {
      const [nextMap, nextRobots, nextMessages, nextHealth, nextConnections, nextContract] = await Promise.all([
        getMap(),
        getRobots(),
        getMessages(),
        getHealth(),
        getConnections(),
        getMqttContract()
      ]);
      setMap(nextMap);
      setRobots(nextRobots);
      setMessages(nextMessages);
      setHealth(nextHealth);
      setConnections(nextConnections);
      setMqttContract(nextContract);
      setStatus("已连接平台 API");
    } catch {
      setStatus("离线演示模式");
    }
  }

  async function refreshRuntime() {
    try {
      const [nextRobots, nextMessages, nextHealth] = await Promise.all([
        getRobots(),
        getMessages(),
        getHealth()
      ]);
      setRobots(nextRobots);
      setMessages(nextMessages);
      setHealth(nextHealth);
    } catch {
      setSocketState("fallback");
    }
  }

  useEffect(() => {
    void refreshAll();
  }, []);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let reconnectTimer: number | undefined;
    let disposed = false;

    function connect() {
      setSocketState("connecting");
      socket = new WebSocket(getSessionSocketUrl(SESSION_ID));
      socket.onopen = () => {
        setSocketState("open");
        setStatus("实时连接已建立");
      };
      socket.onmessage = (event) => {
        const snapshot = JSON.parse(event.data) as SessionSnapshot;
        if (snapshot.type !== "snapshot") {
          return;
        }
        setRobots(snapshot.data.robots);
        setMessages(snapshot.data.messages);
      };
      socket.onerror = () => {
        socket?.close();
      };
      socket.onclose = () => {
        if (disposed) {
          return;
        }
        setSocketState("fallback");
        reconnectTimer = window.setTimeout(connect, 3000);
      };
    }

    connect();
    return () => {
      disposed = true;
      if (reconnectTimer) {
        window.clearTimeout(reconnectTimer);
      }
      socket?.close();
    };
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => {
      if (socketState !== "open") {
        void refreshRuntime();
      } else {
        void getHealth().then(setHealth).catch(() => undefined);
      }
    }, 4000);
    return () => window.clearInterval(timer);
  }, [socketState]);

  const selectedObject = useMemo(
    () => map.objects.find((item) => item.id === selectedId),
    [map.objects, selectedId]
  );

  const displayedMessages = useMemo(() => {
    const robotCode = messageRobotCode.trim().toLowerCase();
    const keyword = messageKeyword.trim().toLowerCase();
    const event = messageEvent.trim().toLowerCase();
    return messages.filter((message) => {
      const payload = message.payload;
      const payloadRobot = String(payload.robotCode ?? payload.robotId ?? "").toLowerCase();
      const payloadEvent = String(payload.event ?? "").toLowerCase();
      const searchable = [
        message.messageId,
        message.messageType,
        message.source,
        message.topic,
        payload.commandId,
        payload.traceId,
        payload.taskId,
        payload.requestId
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      if (robotCode && payloadRobot !== robotCode) {
        return false;
      }
      if (event && payloadEvent !== event) {
        return false;
      }
      if (keyword && !searchable.includes(keyword)) {
        return false;
      }
      return true;
    });
  }, [messageEvent, messageKeyword, messageRobotCode, messages]);

  async function handleSaveDraft() {
    const response = await saveMapDraft(map);
    setDraftId(response.draftId);
    setStatus(`草稿已保存 ${response.draftId}`);
  }

  async function handleValidate() {
    const activeDraft = draftId ?? (await saveMapDraft(map)).draftId;
    setDraftId(activeDraft);
    const response = await validateDraft(map.id, activeDraft);
    setStatus(response.ok ? "草稿校验通过" : `草稿存在 ${response.issues.length} 个问题：${response.issues[0]}`);
  }

  async function handlePublish() {
    const activeDraft = draftId ?? (await saveMapDraft(map)).draftId;
    const nextMap = await publishDraft(map.id, activeDraft);
    setMap(nextMap);
    setDraftId(null);
    setStatus(`已发布配置 ${nextMap.configVersion}`);
  }

  async function handleExport(exportType: string) {
    const response = await createExport(exportType);
    setStatus(`导出任务已生成 ${response.fileName}`);
    if (response.url) {
      window.open(response.url, "_blank");
    }
  }

  async function handleImportFile(file: File) {
    try {
      const json = JSON.parse(await file.text());
      const importedMap = json.map ?? json.data?.map ?? json.data?.data?.map ?? json;
      const response = await importMap(importedMap);
      setMap(response.map);
      setDraftId(response.draftId);
      setStatus(response.ok ? "导入已生成草稿" : `导入存在 ${response.issues.length} 个问题：${response.issues[0]}`);
    } catch (error) {
      setStatus(`导入失败：${error instanceof Error ? error.message : "文件格式错误"}`);
    }
  }

  async function handleConsoleEvent(eventType: string) {
    const response = await triggerConsoleEvent(eventType);
    setStatus(response.mqttPublished ? `事件已发布 ${eventType}` : "事件已记录，MQTT 未连接");
    void refreshRuntime();
  }

  async function handleSendCommand() {
    const robotId = robots[0]?.robotId ?? "robot-001";
    const params = commandType === "goto_pose" ? { x: targetX, y: targetY, z: 0, yaw: 0 } : {};
    const response = await createCommand({ robotId, command: commandType, params });
    const trace = await getCommandTrace(response.commandId);
    setCommandTrace(trace);
    setStatus(`指令已创建 ${response.commandId}`);
    void refreshRuntime();
  }

  return (
    <main className="min-h-screen bg-[radial-gradient(circle_at_top_left,#ffffff_0,#f6f7f9_42%,#eef0f3_100%)] text-neutral-950">
      <div className="mx-auto flex max-w-[1680px] flex-col gap-4 p-4 lg:p-6">
        <motion.header
          initial={{ opacity: 0, y: -8 }}
          animate={{ opacity: 1, y: 0 }}
          className="flex flex-col gap-3 rounded-2xl border border-neutral-200/80 bg-white/80 p-4 shadow-soft backdrop-blur lg:flex-row lg:items-center lg:justify-between"
        >
          <div>
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <Badge tone="blue">二维环境编辑</Badge>
              <Badge tone={status.includes("离线") ? "amber" : "green"}>{status}</Badge>
              <Badge tone={socketState === "open" ? "green" : "amber"}>
                {socketState === "open" ? "WebSocket" : "轮询兜底"}
              </Badge>
              <Badge tone="neutral">配置 {map.configVersion}</Badge>
            </div>
            <h1 className="text-2xl font-semibold tracking-tight text-neutral-950 lg:text-3xl">
              具身智能业务流程仿真平台
            </h1>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" onClick={() => (window.location.href = "/simulation")}>
              <Activity size={16} />
              仿真驾驶舱
            </Button>
            <Button variant="secondary" onClick={handleSaveDraft}>
              <Save size={16} />
              保存草稿
            </Button>
            <Button variant="secondary" onClick={handleValidate}>
              <ShieldCheck size={16} />
              校验
            </Button>
            <Button variant="secondary" onClick={() => importInputRef.current?.click()}>
              <Upload size={16} />
              导入配置
            </Button>
            <Button onClick={handlePublish}>
              <Rocket size={16} />
              发布配置
            </Button>
            <input
              ref={importInputRef}
              className="hidden"
              type="file"
              accept="application/json,.json"
              onChange={(event) => {
                const file = event.currentTarget.files?.[0];
                if (file) {
                  void handleImportFile(file);
                }
                event.currentTarget.value = "";
              }}
            />
          </div>
        </motion.header>

        <section className="grid gap-4 xl:grid-cols-[1fr_380px]">
          <Panel className="p-3">
            <MapEditor
              map={map}
              robots={robots}
              selectedId={selectedId}
              onSelectedChange={setSelectedId}
              onMapChange={(nextMap) => {
                setMap(nextMap);
                setStatus("存在未保存编辑");
              }}
            />
          </Panel>

          <div className="flex flex-col gap-4">
            <Panel className="p-4">
              <div className="mb-4 flex items-center justify-between">
                <div>
                  <h2 className="text-sm font-semibold">运行态摘要</h2>
                  <p className="text-xs text-neutral-500">MQTT 上报状态</p>
                </div>
                <Activity size={18} className="text-neutral-500" />
              </div>
              <div className="space-y-3">
                {robots.map((robot) => (
                  <div key={robot.robotId} className="rounded-xl border border-neutral-200 bg-neutral-50 p-3">
                    <div className="flex items-center justify-between">
                      <div className="font-medium">{robot.robotId}</div>
                      <Badge tone={robot.state === "Error" ? "red" : robot.state === "Idle" ? "neutral" : "green"}>
                        {robot.state}
                      </Badge>
                    </div>
                    <div className="mt-2 grid grid-cols-2 gap-2 text-xs text-neutral-500">
                      <span>动作：{robot.currentAction}</span>
                      <span className="tabular">进度：{robot.progress}%</span>
                      <span className="tabular">X {Math.round(robot.x)}</span>
                      <span className="tabular">Y {Math.round(robot.y)}</span>
                    </div>
                  </div>
                ))}
              </div>
            </Panel>

            <Panel className="p-4">
              <div className="mb-3 flex items-center justify-between">
                <div>
                  <h2 className="text-sm font-semibold">系统健康</h2>
                  <p className="text-xs text-neutral-500">API / MQTT / 存储 / 执行体</p>
                </div>
                {socketState === "open" ? (
                  <Wifi size={18} className="text-emerald-600" />
                ) : (
                  <WifiOff size={18} className="text-amber-600" />
                )}
              </div>
              <div className="grid grid-cols-2 gap-2 text-xs">
                <HealthItem label="API" value={health?.components.api.status ?? "unknown"} />
                <HealthItem label="存储" value={health?.components.storage.status ?? "unknown"} />
                <HealthItem label="MQTT" value={health?.components.mqttBridge.status ?? "unknown"} />
                <HealthItem label="执行体" value={health?.components.virtualExecutor.status ?? "unknown"} />
              </div>
            </Panel>

            <Panel className="p-4">
              <div className="mb-3 flex items-center justify-between">
                <div>
                  <h2 className="text-sm font-semibold">连接信息</h2>
                  <p className="text-xs text-neutral-500">局域网联调入口</p>
                </div>
                <RadioTower size={18} className="text-neutral-500" />
              </div>
              <div className="space-y-2 text-xs text-neutral-500">
                <ConnectionLine label="前端" value={connections?.services.frontend.url ?? "-"} />
                <ConnectionLine label="API" value={connections?.services.api.baseUrl ?? "-"} />
                <ConnectionLine
                  label="MQTT"
                  value={
                    connections
                      ? `${connections.services.mqtt.host}:${connections.services.mqtt.port}`
                      : "-"
                  }
                />
              </div>
            </Panel>

            <Panel className="p-4">
              <div className="mb-3 flex items-center justify-between">
                <div>
                  <h2 className="text-sm font-semibold">MQTT 契约</h2>
                  <p className="text-xs text-neutral-500">机器狗 command/result</p>
                </div>
                <BookOpen size={18} className="text-neutral-500" />
              </div>
              <div className="space-y-2 text-xs text-neutral-500">
                <ConnectionLine
                  label="command"
                  value={mqttContract?.command.topic ?? connections?.services.mqtt.commandTopic ?? "-"}
                />
                <ConnectionLine
                  label="result"
                  value={mqttContract?.result.topic ?? connections?.services.mqtt.resultTopic ?? "-"}
                />
              </div>
              <div className="mt-3 flex flex-wrap gap-1.5">
                {(mqttContract?.command.supportedCommands ?? connections?.services.mqtt.supportedCommands ?? []).map((command) => (
                  <Badge key={command} tone="blue">
                    {command}
                  </Badge>
                ))}
              </div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {(mqttContract?.result.events ?? connections?.services.mqtt.resultEvents ?? []).slice(0, 6).map((eventName) => (
                  <Badge key={eventName} tone="neutral">
                    {eventName}
                  </Badge>
                ))}
              </div>
            </Panel>

            <Panel className="p-4">
              <div className="mb-3 flex items-center justify-between">
                <div>
                  <h2 className="text-sm font-semibold">指令调试</h2>
                  <p className="text-xs text-neutral-500">机器狗 command/result 协议</p>
                </div>
                <Terminal size={18} className="text-neutral-500" />
              </div>
              <div className="grid gap-2">
                <select
                  className="h-9 rounded-lg border border-neutral-200 bg-white px-3 text-sm outline-none focus:border-neutral-400"
                  value={commandType}
                  onChange={(event) => setCommandType(event.currentTarget.value)}
                >
                  {["goto_pose", "where", "stop"].map((action) => (
                    <option key={action} value={action}>
                      {action}
                    </option>
                  ))}
                </select>
                <div className="grid grid-cols-2 gap-2">
                  <NumberField label="X" value={targetX} onChange={setTargetX} />
                  <NumberField label="Y" value={targetY} onChange={setTargetY} />
                </div>
                <Button onClick={handleSendCommand}>
                  <Send size={15} />
                  发送测试指令
                </Button>
                {commandTrace && (
                  <div className="rounded-lg border border-neutral-200 bg-neutral-50 p-3 text-xs text-neutral-500">
                    <div className="flex items-center justify-between">
                      <span className="font-medium text-neutral-900">{commandTrace.commandId}</span>
                      <Badge tone="blue">{commandTrace.messageCount} 条消息</Badge>
                    </div>
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {commandTrace.messages.slice(-8).map((message) => (
                        <Badge key={message.messageId} tone="neutral">
                          {message.messageType}
                        </Badge>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </Panel>

            <Panel className="p-4">
              <div className="mb-3 flex items-center justify-between">
                <div>
                  <h2 className="text-sm font-semibold">MQTT 与消息</h2>
                  <p className="text-xs text-neutral-500">command / result / event</p>
                </div>
                <Filter size={18} className="text-neutral-500" />
              </div>
              <div className="mb-3 grid gap-2">
                <input
                  className="h-9 rounded-lg border border-neutral-200 bg-white px-3 text-xs outline-none focus:border-neutral-400"
                  placeholder="robotCode"
                  value={messageRobotCode}
                  onChange={(event) => setMessageRobotCode(event.currentTarget.value)}
                />
                <input
                  className="h-9 rounded-lg border border-neutral-200 bg-white px-3 text-xs outline-none focus:border-neutral-400"
                  placeholder="commandId / traceId / taskId"
                  value={messageKeyword}
                  onChange={(event) => setMessageKeyword(event.currentTarget.value)}
                />
                <input
                  className="h-9 rounded-lg border border-neutral-200 bg-white px-3 text-xs outline-none focus:border-neutral-400"
                  placeholder="event，如 pose.updated"
                  value={messageEvent}
                  onChange={(event) => setMessageEvent(event.currentTarget.value)}
                />
              </div>
              <div className="max-h-[260px] space-y-2 overflow-auto pr-1">
                {displayedMessages.length === 0 ? (
                  <div className="rounded-xl border border-dashed border-neutral-200 p-4 text-sm text-neutral-500">
                    暂无消息
                  </div>
                ) : (
                  displayedMessages.slice(0, 12).map((message) => (
                    <div key={message.messageId} className="rounded-lg border border-neutral-200 bg-white p-3">
                      <div className="flex items-center justify-between gap-2">
                        <Badge tone="blue">
                          {String(message.payload.event ?? message.messageType)}
                        </Badge>
                        <span className="text-[11px] tabular text-neutral-400">
                          {new Date(message.createdAt).toLocaleTimeString()}
                        </span>
                      </div>
                      <div className="mt-2 truncate text-xs text-neutral-500">{message.topic}</div>
                    </div>
                  ))
                )}
              </div>
              <div className="mt-3 grid grid-cols-2 gap-2">
                <Button variant="secondary" onClick={() => handleConsoleEvent("resource.blocked")}>
                  <Zap size={15} />
                  资源阻塞
                </Button>
                <Button variant="secondary" onClick={() => handleConsoleEvent("path.slowdown")}>
                  <Zap size={15} />
                  路径限速
                </Button>
              </div>
            </Panel>

            <Panel className="p-4">
              <div className="mb-3 flex items-center justify-between">
                <div>
                  <h2 className="text-sm font-semibold">导出</h2>
                  <p className="text-xs text-neutral-500">配置、过程、消息与联调记录</p>
                </div>
                <FileJson size={18} className="text-neutral-500" />
              </div>
              <div className="grid gap-2">
                <Button variant="secondary" onClick={() => handleExport("config")}>
                  <Download size={15} />
                  导出配置
                </Button>
                <Button variant="secondary" onClick={() => handleExport("process_log")}>
                  <Download size={15} />
                  导出过程日志
                </Button>
                <Button variant="secondary" onClick={() => handleExport("mqtt_debug")}>
                  <Download size={15} />
                  导出 MQTT 记录
                </Button>
              </div>
            </Panel>

            <Panel className="p-4">
              <h2 className="text-sm font-semibold">当前选择</h2>
              <div className="mt-2 rounded-xl bg-neutral-50 p-3 text-xs leading-5 text-neutral-500">
                {selectedObject ? (
                  <>
                    <div className="font-medium text-neutral-900">{selectedObject.name}</div>
                    <div>类型：{selectedObject.type}</div>
                    <div className="tabular">
                      坐标：{selectedObject.x}, {selectedObject.y}
                    </div>
                  </>
                ) : (
                  "未选择对象"
                )}
              </div>
            </Panel>
          </div>
        </section>
      </div>
    </main>
  );
}

function HealthItem({ label, value }: { label: string; value: string }) {
  const tone = value === "ok" ? "green" : value === "unknown" ? "neutral" : "amber";
  return (
    <div className="flex items-center justify-between rounded-lg border border-neutral-200 bg-neutral-50 px-3 py-2">
      <span className="text-neutral-500">{label}</span>
      <Badge tone={tone}>{value}</Badge>
    </div>
  );
}

function ConnectionLine({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-lg border border-neutral-200 bg-neutral-50 px-3 py-2">
      <span className="shrink-0 text-neutral-500">{label}</span>
      <span className="truncate font-mono text-[11px] text-neutral-900">{value}</span>
    </div>
  );
}

function NumberField({
  label,
  value,
  onChange
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
}) {
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
