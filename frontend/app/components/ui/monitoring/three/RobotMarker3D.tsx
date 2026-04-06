"use client";

import { memo } from "react";
import { Billboard, Text, RoundedBox } from "@react-three/drei";
import type { RobotMarkerData } from "@/lib/types/map-markers";
import { mapPixelToWorld } from "./mapCoords";

type Props = {
  robot: RobotMarkerData;
  imgW: number;
  imgH: number;
};

/* ── 크기 상수 (AutoXing crawler_s300 잭 AGV) ── */
const WHEEL_R = 0.6;
const WHEEL_W = 0.5;

// 하부 본체 (흰색)
const BASE_W = 8;
const BASE_H = 2.5;
const BASE_D = 10;
const BASE_BOT = WHEEL_R * 2 + 0.1;

// 잭 기둥 (본체와 플랫폼 사이, 검은색)
const JACK_PILLAR_W = 0.8;
const JACK_PILLAR_H = 1.5;
const JACK_GAP = 0.3; // 본체-플랫폼 사이 간격

// 잭 플랫폼 (상단 검은색 판)
const PLAT_W = 9;
const PLAT_H = 1.0;
const PLAT_D = 10;
const PLAT_BOT = BASE_BOT + BASE_H + JACK_GAP;

// 비상정지 버튼
const ESTOP_R = 0.4;

// 라벨
const LABEL_Y = PLAT_BOT + PLAT_H + 6;

/* ── LED 색상 ── */
function getLedColor(
  status: RobotMarkerData["status"],
  collisionState?: RobotMarkerData["collisionState"]
): string {
  if (collisionState === "collision") return "#ff5757";
  if (collisionState === "near_miss") return "#ffba24";
  if (status === "error") return "#ff5757";
  if (status === "warning") return "#ffba24";
  return "#42d8ff";
}

/* ── 컴포넌트 ── */
function RobotMarker3DInner({ robot, imgW, imgH }: Props) {
  const [x, , z] = mapPixelToWorld(robot.position, imgW, imgH);
  const { yaw, robotName, status, collisionState } = robot;

  const ledColor = getLedColor(status, collisionState);
  const rotationY = yaw + Math.PI / 2;

  return (
    <group position={[x, 0, z]}>
      {/* ── 본체 (yaw 회전) ── */}
      <group rotation={[0, rotationY, 0]}>

        {/* ========== 바퀴 ×4 ========== */}
        {(
          [
            [-3, BASE_D / 2 - 1.5],
            [3, BASE_D / 2 - 1.5],
            [-3, -(BASE_D / 2 - 1.5)],
            [3, -(BASE_D / 2 - 1.5)],
          ] as const
        ).map(([cx, cz], i) => (
          <group key={`w${i}`} position={[cx, WHEEL_R, cz]} rotation={[0, 0, Math.PI / 2]}>
            <mesh>
              <cylinderGeometry args={[WHEEL_R, WHEEL_R, WHEEL_W, 16]} />
              <meshStandardMaterial color="#2a2a2a" />
            </mesh>
            <mesh position={[0, WHEEL_W / 2 + 0.01, 0]}>
              <cylinderGeometry args={[WHEEL_R * 0.45, WHEEL_R * 0.45, 0.06, 12]} />
              <meshStandardMaterial color="#aaa" metalness={0.5} roughness={0.3} />
            </mesh>
          </group>
        ))}

        {/* ========== 하부 본체 (흰색, 둥근 모서리) ========== */}
        <RoundedBox
          args={[BASE_W, BASE_H, BASE_D]}
          radius={0.8}
          smoothness={4}
          position={[0, BASE_BOT + BASE_H / 2, 0]}
        >
          <meshStandardMaterial color="#f0f0f0" roughness={0.3} />
        </RoundedBox>

        {/* 본체 하단 다크 라인 */}
        <RoundedBox
          args={[BASE_W + 0.1, 0.25, BASE_D + 0.1]}
          radius={0.1}
          smoothness={2}
          position={[0, BASE_BOT + 0.12, 0]}
        >
          <meshStandardMaterial color="#555" />
        </RoundedBox>

        {/* 전면 USB-C 포트 */}
        <mesh position={[-2, BASE_BOT + BASE_H - 0.5, BASE_D / 2 + 0.01]}>
          <boxGeometry args={[0.8, 0.3, 0.1]} />
          <meshStandardMaterial color="#333" />
        </mesh>

        {/* 전면 LiDAR 센서 (좌/우) */}
        <mesh position={[-2.5, BASE_BOT + BASE_H / 2, BASE_D / 2 + 0.3]}>
          <sphereGeometry args={[0.25, 8, 8]} />
          <meshStandardMaterial color="#555" />
        </mesh>
        <mesh position={[2.5, BASE_BOT + BASE_H / 2, BASE_D / 2 + 0.3]}>
          <sphereGeometry args={[0.25, 8, 8]} />
          <meshStandardMaterial color="#555" />
        </mesh>

        {/* ========== 잭 기둥 (4개, 검은색) ========== */}
        {(
          [
            [-3, 3.5],
            [3, 3.5],
            [-3, -3.5],
            [3, -3.5],
          ] as const
        ).map(([px, pz], i) => (
          <RoundedBox
            key={`jp${i}`}
            args={[JACK_PILLAR_W, JACK_PILLAR_H, JACK_PILLAR_W]}
            radius={0.1}
            smoothness={2}
            position={[px, BASE_BOT + BASE_H + JACK_GAP / 2 + JACK_PILLAR_H / 2 - 0.5, pz]}
          >
            <meshStandardMaterial color="#222" />
          </RoundedBox>
        ))}

        {/* ========== 잭 플랫폼 (상단, 검은색) ========== */}
        <RoundedBox
          args={[PLAT_W, PLAT_H, PLAT_D]}
          radius={0.6}
          smoothness={4}
          position={[0, PLAT_BOT + PLAT_H / 2, 0]}
        >
          <meshStandardMaterial color="#1a1a1a" roughness={0.4} />
        </RoundedBox>

        {/* 플랫폼 상단 도트 패턴 (미끄럼 방지) */}
        {[
          [-2, -2], [-2, 0], [-2, 2],
          [0, -2], [0, 0], [0, 2],
          [2, -2], [2, 0], [2, 2],
        ].map(([dx, dz], i) => (
          <mesh key={`dot${i}`} position={[dx, PLAT_BOT + PLAT_H + 0.01, dz]}>
            <circleGeometry args={[0.15, 8]} />
            <meshStandardMaterial color="#333" />
          </mesh>
        ))}

        {/* 플랫폼 테두리 (흰색 엣지) */}
        <RoundedBox
          args={[PLAT_W + 0.2, 0.15, PLAT_D + 0.2]}
          radius={0.05}
          smoothness={2}
          position={[0, PLAT_BOT + PLAT_H + 0.05, 0]}
        >
          <meshStandardMaterial color="#ddd" />
        </RoundedBox>

        {/* ========== 비상정지 버튼 (빨간색, 전면 중앙) ========== */}
        <mesh position={[0, BASE_BOT + BASE_H + JACK_GAP / 2 + 0.3, BASE_D / 2 - 0.5]}>
          <cylinderGeometry args={[ESTOP_R, ESTOP_R * 0.9, 0.5, 16]} />
          <meshStandardMaterial color="#dd2222" roughness={0.3} />
        </mesh>
        {/* 비상정지 버튼 상단 */}
        <mesh position={[0, BASE_BOT + BASE_H + JACK_GAP / 2 + 0.56, BASE_D / 2 - 0.5]}>
          <cylinderGeometry args={[ESTOP_R * 0.7, ESTOP_R, 0.1, 16]} />
          <meshStandardMaterial color="#cc1111" roughness={0.2} />
        </mesh>

        {/* ========== LED 스트립 (후면 양쪽) ========== */}
        {[-BASE_W / 2 - 0.1, BASE_W / 2 + 0.1].map((lx, i) => (
          <RoundedBox
            key={`led${i}`}
            args={[0.2, BASE_H - 0.5, 0.2]}
            radius={0.05}
            smoothness={2}
            position={[lx, BASE_BOT + BASE_H / 2, -(BASE_D / 2 - 0.5)]}
          >
            <meshStandardMaterial
              color={ledColor}
              emissive={ledColor}
              emissiveIntensity={0.9}
              toneMapped={false}
            />
          </RoundedBox>
        ))}

      </group>

      {/* ── 라벨 ── */}
      <Billboard position={[0, LABEL_Y, 0]}>
        <Text fontSize={3} color={ledColor} anchorY="bottom">
          {robotName}
        </Text>
      </Billboard>
    </group>
  );
}

export const RobotMarker3D = memo(RobotMarker3DInner);
