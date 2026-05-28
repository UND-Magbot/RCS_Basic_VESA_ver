"use client";

import { memo } from "react";
import { Billboard, Text } from "@react-three/drei";
import type { PoiMarkerData } from "@/lib/types/map-markers";
import { mapPixelToWorld } from "./mapCoords";
import { ChargingStation3D } from "./ChargingStation3D";

type Props = {
  poi: PoiMarkerData;
  imgW: number;
  imgH: number;
};

function PoiMarker3DInner({ poi, imgW, imgH }: Props) {
  const [x, , z] = mapPixelToWorld(poi.position, imgW, imgH);
  const isCharging = poi.type === "charging";
  const isJack = poi.type === "jack";
  const renderKind = poi.renderKind ?? (isCharging ? "circle" : "triangle");
  const lockedBy = poi.lockedByRobot;

  return (
    <group position={[x, 0, z]}>
      {lockedBy && <LockRing />}
      {isJack ? (
        /* 잭킹 포인트: 보라색 박스 (실제 랙 크기) */
        (() => {
          const rw = poi.rackWidthPx ?? 13;
          const rd = poi.rackDepthPx ?? 14;
          const legH = rw * 0.8;
          const legOff = 0.8;
          return (
            <group rotation={[0, (poi.angle ?? 0) + Math.PI / 2, 0]}>
              {/* 랙 상판 */}
              <mesh position={[0, legH, 0]}>
                <boxGeometry args={[rw, 1.5, rd]} />
                <meshStandardMaterial
                  color="#a855f7"
                  emissive="#a855f7"
                  emissiveIntensity={0.3}
                  metalness={0.4}
                  roughness={0.6}
                />
              </mesh>
              {/* 4개 다리 */}
              {[
                [-(rw/2 - legOff), -(rd/2 - legOff)],
                [(rw/2 - legOff), -(rd/2 - legOff)],
                [(rw/2 - legOff), (rd/2 - legOff)],
                [-(rw/2 - legOff), (rd/2 - legOff)],
              ].map(([lx, lz], i) => (
                <mesh key={i} position={[lx, legH / 2, lz]}>
                  <boxGeometry args={[1.2, legH, 1.2]} />
                  <meshStandardMaterial
                    color="#7c3aed"
                    emissive="#7c3aed"
                    emissiveIntensity={0.2}
                    metalness={0.5}
                    roughness={0.5}
                  />
                </mesh>
              ))}
              {/* 하단 가이드 */}
              <mesh position={[0, 0.3, 0]}>
                <boxGeometry args={[rw * 0.9, 0.4, rd * 0.9]} />
                <meshStandardMaterial color="#a855f7" transparent opacity={0.25} />
              </mesh>
            </group>
          );
        })()
      ) : renderKind === "circle" ? (
        <group rotation={[0, poi.angle ?? 0, 0]}>
          <group position={[3, 0, 0]}>
            <ChargingStation3D />
          </group>
        </group>
      ) : (
        /* Blue cone for non-charging */
        <mesh position={[0, 7, 0]}>
          <coneGeometry args={[6, 14, 3]} />
          <meshStandardMaterial
            color="#1f6fff"
            emissive="#1f6fff"
            emissiveIntensity={0.35}
          />
        </mesh>
      )}

      {/* Angle direction indicator (충전/대기/잭킹 지점 제외) */}
      {poi.angle != null && poi.type !== "charging" && poi.type !== "workstation" && poi.type !== "jack" && (
        <AngleIndicator angle={poi.angle} />
      )}

      {/* Label */}
      <Billboard position={[0, isJack ? (poi.rackWidthPx ?? 13) + 8 : renderKind === "circle" ? 26 : 18, 0]}>
        <Text fontSize={7} color="#ffffff" anchorY="bottom">
          {poi.label}
        </Text>
        {lockedBy && (
          <Text fontSize={5} color="#ff5b5b" anchorY="top" position={[0, -0.5, 0]}>
            {`${lockedBy} 점유`}
          </Text>
        )}
      </Billboard>
    </group>
  );
}

function LockRing() {
  return (
    <mesh position={[0, 0.6, 0]} rotation={[-Math.PI / 2, 0, 0]}>
      <ringGeometry args={[10, 12, 32]} />
      <meshBasicMaterial color="#ff3b3b" transparent opacity={0.85} />
    </mesh>
  );
}

function AngleIndicator({ angle }: { angle: number }) {
  const angleRad = (angle * Math.PI) / 180;
  const len = 18;
  const endX = Math.cos(angleRad) * len;
  const endZ = -Math.sin(angleRad) * len;

  return (
    <group position={[0, 5, 0]}>
      {/* Direction line using a thin cylinder */}
      <mesh
        position={[endX / 2, 0, endZ / 2]}
        rotation={[0, -angleRad, 0]}
      >
        <cylinderGeometry args={[0.8, 0.8, len, 4]} />
        <meshBasicMaterial color="#ffc83c" transparent opacity={0.9} />
      </mesh>
      {/* Arrowhead */}
      <mesh
        position={[endX, 0, endZ]}
        rotation={[0, -angleRad, -Math.PI / 2]}
      >
        <coneGeometry args={[2.5, 6, 4]} />
        <meshBasicMaterial color="#ffc83c" transparent opacity={0.9} />
      </mesh>
    </group>
  );
}

export const PoiMarker3D = memo(PoiMarker3DInner);
