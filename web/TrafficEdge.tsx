import React, { useLayoutEffect, useRef } from "react";
import {
  BaseEdge,
  EdgeText,
  getBezierPath,
  getStraightPath,
  useInternalNode,
  ViewportPortal,
  type Edge,
  type EdgeProps,
} from "@xyflow/react";

export type LinkActivity = {
  rx: number;
  tx: number;
  seconds: number;
  bytes_per_second: number;
};

export type TopologyEdge = Edge<{ protocol: string; curved?: boolean }>;

function RouteProtocol({ x, y, label }: { x: number; y: number; label: string }) {
  return (
    <EdgeText
      x={x}
      y={y}
      label={label}
      labelBgPadding={[6, 3]}
      labelBgBorderRadius={4}
      className="route-protocol"
      aria-hidden="true"
    />
  );
}

export function TrafficEdge(props: EdgeProps<TopologyEdge>) {
  const [path, x, y] = props.data?.curved ? getBezierPath(props) : getStraightPath(props);
  return (
    <>
      <BaseEdge
        id={props.id}
        path={path}
        style={props.style}
        interactionWidth={props.interactionWidth ?? 18}
      />
      {props.label && (
        <text x={x} y={y - 5} textAnchor="middle" className="edge-warning">
          {props.label}
        </text>
      )}
      {props.data && <RouteProtocol x={x} y={y + (props.label ? 14 : -14)} label={props.data.protocol} />}
    </>
  );
}
export const edgeTypes = { traffic: TrafficEdge };

function usePorts(id: string) {
  const node = useInternalNode(id);
  const width = node?.measured.width;
  const height = node?.measured.height;
  if (!node || !width || !height) return null;
  const { x, y } = node.internals.positionAbsolute;
  const center = x + width / 2;
  return {
    top: `${center},${y}`,
    bottom: `${center},${y + height}`,
    center,
    bottomY: y + height,
  };
}

function MovingPacket({
  guidePath,
  hops,
  onLap,
  accessMidpoint,
}: {
  guidePath: string;
  hops: string[];
  onLap?: () => void;
  accessMidpoint?: { x: number; y: number };
}) {
  const line = useRef<SVGPathElement>(null);
  const dot = useRef<SVGCircleElement>(null);
  const complete = useRef(onLap);
  useLayoutEffect(() => {
    complete.current = onLap;
  }, [onLap]);
  useLayoutEffect(() => {
    const guide = line.current,
      marker = dot.current;
    if (!guide || !marker) return;
    const length = guide.getTotalLength();
    const travel = 2800;
    const lap = travel + 350;
    function position(fraction: number) {
      const point = guide!.getPointAtLength(fraction * length);
      marker!.setAttribute("cx", String(point.x));
      marker!.setAttribute("cy", String(point.y));
    }
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      position(0.5);
      const timer = window.setInterval(() => complete.current?.(), lap);
      return () => window.clearInterval(timer);
    }
    let start = performance.now();
    let frame = 0;
    function move(now: number) {
      const elapsed = now - start;
      position(Math.min(1, elapsed / travel));
      if (elapsed >= lap) {
        start = now;
        complete.current?.();
      }
      frame = requestAnimationFrame(move);
    }
    move(start);
    return () => cancelAnimationFrame(frame);
  }, [guidePath]);
  return (
    <svg
      className="route-overlay"
      width="100%"
      height="100%"
      aria-hidden={!accessMidpoint}
    >
      <path
        ref={line}
        d={guidePath}
        fill="none"
        className={accessMidpoint ? "access-probe" : undefined}
        stroke={accessMidpoint ? undefined : "none"}
      />
      {accessMidpoint && (
        <g className="access-probe-route" tabIndex={0} role="img" aria-label="ICMP probe on the shared access network">
          <path d={guidePath} className="access-probe-hit-area" fill="none" />
          <RouteProtocol x={accessMidpoint.x} y={accessMidpoint.y - 14} label="ICMP probe" />
        </g>
      )}
      <circle
        ref={dot}
        className="packet"
        r={6}
        aria-hidden="true"
        data-path={hops.join(" ")}
        data-route={(hops.length === 2 ? hops : [hops[0], hops[2], hops[hops.length - 1]])
          .map((id) => id.split("-")[1]).join("-")}
      />
    </svg>
  );
}

export function ProbePacket({ hops, onLap }: { hops: string[]; onLap?: () => void }) {
  const [source, sourceLeaf, spine = source, targetLeaf = sourceLeaf, target] = hops;
  const from = usePorts(source);
  const first = usePorts(sourceLeaf);
  const via = usePorts(spine);
  const last = usePorts(targetLeaf);
  const to = usePorts(target || targetLeaf);
  const points = [
    from?.top,
    first?.bottom,
    first?.top,
    via?.bottom,
    last?.top,
    ...(target ? [last?.bottom, to?.top] : []),
  ];
  let guidePath: string;
  let accessMidpoint: { x: number; y: number } | undefined;
  if (hops.length === 2) {
    if (!from || !to) return null;
    const x = (from.center + to.center) / 2;
    const y = Math.max(from.bottomY, to.bottomY) + 44;
    // Hosts on the same access network send frames directly to each other.
    guidePath = `M${from.bottom} Q${x},${y} ${to.bottom}`;
    accessMidpoint = { x, y: (from.bottomY + 2 * y + to.bottomY) / 4 };
  } else {
    if (points.some((p) => !p)) return null;
    guidePath = "M" + points.join(" L");
  }
  return (
    <ViewportPortal>
      <MovingPacket guidePath={guidePath} hops={hops} onLap={onLap} accessMidpoint={accessMidpoint} />
    </ViewportPortal>
  );
}
