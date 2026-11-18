import React from "react";
import { BaseEdge, EdgeProps, getStraightPath } from "@xyflow/react";

export type LinkActivity = {
  rx: number;
  tx: number;
  seconds: number;
  bytes_per_second: number;
};

export function TrafficEdge(props: EdgeProps) {
  const [path, x, y] = getStraightPath(props);
  const activity = props.data?.activity as LinkActivity | undefined;
  const access = props.id.startsWith("access");
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
      {activity &&
        [
          access ? activity.tx : activity.rx,
          access ? activity.rx : activity.tx,
        ].map(
          (packets, i) =>
            packets > 0 && (
              <circle key={i} className="packet" r={3.7} pointerEvents="none">
                <animateMotion
                  path={path}
                  dur={i ? "2.1s" : "1.8s"}
                  keyPoints={i ? "1;0" : "0;1"}
                  keyTimes="0;1"
                  calcMode="linear"
                  repeatCount="indefinite"
                />
              </circle>
            ),
        )}
    </>
  );
}
export const edgeTypes = { traffic: TrafficEdge };
