import { useId, useRef, useState, type PointerEvent, type ReactNode } from "react";
import type { ExtraProps } from "react-markdown";
import { CopyableCode } from "./ChatMedia";
import { WorkspaceDialog } from "./WorkspaceDialog";
import type { ChatProduct } from "./types";
import "./rich-chat-response.css";

type ChartSeries = { name: string; values: number[] };
type Chart = { title: string; labels: string[]; series: ChartSeries[]; unit?: string };
type FlowNode = { id: string; label: string };
type FlowEdge = { from: string; to: string };
type Flowchart = { title: string; nodes: FlowNode[]; edges: FlowEdge[] };
type TextDocument = { title: string; text: string };
type RichResponse =
  | { kind: "chart"; data: Chart }
  | { kind: "flowchart"; data: Flowchart }
  | { kind: "text"; data: TextDocument };

const MAX_PAYLOAD_LENGTH = 32_768;
const SERIES_COLORS = ["#438bff", "#12a981", "#cf7432", "#a479ef", "#db537b", "#4d98a3"];
const numberFormat = new Intl.NumberFormat("ru-RU", { maximumSignificantDigits: 8 });
const productDateFormat = new Intl.DateTimeFormat("ru-RU", { dateStyle: "short", timeStyle: "short" });
const intl = Intl as typeof Intl & { supportedValuesOf?: (key: "currency") => string[] };
const currencies = intl.supportedValuesOf?.("currency");
const knownCurrencies = currencies ? new Set(currencies) : null;

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isText(value: unknown, limit: number): value is string {
  return typeof value === "string" && value.length <= limit && Boolean(value.trim());
}

function isChartSeries(value: unknown, pointCount: number): value is ChartSeries {
  return isRecord(value) && isText(value.name, 70) && Array.isArray(value.values) &&
    value.values.length === pointCount && value.values.every((point) =>
      typeof point === "number" && Number.isFinite(point) && Math.abs(point) <= 1e12
    );
}

function isFlowNode(value: unknown): value is FlowNode {
  return isRecord(value) && typeof value.id === "string" &&
    /^[a-zA-Z0-9_-]{1,32}$/.test(value.id) && isText(value.label, 70);
}

function isFlowEdge(value: unknown, ids: Set<string>): value is FlowEdge {
  return isRecord(value) && typeof value.from === "string" && ids.has(value.from) &&
    typeof value.to === "string" && ids.has(value.to);
}

function parseResponse(kind: string, source: string): RichResponse | null {
  if (source.length > MAX_PAYLOAD_LENGTH) return null;
  let value: unknown;
  try { value = JSON.parse(source); } catch { return null; }
  if (!isRecord(value) || !isText(value.title, 100)) return null;
  const title = value.title.trim();
  if (kind === "chart") {
    const { labels, series, unit } = value;
    if (!Array.isArray(labels) || labels.length < 1 || labels.length > 80 ||
      !labels.every((label): label is string => isText(label, 60))) return null;
    if (!Array.isArray(series) || series.length < 1 || series.length > 6 ||
      !series.every((entry): entry is ChartSeries => isChartSeries(entry, labels.length))) return null;
    if (unit !== undefined && !isText(unit, 20)) return null;
    return { kind, data: { title, labels, series, unit } };
  }
  if (kind === "flowchart") {
    const { nodes, edges } = value;
    if (!Array.isArray(nodes) || nodes.length < 1 || nodes.length > 18 || !nodes.every(isFlowNode)) return null;
    const ids = new Set(nodes.map((node) => node.id));
    if (ids.size !== nodes.length || !Array.isArray(edges) || edges.length > 36 ||
      !edges.every((edge): edge is FlowEdge => isFlowEdge(edge, ids))) return null;
    return { kind, data: { title, nodes, edges } };
  }
  if (kind === "text" && isText(value.text, 20_000)) return { kind, data: { title, text: value.text } };
  return null;
}

export function RichResponseBlock({ node, children }: ExtraProps & { children?: ReactNode }) {
  const code = node?.children[0];
  const kind = code?.type === "element" && code.tagName === "code" &&
    Array.isArray(code.properties.className) && code.properties.className.find((item) =>
      typeof item === "string" && /^language-avelin-(chart|flowchart|text)$/.test(item)
    );
  const source = code?.type === "element" && code.children[0]?.type === "text" ? code.children[0].value : "";
  const response = typeof kind === "string" ? parseResponse(kind.slice("language-avelin-".length), source) : null;
  if (response?.kind === "chart") return <ChartResponse data={response.data} />;
  if (response?.kind === "flowchart") return <FlowchartResponse data={response.data} />;
  if (response?.kind === "text") return <TextResponse data={response.data} />;
  return <CopyableCode>{children}</CopyableCode>;
}

function displayProduct(value: unknown) {
  if (!isRecord(value) || !isText(value.title, 180) || typeof value.price !== "string" ||
    !/^\d{1,12}(?:\.\d{1,4})?$/.test(value.price) || typeof value.currency !== "string" ||
    !/^[A-Z]{3}$/.test(value.currency) || (knownCurrencies && !knownCurrencies.has(value.currency)) ||
    !isText(value.url, 2048) || !/^https?:\/\/\S+$/i.test(value.url) || !isText(value.checked_at, 40) ||
    !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)$/.test(value.checked_at)) return null;
  const checked = new Date(value.checked_at);
  if (!Number.isFinite(checked.getTime())) return null;
  let url: URL;
  try { url = new URL(value.url); } catch { return null; }
  if (url.username || url.password || !url.hostname || !["https:", "http:"].includes(url.protocol)) return null;
  const price = new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency: value.currency,
    maximumFractionDigits: Math.max(2, value.price.split(".")[1]?.length ?? 0),
  }).format(Number(value.price));
  return { title: value.title, price, url, checked };
}

export function ChatProducts({ products }: { products: ChatProduct[] }) {
  if (!Array.isArray(products)) return null;
  const items = products.slice(0, 12).map(displayProduct).filter((item) => item !== null).slice(0, 6);
  if (!items.length) return null;
  return <section className="rich-products" aria-label="Найденные товары">
    {items.map((item, index) => <article className="rich-response rich-product" key={index}>
      <h3>{item.title}</h3>
      <strong className="rich-product-price">{item.price}</strong>
      <span className="rich-product-checked">
        Цена проверена <time dateTime={item.checked.toISOString()}>{productDateFormat.format(item.checked)}</time>
      </span>
      <a href={item.url.href} target="_blank" rel="noopener noreferrer">На сайте {item.url.hostname} ↗</a>
    </article>)}
  </section>;
}

function ChartResponse({ data }: { data: Chart }) {
  const [selected, setSelected] = useState(0);
  const titleId = useId();
  const index = Math.min(selected, data.labels.length - 1);
  const width = 600;
  const height = 240;
  const left = 80;
  const right = 584;
  const top = 18;
  const bottom = 214;
  const values = data.series.flatMap((series) => series.values);
  const minValue = Math.min(...values);
  const maxValue = Math.max(...values);
  const pad = minValue === maxValue ? Math.max(1, Math.abs(minValue) * 0.05) : 0;
  const min = minValue - pad;
  const max = maxValue + pad;
  const x = (point: number) => data.labels.length === 1 ? (left + right) / 2 :
    left + (right - left) * point / (data.labels.length - 1);
  const y = (value: number) => bottom - (bottom - top) * (value - min) / (max - min);
  const ticks = Array.from({ length: 5 }, (_, tick) => min + (max - min) * tick / 4);
  const format = (value: number) => [numberFormat.format(value), data.unit].filter(Boolean).join(" ");
  const axisLabel = (label: string) => label.length <= 20 ? label : label.slice(0, 19) + "…";
  function choosePoint(event: PointerEvent<SVGSVGElement>) {
    const bounds = event.currentTarget.getBoundingClientRect();
    if (bounds.width === 0) return;
    const chartX = (event.clientX - bounds.left) * width / bounds.width;
    const nearest = Math.round((chartX - left) / (right - left) * (data.labels.length - 1));
    setSelected(Math.max(0, Math.min(data.labels.length - 1, nearest)));
  }

  return <section className="rich-response rich-chart" aria-labelledby={titleId}>
    <h3 id={titleId}>{data.title}</h3>
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`Линейный график. ${data.title}`}
      onPointerMove={(event) => { if (event.pointerType === "mouse") choosePoint(event); }}
      onPointerDown={choosePoint}>
      {ticks.map((tick, i) => <g key={i}>
        <line className="rich-chart-grid" x1={left} y1={y(tick)} x2={right} y2={y(tick)} />
        <text x={left - 8} y={y(tick)} textAnchor="end" dominantBaseline="central">{numberFormat.format(tick)}</text>
      </g>)}
      <line className="rich-chart-guide" x1={x(index)} y1={top} x2={x(index)} y2={bottom} />
      {data.series.map((series, seriesIndex) => <g key={seriesIndex} style={{ color: SERIES_COLORS[seriesIndex] }}>
        <polyline points={series.values.map((value, point) => `${x(point)},${y(value)}`).join(" ")}
          fill="none" stroke="currentColor" strokeWidth={2.5} vectorEffect="non-scaling-stroke" />
        <circle cx={x(index)} cy={y(series.values[index])} r={5} fill="currentColor" />
      </g>)}
      <text x={x(0)} y={height - 5} textAnchor={data.labels.length === 1 ? "middle" : "start"}>{axisLabel(data.labels[0])}</text>
      {data.labels.length > 1 ? <text x={right} y={height - 5} textAnchor="end">{axisLabel(data.labels[data.labels.length - 1])}</text> : null}
    </svg>
    {data.labels.length > 1 ? <input type="range" aria-label="Точка графика"
      aria-valuetext={data.labels[index]} min={0} max={data.labels.length - 1} value={index}
      onChange={(event) => setSelected(event.currentTarget.valueAsNumber)} /> : null}
    <output className="rich-chart-selection" aria-label="Значения выбранной точки">
      <strong>{data.labels[index]}</strong>
      <ul>{data.series.map((series, seriesIndex) => <li key={seriesIndex}>
        <i aria-hidden="true" style={{ background: SERIES_COLORS[seriesIndex] }} />
        <span>{series.name}</span> <b>{format(series.values[index])}</b>
      </li>)}</ul>
    </output>
    <details>
      <summary>Данные графика</summary>
      <div className="rich-response-scroll" role="region" aria-label="Таблица значений графика" tabIndex={0}>
        <table>
          <thead><tr><th scope="col">Точка</th>{data.series.map((series, i) => <th key={i} scope="col">{series.name}</th>)}</tr></thead>
          <tbody>{data.labels.map((label, index) => <tr key={index}><th scope="row">{label}</th>
            {data.series.map((series, i) => <td key={i}>{format(series.values[index])}</td>)}
          </tr>)}</tbody>
        </table>
      </div>
    </details>
  </section>;
}

const NODE_WIDTH = 185;
const NODE_HEIGHT = 76;
const COLUMN_GAP = 62;
const ROW_GAP = 30;
const FLOW_MARGIN = 44;
type Point = { x: number; y: number };

function flowPositions(nodes: FlowNode[], edges: FlowEdge[]) {
  const incoming = new Set(edges.map((edge) => edge.to));
  const levels = new Map<string, number>();
  const roots = nodes.filter((node) => !incoming.has(node.id)).map((node) => node.id);
  for (const root of [...roots, ...nodes.map((node) => node.id)]) {
    if (levels.has(root)) continue;
    levels.set(root, 0);
    const queue = [root];
    for (const current of queue) {
      for (const edge of edges) {
        if (edge.from !== current || levels.has(edge.to) || roots.includes(edge.to)) continue;
        levels.set(edge.to, (levels.get(current) ?? 0) + 1);
        queue.push(edge.to);
      }
    }
  }
  const rows = new Map<number, number>();
  const positions = new Map<string, Point>();
  for (const node of nodes) {
    const level = levels.get(node.id) ?? 0;
    const row = rows.get(level) ?? 0;
    positions.set(node.id, {
      x: FLOW_MARGIN + level * (NODE_WIDTH + COLUMN_GAP),
      y: FLOW_MARGIN + row * (NODE_HEIGHT + ROW_GAP),
    });
    rows.set(level, row + 1);
  }
  return {
    positions,
    width: FLOW_MARGIN * 2 + (Math.max(...rows.keys()) + 1) * (NODE_WIDTH + COLUMN_GAP) - COLUMN_GAP,
    height: FLOW_MARGIN * 2 + Math.max(...rows.values()) * (NODE_HEIGHT + ROW_GAP) - ROW_GAP,
  };
}

function edgePath(from: Point, to: Point): string {
  if (from.x < to.x) {
    const startX = from.x + NODE_WIDTH;
    const middleX = (startX + to.x) / 2;
    const startY = from.y + NODE_HEIGHT / 2;
    const endY = to.y + NODE_HEIGHT / 2;
    return `M ${startX} ${startY} C ${middleX} ${startY} ${middleX} ${endY} ${to.x - 4} ${endY}`;
  }
  const startX = from.x + NODE_WIDTH / 2 + 18;
  const endX = to.x + NODE_WIDTH / 2 - 18;
  const roof = Math.min(from.y, to.y) - 32;
  return `M ${startX} ${from.y} C ${startX} ${roof} ${endX} ${roof} ${endX} ${to.y - 4}`;
}

function FlowchartResponse({ data }: { data: Flowchart }) {
  const id = useId();
  const { positions, width, height } = flowPositions(data.nodes, data.edges);
  const nodeLabels = new Map(data.nodes.map((node) => [node.id, node.label]));
  return <section className="rich-response rich-flowchart" aria-labelledby={id}>
    <h3 id={id}>{data.title}</h3>
    <div className="rich-response-scroll" role="region" aria-label="Блок-схема, прокрутка" tabIndex={0}>
      <svg viewBox={`0 0 ${width} ${height}`} width={width} height={height} role="img" aria-label={data.title}>
        <defs><marker id={`${id}-arrow`} viewBox="0 0 10 10" refX={8} refY={5} markerWidth={7} markerHeight={7} orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" className="rich-flowchart-arrow" />
        </marker></defs>
        {data.edges.map((edge, index) => <path key={index} className="rich-flowchart-edge"
          d={edgePath(positions.get(edge.from)!, positions.get(edge.to)!)} markerEnd={`url(#${id}-arrow)`} />)}
        {data.nodes.map((node) => {
          const point = positions.get(node.id)!;
          return <g key={node.id} transform={`translate(${point.x} ${point.y})`}>
            <rect width={NODE_WIDTH} height={NODE_HEIGHT} rx={12} />
            <foreignObject x={8} y={6} width={NODE_WIDTH - 16} height={NODE_HEIGHT - 12}>
              <div className="rich-flowchart-node-label">{node.label}</div>
            </foreignObject>
          </g>;
        })}
      </svg>
    </div>
    <details><summary>Узлы и связи</summary>
      <ul>{data.nodes.map((node) => <li key={node.id}>{node.label}</li>)}</ul>
      {data.edges.length ? <ol>{data.edges.map((edge, index) =>
        <li key={index}>{nodeLabels.get(edge.from)} → {nodeLabels.get(edge.to)}</li>)}</ol> : null}
    </details>
  </section>;
}

function TextResponse({ data }: { data: TextDocument }) {
  const [open, setOpen] = useState(false);
  const [copyStatus, setCopyStatus] = useState("");
  const textArea = useRef<HTMLTextAreaElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  function close() {
    setOpen(false);
    window.requestAnimationFrame(() => trigger.current?.focus({ preventScroll: true }));
  }
  async function copy() {
    try {
      await navigator.clipboard.writeText(data.text);
      setCopyStatus("Текст скопирован");
    } catch {
      textArea.current?.focus();
      textArea.current?.select();
      setCopyStatus("Текст выделен для копирования");
    }
  }
  return <section className="rich-response rich-text">
    <h3>{data.title}</h3>
    <p className="rich-text-preview">{data.text.length <= 160 ? data.text : data.text.slice(0, 160).trimEnd() + "…"}</p>
    <button className="ghost-button" type="button" ref={trigger} onClick={() => { setCopyStatus(""); setOpen(true); }}>Открыть текст</button>
    {open ? <WorkspaceDialog open title={data.title} onClose={close}>
      <textarea className="rich-text-document" ref={textArea} readOnly value={data.text} aria-label="Сгенерированный текст" />
      <div className="rich-text-actions">
        <button className="ghost-button" type="button" onClick={() => void copy()}>Копировать текст</button>
        <span role="status">{copyStatus}</span>
      </div>
    </WorkspaceDialog> : null}
  </section>;
}
