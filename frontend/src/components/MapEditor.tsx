import { CircleDot, Diamond, MousePointer2, Pentagon, Route, Square, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { PointerEvent } from "react";
import { Badge, Button } from "./ui";
import type { MapObject, MapObjectType, PathEdge, PathGroup, RobotState, SiteMap } from "../lib/types";
import { cn } from "../lib/utils";

type Tool = "select" | MapObjectType;

const toolMeta: Array<{ tool: Tool; label: string; icon: typeof MousePointer2 }> = [
  { tool: "select", label: "选择", icon: MousePointer2 },
  { tool: "zone", label: "区域", icon: Pentagon },
  { tool: "obstacle", label: "障碍物", icon: Square },
  { tool: "station", label: "工位", icon: CircleDot },
  { tool: "pathNode", label: "路径点", icon: Route },
  { tool: "resourcePoint", label: "资源点", icon: Diamond }
];

const colors: Record<MapObjectType, string> = {
  zone: "#dbeafe",
  obstacle: "#e5e7eb",
  station: "#dcfce7",
  pathNode: "#111827",
  resourcePoint: "#fef3c7"
};

const strokeColors: Record<MapObjectType, string> = {
  zone: "#3b82f6",
  obstacle: "#6b7280",
  station: "#16a34a",
  pathNode: "#111827",
  resourcePoint: "#d97706"
};

const pathGroupColors = ["#2563eb", "#16a34a", "#dc2626", "#7c3aed", "#0891b2", "#d97706"];

function objectDefaults(type: MapObjectType, x: number, y: number, count: number): MapObject {
  const base = {
    id: `${type}-${Date.now()}-${count}`,
    type,
    name: `${toolMeta.find((item) => item.tool === type)?.label ?? "对象"} ${count + 1}`,
    x,
    y,
    color: colors[type]
  };
  if (type === "zone") {
    return { ...base, width: 180, height: 120 };
  }
  if (type === "obstacle") {
    return { ...base, width: 90, height: 70 };
  }
  if (type === "station") {
    return { ...base, radius: 20 };
  }
  if (type === "resourcePoint") {
    return { ...base, width: 34, height: 34 };
  }
  return { ...base, radius: 6 };
}

function defaultPathGroup(edges: PathEdge[]): PathGroup {
  return {
    id: "default-path-group",
    name: "默认路径",
    edgeIds: edges.map((edge) => edge.id),
    allowedRobotCodes: [],
    color: "#111827",
    status: "active",
    priority: 5,
    metadata: { source: "legacy" }
  };
}

function normalizedPathGroups(map: SiteMap): PathGroup[] {
  const groups = map.pathGroups?.length ? map.pathGroups : [defaultPathGroup(map.pathEdges)];
  return groups.map((group, index) => ({
    ...group,
    color: group.color || pathGroupColors[index % pathGroupColors.length],
    edgeIds: group.edgeIds ?? [],
    allowedRobotCodes: group.allowedRobotCodes ?? [],
    metadata: group.metadata ?? {}
  }));
}

function orderedGroupEdges(map: SiteMap, group: PathGroup): PathEdge[] {
  const edges = map.pathEdges.filter((edge) => edge.pathGroupId === group.id || group.edgeIds.includes(edge.id));
  return edges.sort((first, second) => {
    const firstSequence = first.sequence ?? group.edgeIds.indexOf(first.id);
    const secondSequence = second.sequence ?? group.edgeIds.indexOf(second.id);
    return firstSequence - secondSequence;
  });
}

function groupNodeIds(map: SiteMap, group: PathGroup): string[] {
  const nodeIds = Array.isArray(group.metadata.nodeIds) ? group.metadata.nodeIds.map(String) : [];
  if (nodeIds.length) {
    return nodeIds;
  }
  const edges = orderedGroupEdges(map, group);
  if (!edges.length) {
    return [];
  }
  return [edges[0].from, ...edges.map((edge) => edge.to)];
}

interface MapEditorProps {
  map: SiteMap;
  robots?: RobotState[];
  selectedId: string | null;
  onMapChange: (map: SiteMap) => void;
  onSelectedChange: (id: string | null) => void;
}

export function MapEditor({ map, robots = [], selectedId, onMapChange, onSelectedChange }: MapEditorProps) {
  const [tool, setTool] = useState<Tool>("select");
  const [cursor, setCursor] = useState({ x: 0, y: 0 });
  const [dragId, setDragId] = useState<string | null>(null);
  const [snapToGrid, setSnapToGrid] = useState(true);
  const [showGrid, setShowGrid] = useState(true);
  const pathGroups = useMemo(() => normalizedPathGroups(map), [map]);
  const [selectedPathGroupId, setSelectedPathGroupId] = useState(pathGroups[0]?.id ?? "default-path-group");

  const selected = map.objects.find((item) => item.id === selectedId) ?? null;
  const activePathGroup = pathGroups.find((group) => group.id === selectedPathGroupId) ?? pathGroups[0];

  useEffect(() => {
    if (!pathGroups.some((group) => group.id === selectedPathGroupId)) {
      setSelectedPathGroupId(pathGroups[0]?.id ?? "default-path-group");
    }
  }, [pathGroups, selectedPathGroupId]);

  function toMapPoint(event: PointerEvent<SVGSVGElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    const rawX = ((event.clientX - rect.left) / rect.width) * map.width;
    const rawY = ((event.clientY - rect.top) / rect.height) * map.height;
    if (!snapToGrid) {
      return { x: Math.round(rawX), y: Math.round(rawY) };
    }
    return {
      x: Math.round(rawX / map.gridSize) * map.gridSize,
      y: Math.round(rawY / map.gridSize) * map.gridSize
    };
  }

  function handleCanvasPointerMove(event: PointerEvent<SVGSVGElement>) {
    const next = toMapPoint(event);
    setCursor(next);
    if (!dragId) {
      return;
    }
    onMapChange({
      ...map,
      objects: map.objects.map((item) => (item.id === dragId ? { ...item, ...next } : item))
    });
  }

  function handleCanvasPointerDown(event: PointerEvent<SVGSVGElement>) {
    const target = event.target as Element;
    if (target.closest("[data-map-object]")) {
      return;
    }
    if (tool === "select") {
      onSelectedChange(null);
      return;
    }

    const point = toMapPoint(event);
    const nextObject = objectDefaults(tool, point.x, point.y, map.objects.length);
    const nextObjects = [...map.objects, nextObject];
    let nextEdges = map.pathEdges;
    let nextGroups = pathGroups;
    if (tool === "pathNode" && activePathGroup) {
      const groupNodes = groupNodeIds(map, activePathGroup);
      const lastNodeId = groupNodes[groupNodes.length - 1];
      const nextNodeIds = [...groupNodes, nextObject.id];
      if (lastNodeId) {
        const edgeId = `edge-${Date.now()}`;
        nextEdges = [
          ...map.pathEdges,
          {
            id: edgeId,
            from: lastNodeId,
            to: nextObject.id,
            direction: "two_way" as const,
            capacity: 1,
            pathGroupId: activePathGroup.id,
            sequence: activePathGroup.edgeIds.length + 1
          }
        ];
        nextGroups = pathGroups.map((group) =>
          group.id === activePathGroup.id
            ? {
                ...group,
                edgeIds: [...group.edgeIds, edgeId],
                metadata: { ...group.metadata, nodeIds: nextNodeIds }
              }
            : group
        );
      } else {
        nextGroups = pathGroups.map((group) =>
          group.id === activePathGroup.id
            ? {
                ...group,
                metadata: { ...group.metadata, nodeIds: nextNodeIds }
              }
            : group
        );
      }
    }

    onMapChange({ ...map, objects: nextObjects, pathEdges: nextEdges, pathGroups: nextGroups });
    onSelectedChange(nextObject.id);
    setTool("select");
  }

  function handleDeleteSelected() {
    if (!selected) {
      return;
    }
    const removedEdgeIds = map.pathEdges
      .filter((edge) => edge.from === selected.id || edge.to === selected.id)
      .map((edge) => edge.id);
    onMapChange({
      ...map,
      objects: map.objects.filter((item) => item.id !== selected.id),
      pathEdges: map.pathEdges.filter((edge) => edge.from !== selected.id && edge.to !== selected.id),
      pathGroups: pathGroups.map((group) => {
        const nodeIds = groupNodeIds(map, group).filter((nodeId) => nodeId !== selected.id);
        return {
          ...group,
          edgeIds: group.edgeIds.filter((edgeId) => !removedEdgeIds.includes(edgeId)),
          metadata: { ...group.metadata, nodeIds }
        };
      })
    });
    onSelectedChange(null);
  }

  function handleCreatePathGroup() {
    const id = `path-group-${Date.now()}`;
    const nextGroup: PathGroup = {
      id,
      name: `路径组 ${pathGroups.length + 1}`,
      edgeIds: [],
      allowedRobotCodes: [],
      color: pathGroupColors[pathGroups.length % pathGroupColors.length],
      status: "active",
      priority: 5,
      metadata: { nodeIds: [] }
    };
    onMapChange({ ...map, pathGroups: [...pathGroups, nextGroup] });
    setSelectedPathGroupId(id);
  }

  function updatePathGroup(next: PathGroup) {
    onMapChange({
      ...map,
      pathGroups: pathGroups.map((group) => (group.id === next.id ? next : group))
    });
  }

  const gridLines = [];
  for (let x = 0; x <= map.width; x += map.gridSize) {
    gridLines.push(<line key={`x-${x}`} x1={x} x2={x} y1={0} y2={map.height} />);
  }
  for (let y = 0; y <= map.height; y += map.gridSize) {
    gridLines.push(<line key={`y-${y}`} x1={0} x2={map.width} y1={y} y2={y} />);
  }

  return (
    <div className="flex h-full min-h-[620px] flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap gap-1.5">
          {toolMeta.map((item) => {
            const Icon = item.icon;
            return (
              <Button
                key={item.tool}
                variant={tool === item.tool ? "default" : "secondary"}
                onClick={() => setTool(item.tool)}
              >
                <Icon size={15} />
                {item.label}
              </Button>
            );
          })}
        </div>
        <div className="flex items-center gap-2">
          <select
            className="h-9 rounded-lg border border-neutral-200 bg-white px-3 text-sm outline-none focus:border-neutral-400"
            value={activePathGroup?.id ?? ""}
            onChange={(event) => setSelectedPathGroupId(event.currentTarget.value)}
          >
            {pathGroups.map((group) => (
              <option key={group.id} value={group.id}>
                {group.name}
              </option>
            ))}
          </select>
          <Button variant="secondary" onClick={handleCreatePathGroup}>
            <Route size={15} />
            新建路径组
          </Button>
          <Button variant={showGrid ? "secondary" : "ghost"} onClick={() => setShowGrid(!showGrid)}>
            网格
          </Button>
          <Button variant={snapToGrid ? "secondary" : "ghost"} onClick={() => setSnapToGrid(!snapToGrid)}>
            吸附
          </Button>
          <Button variant="danger" disabled={!selected} onClick={handleDeleteSelected}>
            <Trash2 size={15} />
            删除
          </Button>
        </div>
      </div>

      <div className="grid flex-1 gap-3 xl:grid-cols-[1fr_280px]">
        <div className="relative overflow-hidden rounded-xl border border-neutral-200 bg-neutral-50">
          <svg
            viewBox={`0 0 ${map.width} ${map.height}`}
            className={cn(
              "h-full min-h-[560px] w-full cursor-crosshair bg-white",
              tool === "select" && "cursor-default"
            )}
            onPointerMove={handleCanvasPointerMove}
            onPointerDown={handleCanvasPointerDown}
            onPointerUp={() => setDragId(null)}
            onPointerLeave={() => setDragId(null)}
          >
            <rect x={0} y={0} width={map.width} height={map.height} fill="#fff" />
            {showGrid && (
              <g stroke="#e5e7eb" strokeWidth="0.8" pointerEvents="none">
                {gridLines}
              </g>
            )}
            <g stroke="#9ca3af" strokeWidth="2" pointerEvents="none">
              <line x1={0} y1={0} x2={map.width} y2={0} />
              <line x1={0} y1={0} x2={0} y2={map.height} />
            </g>
            <g fill="#6b7280" fontSize="13" pointerEvents="none">
              <text x={8} y={18}>Y</text>
              <text x={map.width - 22} y={18}>X</text>
              <text x={8} y={map.height - 10}>0, {map.height}</text>
              <text x={map.width - 78} y={map.height - 10}>{map.width}, {map.height}</text>
            </g>

            <g pointerEvents="none">
              {map.pathEdges.map((edge) => {
                const from = map.objects.find((item) => item.id === edge.from);
                const to = map.objects.find((item) => item.id === edge.to);
                if (!from || !to) {
                  return null;
                }
                const group = pathGroups.find((item) => item.id === edge.pathGroupId || item.edgeIds.includes(edge.id));
                const isActive = group?.id === activePathGroup?.id;
                return (
                  <line
                    key={edge.id}
                    x1={from.x}
                    y1={from.y}
                    x2={to.x}
                    y2={to.y}
                    stroke={group?.color ?? "#111827"}
                    strokeOpacity={isActive ? 0.9 : 0.34}
                    strokeWidth={isActive ? 5 : 3}
                  />
                );
              })}
            </g>

            {map.objects.map((item) => (
              <MapShape
                key={item.id}
                item={item}
                selected={item.id === selectedId}
                onPointerDown={(event) => {
                  event.stopPropagation();
                  onSelectedChange(item.id);
                  setDragId(item.id);
                }}
              />
            ))}

            <g pointerEvents="none">
              {robots.map((robot) => (
                <g key={robot.robotId} transform={`translate(${robot.x}, ${robot.y})`}>
                  <circle r={16} fill="#111827" opacity="0.96" />
                  <circle r={5} fill="#34d399" />
                  <text x={22} y={5} fill="#111827" fontSize="14" fontWeight="600">
                    {robot.robotId}
                  </text>
                </g>
              ))}
            </g>
          </svg>
          <div className="absolute bottom-3 left-3 rounded-lg border border-neutral-200 bg-white/90 px-3 py-2 text-xs text-neutral-600 shadow-sm backdrop-blur">
            <span className="font-medium text-neutral-950">坐标</span>{" "}
            <span className="tabular">X {cursor.x}</span>{" "}
            <span className="tabular">Y {cursor.y}</span>{" "}
            <span>{map.unit}</span>
          </div>
        </div>

        <aside className="rounded-xl border border-neutral-200 bg-white p-4">
          {activePathGroup && (
            <PathGroupInspector
              group={activePathGroup}
              robots={robots}
              edgeCount={orderedGroupEdges(map, activePathGroup).length}
              onChange={updatePathGroup}
            />
          )}
          <div className="mb-4 flex items-center justify-between">
            <div>
              <h3 className="text-sm font-semibold text-neutral-950">对象属性</h3>
              <p className="text-xs text-neutral-500">编辑结果保存为地图草稿</p>
            </div>
            <Badge tone={selected ? "blue" : "neutral"}>{selected ? selected.type : "未选择"}</Badge>
          </div>
          {selected ? (
            <ObjectInspector
              item={selected}
              onChange={(next) =>
                onMapChange({
                  ...map,
                  objects: map.objects.map((item) => (item.id === next.id ? next : item))
                })
              }
            />
          ) : (
            <div className="rounded-lg border border-dashed border-neutral-200 p-4 text-sm leading-6 text-neutral-500">
              选择地图对象查看属性。使用上方工具直接创建区域、障碍物、工位、路径点或资源点。
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}

function MapShape({
  item,
  selected,
  onPointerDown
}: {
  item: MapObject;
  selected: boolean;
  onPointerDown: (event: PointerEvent<SVGElement>) => void;
}) {
  const stroke = selected ? "#111827" : strokeColors[item.type];
  const strokeWidth = selected ? 4 : 2;

  if (item.type === "zone" || item.type === "obstacle") {
    return (
      <g data-map-object onPointerDown={onPointerDown}>
        <rect
          x={item.x - (item.width ?? 100) / 2}
          y={item.y - (item.height ?? 80) / 2}
          width={item.width ?? 100}
          height={item.height ?? 80}
          rx={8}
          fill={item.color}
          stroke={stroke}
          strokeWidth={strokeWidth}
          opacity={item.type === "zone" ? 0.68 : 0.9}
        />
        <text x={item.x + 8} y={item.y - 8} fill="#111827" fontSize="14">
          {item.name}
        </text>
      </g>
    );
  }

  if (item.type === "resourcePoint") {
    const size = item.width ?? 34;
    const points = `${item.x},${item.y - size / 2} ${item.x + size / 2},${item.y} ${item.x},${item.y + size / 2} ${item.x - size / 2},${item.y}`;
    return (
      <g data-map-object onPointerDown={onPointerDown}>
        <polygon points={points} fill={item.color} stroke={stroke} strokeWidth={strokeWidth} />
        <text x={item.x + 14} y={item.y + 5} fill="#111827" fontSize="14">
          {item.name}
        </text>
      </g>
    );
  }

  return (
    <g data-map-object onPointerDown={onPointerDown}>
      <circle
        cx={item.x}
        cy={item.y}
        r={item.radius ?? (item.type === "station" ? 20 : 7)}
        fill={item.color}
        stroke={stroke}
        strokeWidth={strokeWidth}
      />
      <text x={item.x + 14} y={item.y + 5} fill="#111827" fontSize="14">
        {item.name}
      </text>
    </g>
  );
}

function PathGroupInspector({
  group,
  robots,
  edgeCount,
  onChange
}: {
  group: PathGroup;
  robots: RobotState[];
  edgeCount: number;
  onChange: (group: PathGroup) => void;
}) {
  return (
    <div className="mb-5 rounded-lg border border-neutral-200 bg-neutral-50 p-3">
      <div className="mb-3 flex items-center justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold text-neutral-950">路径组</h3>
          <p className="text-xs text-neutral-500">{edgeCount} 段路径边</p>
        </div>
        <Badge tone={group.status === "active" ? "green" : group.status === "blocked" ? "red" : "neutral"}>
          {group.status}
        </Badge>
      </div>
      <div className="grid gap-3">
        <label className="block text-xs font-medium text-neutral-500">
          名称
          <input
            className="mt-1 h-9 w-full rounded-lg border border-neutral-200 bg-white px-3 text-sm text-neutral-950 outline-none focus:border-neutral-400"
            value={group.name}
            onChange={(event) => onChange({ ...group, name: event.currentTarget.value })}
          />
        </label>
        <div className="grid grid-cols-2 gap-2">
          <label className="block text-xs font-medium text-neutral-500">
            颜色
            <input
              className="mt-1 h-9 w-full rounded-lg border border-neutral-200 bg-white px-2 text-sm text-neutral-950 outline-none focus:border-neutral-400"
              type="color"
              value={group.color}
              onChange={(event) => onChange({ ...group, color: event.currentTarget.value })}
            />
          </label>
          <label className="block text-xs font-medium text-neutral-500">
            状态
            <select
              className="mt-1 h-9 w-full rounded-lg border border-neutral-200 bg-white px-3 text-sm text-neutral-950 outline-none focus:border-neutral-400"
              value={group.status}
              onChange={(event) => onChange({ ...group, status: event.currentTarget.value as PathGroup["status"] })}
            >
              <option value="active">active</option>
              <option value="disabled">disabled</option>
              <option value="blocked">blocked</option>
            </select>
          </label>
        </div>
        <label className="block text-xs font-medium text-neutral-500">
          绑定机器人
          <select
            className="mt-1 h-24 w-full rounded-lg border border-neutral-200 bg-white px-3 py-2 text-sm text-neutral-950 outline-none focus:border-neutral-400"
            multiple
            value={group.allowedRobotCodes}
            onChange={(event) =>
              onChange({
                ...group,
                allowedRobotCodes: Array.from(event.currentTarget.selectedOptions).map((option) => option.value)
              })
            }
          >
            {robots.map((robot) => (
              <option key={robot.robotId} value={robot.robotId}>
                {robot.robotId}
              </option>
            ))}
          </select>
        </label>
      </div>
    </div>
  );
}

function ObjectInspector({
  item,
  onChange
}: {
  item: MapObject;
  onChange: (item: MapObject) => void;
}) {
  return (
    <div className="space-y-3">
      <label className="block text-xs font-medium text-neutral-500">
        名称
        <input
          className="mt-1 h-9 w-full rounded-lg border border-neutral-200 px-3 text-sm text-neutral-950 outline-none focus:border-neutral-400"
          value={item.name}
          onChange={(event) => onChange({ ...item, name: event.currentTarget.value })}
        />
      </label>
      <div className="grid grid-cols-2 gap-2">
        <NumberInput label="X" value={item.x} onChange={(x) => onChange({ ...item, x })} />
        <NumberInput label="Y" value={item.y} onChange={(y) => onChange({ ...item, y })} />
      </div>
      {(item.type === "zone" || item.type === "obstacle") && (
        <div className="grid grid-cols-2 gap-2">
          <NumberInput label="宽度" value={item.width ?? 100} onChange={(width) => onChange({ ...item, width })} />
          <NumberInput label="高度" value={item.height ?? 80} onChange={(height) => onChange({ ...item, height })} />
        </div>
      )}
      <div className="rounded-lg bg-neutral-50 p-3 text-xs leading-5 text-neutral-500">
        发布地图配置前，平台后端会校验坐标范围、路径拓扑、对象引用和资源约束。
      </div>
    </div>
  );
}

function NumberInput({
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
