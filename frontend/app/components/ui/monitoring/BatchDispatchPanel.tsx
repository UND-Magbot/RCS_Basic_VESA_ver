"use client";

import { useEffect, useMemo, useState } from "react";
import { ROBOT_TYPE_WORK_MODES, WORK_MODE_LABELS, type WorkMode, type RobotType } from "@/lib/constants/robotTypes";
import { manualRunBatch, type BatchAssignment } from "@/lib/api/tasks";
import { useAlert } from "@/lib/context/AlertContext";

const API = process.env.NEXT_PUBLIC_API_URL || "";

type LiveRobot = {
  ID: number;
  IP: string;
  SN: string;
  ROBOTNAME: string;
  ONLINE: string;
  ROBOT_TYPE?: string;
  [key: string]: any;
};
type PoiOption = { id: number; name: string; type: string };

type Row = {
  uid: string;
  robotId: number;
  workMode: WorkMode;
  pickupId: number;
  dropoffId: number;
  waitSec: number;
  repeatCount: number;     // 0 = 무한
};

function genUid() {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
}

function newRow(): Row {
  return {
    uid: genUid(),
    robotId: 0,
    workMode: "rack_pickup",
    pickupId: 0,
    dropoffId: 0,
    waitSec: 0,
    repeatCount: 1,
  };
}

type Props = {
  liveRobots: LiveRobot[];
  areaId?: number;
  busyRobotIps?: Set<string>;
};

// 공통 스타일
const labelStyle: React.CSSProperties = {
  display: "block",
  fontSize: 12,
  color: "var(--text-muted)",
  marginBottom: 4,
  fontWeight: 500,
};
const fieldStyle: React.CSSProperties = {
  display: "block",
  marginBottom: 10,
};
const inputStyle: React.CSSProperties = {
  width: "100%",
  padding: "7px 10px",
  fontSize: 13,
  background: "var(--bg-surface-1)",
  border: "1px solid var(--border-color)",
  borderRadius: 4,
  color: "var(--text-primary)",
  boxSizing: "border-box",
};

export function BatchDispatchPanel({ liveRobots, areaId, busyRobotIps }: Props) {
  const [rows, setRows] = useState<Row[]>([newRow()]);
  const [pois, setPois] = useState<PoiOption[]>([]);
  const [isStarting, setIsStarting] = useState(false);
  const { showAlert } = useAlert();

  const onlineRobots = useMemo(
    () => liveRobots.filter((r) => r.ONLINE === "Online"),
    [liveRobots]
  );
  const jackPois = useMemo(() => pois.filter((p) => p.type === "jack"), [pois]);

  useEffect(() => {
    const url = areaId ? `${API}/api/map/active-pois?area_id=${areaId}` : `${API}/api/map/active-pois`;
    fetch(url)
      .then((r) => r.json())
      .then((data) =>
        setPois(
          Array.isArray(data)
            ? data.map((p: any) => ({ id: p.id, name: p.name, type: p.poi_type || p.type }))
            : []
        )
      )
      .catch(() => {});
  }, [areaId]);

  const usedRobotIds = new Set(rows.map((r) => r.robotId).filter((id) => id > 0));

  const robotsForRow = (currentRobotId: number) =>
    onlineRobots.filter((r) => {
      if (busyRobotIps && busyRobotIps.has(r.IP)) return false;
      if (r.ID === currentRobotId) return true;
      return !usedRobotIds.has(r.ID);
    });

  const allowedWorkModesForRobot = (robotId: number): WorkMode[] => {
    const robot = onlineRobots.find((r) => r.ID === robotId);
    const type = (robot?.ROBOT_TYPE || "lifting") as RobotType;
    return ROBOT_TYPE_WORK_MODES[type] || ["simple_move"];
  };

  const updateRow = (uid: string, patch: Partial<Row>) => {
    setRows((prev) =>
      prev.map((r) => {
        if (r.uid !== uid) return r;
        const next = { ...r, ...patch };
        if (patch.robotId !== undefined) {
          const allowed = allowedWorkModesForRobot(next.robotId);
          if (!allowed.includes(next.workMode)) next.workMode = allowed[0];
        }
        return next;
      })
    );
  };

  const addRow = () => setRows((prev) => [...prev, newRow()]);
  const removeRow = (uid: string) =>
    setRows((prev) => (prev.length > 1 ? prev.filter((r) => r.uid !== uid) : prev));

  const handleRunAll = async () => {
    const valid: BatchAssignment[] = [];
    for (const r of rows) {
      if (r.robotId > 0 && r.pickupId > 0 && r.dropoffId > 0) {
        valid.push({
          robot_id: r.robotId,
          work_mode: r.workMode,
          pickup_poi_id: r.pickupId,
          dropoff_poi_id: r.dropoffId,
          wait_sec: Math.max(0, Number(r.waitSec) || 0),
          repeat_count: r.repeatCount > 0 ? r.repeatCount : null,
        });
      }
    }
    if (valid.length === 0) {
      showAlert({
        title: "안내",
        message: "유효한 어사인먼트가 없습니다.\n각 행에서 로봇·작업1·작업2를 모두 선택하세요.",
      });
      return;
    }

    setIsStarting(true);
    try {
      const res = await manualRunBatch(valid);
      const okN = res.started?.length ?? 0;
      const skipN = res.skipped?.length ?? 0;
      const skipDetail = (res.skipped ?? [])
        .map((s) => `· 로봇 #${s.robot_id}: ${s.reason}`)
        .join("\n");
      showAlert({
        title: "배차 결과",
        message: skipN > 0
          ? `시작 ${okN}대 / 건너뜀 ${skipN}대\n\n${skipDetail}`
          : `시작 ${okN}대`,
      });
      if (okN > 0) setRows([newRow()]);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "배치 실행 실패";
      showAlert({ title: "오류", message: msg });
    } finally {
      setIsStarting(false);
    }
  };

  return (
    <div style={{ padding: 14, display: "flex", flexDirection: "column", gap: 12 }}>
      {/* 헤더 */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <div style={{ fontSize: 14, fontWeight: 700, color: "var(--text-primary)" }}>
            여러 대 배차
          </div>
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
            로봇별로 작업을 추가한 뒤 전체 실행하세요
          </div>
        </div>
        <button
          onClick={addRow}
          style={{
            padding: "6px 12px",
            fontSize: 12,
            fontWeight: 600,
            background: "var(--color-primary, #36dfc8)",
            color: "white",
            border: "none",
            borderRadius: 4,
            cursor: "pointer",
            whiteSpace: "nowrap",
          }}
        >+ 작업 추가</button>
      </div>

      {/* 어사인먼트 카드들 */}
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        {rows.map((row, idx) => {
          const allowed = allowedWorkModesForRobot(row.robotId);
          const selectable = robotsForRow(row.robotId);
          return (
            <div
              key={row.uid}
              style={{
                padding: 14,
                background: "var(--bg-surface-2)",
                border: "1px solid var(--border-color)",
                borderRadius: 8,
              }}
            >
              {/* 카드 헤더 */}
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  marginBottom: 12,
                  paddingBottom: 8,
                  borderBottom: "1px solid var(--border-color)",
                }}
              >
                <span style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>
                  작업 #{idx + 1}
                </span>
                {rows.length > 1 && (
                  <button
                    onClick={() => removeRow(row.uid)}
                    style={{
                      background: "transparent",
                      border: "1px solid #ff5b5b",
                      color: "#ff5b5b",
                      cursor: "pointer",
                      fontSize: 11,
                      padding: "3px 8px",
                      borderRadius: 4,
                    }}
                  >삭제</button>
                )}
              </div>

              {/* 로봇 */}
              <div style={fieldStyle}>
                <label style={labelStyle}>로봇</label>
                <select
                  style={inputStyle}
                  value={row.robotId}
                  onChange={(e) => updateRow(row.uid, { robotId: Number(e.target.value) })}
                >
                  <option value={0}>— 로봇 선택 —</option>
                  {selectable.map((r) => (
                    <option key={r.IP} value={r.ID}>
                      {r.ROBOTNAME || r.SN}
                    </option>
                  ))}
                </select>
              </div>

              {/* 작업 종류 */}
              <div style={fieldStyle}>
                <label style={labelStyle}>작업 종류</label>
                <select
                  style={inputStyle}
                  value={row.workMode}
                  onChange={(e) => updateRow(row.uid, { workMode: e.target.value as WorkMode })}
                  disabled={row.robotId === 0}
                >
                  {allowed.map((m) => (
                    <option key={m} value={m}>{WORK_MODE_LABELS[m]}</option>
                  ))}
                </select>
              </div>

              {/* 작업 1 */}
              <div style={fieldStyle}>
                <label style={labelStyle}>
                  {row.workMode === "simple_move" ? "시작 위치" : "작업 1 위치"}
                </label>
                <select
                  style={inputStyle}
                  value={row.pickupId}
                  onChange={(e) => updateRow(row.uid, { pickupId: Number(e.target.value) })}
                >
                  <option value={0}>— 위치 선택 —</option>
                  {jackPois.map((p) => (
                    <option key={p.id} value={p.id}>{p.name}</option>
                  ))}
                </select>
              </div>

              {/* 작업 2 */}
              <div style={fieldStyle}>
                <label style={labelStyle}>
                  {row.workMode === "simple_move" ? "도착 위치" : "작업 2 위치"}
                </label>
                <select
                  style={inputStyle}
                  value={row.dropoffId}
                  onChange={(e) => updateRow(row.uid, { dropoffId: Number(e.target.value) })}
                >
                  <option value={0}>— 위치 선택 —</option>
                  {jackPois.map((p) => (
                    <option key={p.id} value={p.id}>{p.name}</option>
                  ))}
                </select>
              </div>

              {/* 대기 시간 + 반복 한 줄에 */}
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 0 }}>
                <div>
                  <label style={labelStyle}>위치 간 대기 (초)</label>
                  <input
                    type="number"
                    style={inputStyle}
                    min={0}
                    value={row.waitSec}
                    onChange={(e) => updateRow(row.uid, { waitSec: Math.max(0, Number(e.target.value) || 0) })}
                  />
                </div>
                <div>
                  <label style={labelStyle}>반복 횟수</label>
                  <input
                    type="number"
                    style={inputStyle}
                    min={0}
                    placeholder="0 = 무한"
                    value={row.repeatCount}
                    onChange={(e) => updateRow(row.uid, { repeatCount: Math.max(0, Number(e.target.value) || 0) })}
                  />
                  <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 3 }}>
                    {row.repeatCount === 0 ? "무한 반복 (중지 시까지)" : `${row.repeatCount}회 반복`}
                  </div>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* 전체 실행 */}
      <button
        onClick={handleRunAll}
        disabled={isStarting}
        style={{
          width: "100%",
          padding: "12px 14px",
          fontSize: 14,
          fontWeight: 700,
          background: isStarting ? "var(--bg-surface-3)" : "linear-gradient(135deg, #36dfc8, #2bb5a0)",
          color: "white",
          border: "none",
          borderRadius: 6,
          cursor: isStarting ? "not-allowed" : "pointer",
          marginTop: 4,
        }}
      >
        {isStarting ? "실행 중..." : `전체 실행 (${rows.length}건)`}
      </button>
    </div>
  );
}
