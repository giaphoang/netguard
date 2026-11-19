import React, { useLayoutEffect, useRef } from "react";
import {
  BaseEdge,
  EdgeProps,
  getStraightPath,
  useInternalNode,
  ViewportPortal,
} from "@xyflow/react";

export type LinkActivity = {
  rx: number;
  tx: number;
  seconds: number;
  bytes_per_second: number;
};

export function TrafficEdge(props: EdgeProps) {
  const [path, x, y] = getStraightPath(props);
  return (
    <>
      <BaseEdge
        id={props.id}
        path={path}
        style={props.style}
        interactionWidth={18}
      />
      {props.label && (
        <text x={x} y={y - 5} textAnchor="middle" className="edge-warning">
          {props.label}
        </text>
      )}
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
  return { top: `${center},${y}`, bottom: `${center},${y + height}` };
}

function MovingPacket({ guidePath, hops }: { guidePath: string; hops: string[] }) {
  const line = useRef<SVGPathElement>(null);
  const dot = useRef<SVGCircleElement>(null);
  useLayoutEffect(() => {
    const guide = line.current,
      marker = dot.current;
    if (!guide || !marker) return;
    const length = guide.getTotalLength();
    const start = performance.now();
    let frame = 0;
    function move(now: number) {
      const fraction = Math.min(1, ((now - start) % 3400) / 3000);
      const p = guide!.getPointAtLength(fraction * length);
      marker!.setAttribute("cx", String(p.x));
      marker!.setAttribute("cy", String(p.y));
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
      aria-hidden="true"
    >
      <path ref={line} d={guidePath} fill="none" stroke="none" />
      <circle
        ref={dot}
        className="packet"
        r={5}
        data-path={hops.join(" ")}
        data-route={[hops[0], hops[2], hops[hops.length - 1]].map(id => id.split("-")[1]).join("-")}
      />
    </svg>
  );
}

export function ProbePacket({ hops }: { hops: string[] }) {
  const [source, sourceLeaf, spine, targetLeaf, target] = hops;
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
  if (points.some((p) => !p)) return null;
  return (
    <ViewportPortal>
      <MovingPacket guidePath={"M" + points.join(" L")} hops={hops} />
    </ViewportPortal>
  );
}
