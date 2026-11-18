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

function MovingPacket({ path, route }: { path: string; route: string }) {
  const line = useRef<SVGPathElement>(null);
  const dot = useRef<SVGCircleElement>(null);
  useLayoutEffect(() => {
    const guide = line.current,
      marker = dot.current;
    if (!guide || !marker) return;
    const length = guide.getTotalLength();
    let frame = 0;
    function move(now: number) {
      const fraction = Math.max(0, Math.min(1, ((now % 3400) - 200) / 3000));
      const p = guide!.getPointAtLength(fraction * length);
      marker!.setAttribute("cx", String(p.x));
      marker!.setAttribute("cy", String(p.y));
      frame = requestAnimationFrame(move);
    }
    move(performance.now());
    return () => cancelAnimationFrame(frame);
  }, [path]);
  return (
    <svg
      className="route-overlay"
      width="100%"
      height="100%"
      aria-hidden="true"
    >
      <path ref={line} d={path} fill="none" stroke="none" />
      <circle ref={dot} className="packet" r={5} data-route={route} />
    </svg>
  );
}

export function ProbePacket({
  source,
  spine,
}: {
  source: number;
  spine: number;
}) {
  const from = usePorts("host-" + source);
  const first = usePorts("leaf-" + source);
  const via = usePorts("spine-" + spine);
  const last = usePorts("leaf-" + (3 - source));
  const to = usePorts("host-" + (3 - source));
  const points = [
    from?.top,
    first?.bottom,
    first?.top,
    via?.bottom,
    last?.top,
    last?.bottom,
    to?.top,
  ];
  if (points.some((p) => !p)) return null;
  const path = "M" + points.join(" L");
  return (
    <ViewportPortal>
      <MovingPacket
        path={path}
        route={source + "-" + spine + "-" + (3 - source)}
      />
    </ViewportPortal>
  );
}
