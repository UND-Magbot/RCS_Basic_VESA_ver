"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { Modal } from "./Modal";
import type { RobotDeviceInfoProps } from "@/lib/types/robots";
import { useAlert } from "@/lib/context/AlertContext";
import "./RobotDeviceInfo.css";

function formatValue(value: string | number | null | undefined): string {
  if (value == null || value === "") return "-";
  return String(value);
}

const MODEL_NAME_MAP: Record<string, string> = {
  "餐厅": "식당용",
  "酒店": "호텔용",
  "配送": "배송용",
  "清洁": "청소용",
  "巡检": "순찰용",
  "仓库": "창고용",
};

function translateModel(model: string | null | undefined): string {
  if (!model) return "-";
  return MODEL_NAME_MAP[model] ?? model;
}

export function RobotDeviceInfo({
  device,
  onClose,
  onEnableToggle,
  togglingDeviceId,
  showChargingStation = true,
  readOnly = false,
}: RobotDeviceInfoProps) {
  const [initialMinBattery, setInitialMinBattery] = useState<number | null>(null);
  const [minBattery, setMinBattery] = useState(20);
  const [chargingPois, setChargingPois] = useState<{ id: number; name: string }[]>([]);
  const [initialChargingId, setInitialChargingId] = useState<number | null>(null);
  const [chargingId, setChargingId] = useState<number | null>(null);
  const [standbyPois, setStandbyPois] = useState<{ id: number; name: string }[]>([]);
  const [initialStandbyId, setInitialStandbyId] = useState<number | null>(null);
  const [standbyId, setStandbyId] = useState<number | null>(null);
  const [isApplying, setIsApplying] = useState(false);
  const [robotSpeed, setRobotSpeed] = useState(1.2);
  const [initialSpeed, setInitialSpeed] = useState(1.2);
  const [robotType, setRobotType] = useState<string>("lifting");
  const [initialRobotType, setInitialRobotType] = useState<string>("lifting");
  const [poiDropdownOpen, setPoiDropdownOpen] = useState(false);
  const [poiDropdownPos, setPoiDropdownPos] = useState<{ top: number; left: number; width: number } | null>(null);
  const poiDropdownRef = useRef<HTMLDivElement>(null);
  const poiTriggerRef = useRef<HTMLButtonElement>(null);
  const { showAlert } = useAlert();

  useEffect(() => {
    if (!device) return;
    const type = (device as any).robotType || "lifting";
    setRobotType(type);
    setInitialRobotType(type);
    const controller = new AbortController();

    fetch(
      `${process.env.NEXT_PUBLIC_API_URL}/api/robots/sn/${encodeURIComponent(device.sn)}/min-battery`,
      { signal: controller.signal }
    )
      .then((res) => res.json())
      .then((data: { min_battery: number; charging_id: number | null; standby_id: number | null }) => {
        setInitialMinBattery(data.min_battery);
        setMinBattery(data.min_battery);
        setInitialChargingId(data.charging_id ?? null);
        setChargingId(data.charging_id ?? null);
        setInitialStandbyId(data.standby_id ?? null);
        setStandbyId(data.standby_id ?? null);
      })
      .catch((err) => {
        if (err.name !== "AbortError") {
          console.error("[로봇 상세] 충전 설정 조회 실패:", err);
        }
        setInitialMinBattery(20);
        setMinBattery(20);
        setInitialChargingId(null);
        setChargingId(null);
        setInitialStandbyId(null);
        setStandbyId(null);
      });

    // 로봇 속도 조회
    if (device.ip) {
      fetch(`${process.env.NEXT_PUBLIC_API_URL}/api/robots/speed/${device.ip}`)
        .then((r) => r.json())
        .then((d) => {
          const spd = d.max_forward_velocity ?? 1.2;
          setRobotSpeed(spd);
          setInitialSpeed(spd);
        })
        .catch(() => {});
    }

    return () => controller.abort();
  }, [device]);

  useEffect(() => {
    if (!device || !showChargingStation) return;
    const controller = new AbortController();

    fetch(
      `${process.env.NEXT_PUBLIC_API_URL}/api/robots/sn/${encodeURIComponent(device.sn)}/charging-pois`,
      { signal: controller.signal }
    )
      .then((res) => {
        if (!res.ok) throw new Error(String(res.status));
        return res.json();
      })
      .then((data: { id: number; name: string }[]) => {
        setChargingPois(Array.isArray(data) ? data : []);
      })
      .catch((err) => {
        if (err.name !== "AbortError") {
          console.error("[로봇 상세] 충전소 목록 조회 실패:", err);
        }
        setChargingPois([]);
      });

    return () => controller.abort();
  }, [device, showChargingStation]);

  useEffect(() => {
    if (!device) return;
    const controller = new AbortController();

    fetch(
      `${process.env.NEXT_PUBLIC_API_URL}/api/robots/sn/${encodeURIComponent(device.sn)}/standby-pois`,
      { signal: controller.signal }
    )
      .then((res) => {
        if (!res.ok) throw new Error(String(res.status));
        return res.json();
      })
      .then((data: { id: number; name: string }[]) => {
        setStandbyPois(Array.isArray(data) ? data : []);
      })
      .catch(() => {
        setStandbyPois([]);
      });

    return () => controller.abort();
  }, [device]);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (poiDropdownRef.current && !poiDropdownRef.current.contains(e.target as Node)) {
        setPoiDropdownOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const handleApply = useCallback(async () => {
    if (!device || isApplying) return;
    setIsApplying(true);
    try {
      // 충전 설정
      const res = await fetch(
        `${process.env.NEXT_PUBLIC_API_URL}/api/robots/sn/${encodeURIComponent(device.sn)}/min-battery`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ min_battery: minBattery, charging_id: chargingId, standby_id: standbyId }),
        }
      );
      if (res.ok) {
        const data: { min_battery: number; charging_id: number | null; standby_id: number | null } = await res.json();
        setInitialMinBattery(data.min_battery);
        setInitialChargingId(data.charging_id ?? null);
        setInitialStandbyId(data.standby_id ?? null);
      }
    } catch (err) {
      console.error("[로봇 상세] 충전 설정 저장 오류:", err);
    }
    // 속도 변경 (충전 설정과 독립적으로 항상 실행)
    if (device.ip) {
      try {
        await fetch(`${process.env.NEXT_PUBLIC_API_URL}/api/robots/speed/${device.ip}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ max_forward_velocity: robotSpeed }),
        });
        setInitialSpeed(robotSpeed);
      } catch (err) {
        console.error("[로봇 상세] 속도 저장 오류:", err);
      }
    }
    // 로봇 종류 저장
    if (robotType !== initialRobotType) {
      try {
        await fetch(`${process.env.NEXT_PUBLIC_API_URL}/api/robots/${device.id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ robot_type: robotType }),
        });
        setInitialRobotType(robotType);
      } catch (err) {
        console.error("[로봇 상세] 로봇 종류 저장 오류:", err);
      }
    }
    try {
      showAlert({ title: "완료", message: "설정이 저장되었습니다." });
    } catch {
    } finally {
      setIsApplying(false);
    }
  }, [device, minBattery, chargingId, standbyId, robotSpeed, robotType, initialRobotType, isApplying, showAlert]);

  if (!device) return null;

  const isToggleDisabled = togglingDeviceId === device.id;
  const isChanged =
    (initialMinBattery !== null && minBattery !== initialMinBattery) ||
    (showChargingStation && chargingId !== initialChargingId) ||
    standbyId !== initialStandbyId ||
    robotSpeed !== initialSpeed ||
    robotType !== initialRobotType;

  const shouldScrollTaskTable = device.currentTask.length > 5;

  const renderPower = () => {
    if (device.power == null) return "-";

    return (
      <span className={`robot-info__power-value${device.power <= 30 ? " robot-info__power-value--danger" : ""}`}>
        {device.power}
      </span>
    );
  };

  return (
    <Modal open onClose={onClose} title="로봇 상세 정보" width="760px">
      <div className="robot-info">
        {/* Base Information */}
        <section className="robot-info__section">
          <h3 className="robot-info__section-title">기본 정보</h3>
          <div className="robot-info__grid">
            <div className="robot-info__field">
              <span className="robot-info__label">로봇 SN</span>
              <span className="robot-info__value">
                {formatValue(device.sn)}
              </span>
            </div>
            <div className="robot-info__field">
              <span className="robot-info__label">로봇 명</span>
              <span className="robot-info__value">
                {formatValue(device.robotName)}
              </span>
            </div>
            <div className="robot-info__field">
              <span className="robot-info__label">모델</span>
              <span className="robot-info__value">
                {translateModel(device.model)}
              </span>
            </div>
            <div className="robot-info__field">
              <span className="robot-info__label">닉네임</span>
              <span className="robot-info__value">
                {formatValue(device.nickname)}
              </span>
            </div>
            <div className="robot-info__field">
              <span className="robot-info__label">소프트웨어 버전</span>
              <span className="robot-info__value">
                {formatValue(device.axbotVersion)}
              </span>
            </div>
            <div className="robot-info__field">
              <span className="robot-info__label">플랫폼</span>
              <span className="robot-info__value">
                {formatValue(device.platform)}
              </span>
            </div>
          </div>
        </section>

        {/* Operational */}
        <section className="robot-info__section">
          <h3 className="robot-info__section-title">운영 정보</h3>
          <div className="robot-info__grid">
            <div className="robot-info__field">
              <span className="robot-info__label">고객사</span>
              <span className="robot-info__value">
                UND
              </span>
            </div>
            <div className="robot-info__field">
              <span className="robot-info__label">전원</span>
              <span
                className={`robot-info__value robot-info__value--${device.online ? "online" : "offline"}`}
              >
                {device.online ? "Online" : "Offline"}
              </span>
            </div>
            <div className="robot-info__field">
              <span className="robot-info__label">운행 상태</span>
              <span className="robot-info__value">
                {formatValue(device.runState)}
              </span>
            </div>
            <div className="robot-info__field">
              <span className="robot-info__label">배터리</span>
              <span className="robot-info__value">{renderPower()}{!showChargingStation && "%"}</span>
            </div>
            <div className="robot-info__field">
              <span className="robot-info__label">Signal</span>
              <span className="robot-info__value">
                {device.signal != null ? `${device.signal}${!showChargingStation ? "%" : ""}` : "-"}
              </span>
            </div>
            {/* <div className="robot-info__field">
              <span className="robot-info__label">활성 상태</span>
              <label className="robot-info__toggle">
                <input
                  type="checkbox"
                  checked={device.enable}
                  onChange={() => onEnableToggle(device.id)}
                  disabled={isToggleDisabled}
                />
                <span className="robot-info__toggle-slider" />
              </label>
            </div> */}
          </div>

          <div className="robot-info__charging-row">
            <span className="robot-info__label">로봇 종류</span>
            <select
              value={robotType}
              disabled={readOnly}
              onChange={(e) => setRobotType(e.target.value)}
              style={{ flex: 1, padding: "6px 10px", background: "var(--bg-surface-2)", border: "1px solid var(--border-color)", borderRadius: 6, color: "var(--text-primary)" }}
            >
              <option value="lifting">리프팅 (모든 작업)</option>
              <option value="serving">서빙 (단순 이동만)</option>
            </select>
          </div>

          <div className="robot-info__battery-row">
            <span className="robot-info__label">최소 배터리</span>
            <input
              type="range"
              className="robot-info__range"
              min={0}
              max={100}
              value={minBattery}
              onChange={(e) => !readOnly && setMinBattery(Number(e.target.value))}
              disabled={readOnly}
              style={{
                background: `linear-gradient(to right, var(--color-primary) ${minBattery}%, var(--bg-surface-2) ${minBattery}%)`,
              }}
            />
            <span className="robot-info__range-value">{minBattery}%</span>
          </div>

          <div className="robot-info__battery-row">
            <span className="robot-info__label">최대 속도</span>
            <input
              type="range"
              className="robot-info__range"
              min={0.5}
              max={2.0}
              step={0.1}
              value={robotSpeed}
              onChange={(e) => !readOnly && setRobotSpeed(Number(e.target.value))}
              disabled={readOnly}
              style={{
                background: `linear-gradient(to right, var(--color-info) ${((robotSpeed - 0.5) / 1.5) * 100}%, var(--bg-surface-2) ${((robotSpeed - 0.5) / 1.5) * 100}%)`,
              }}
            />
            <span className="robot-info__range-value">{robotSpeed.toFixed(1)} m/s</span>
          </div>

          {showChargingStation && (
            <>
              <div className="robot-info__charging-row">
                <span className="robot-info__label">충전소</span>
                <select
                  className="robot-info__native-select"
                  value={chargingId ?? ""}
                  disabled={readOnly}
                  onChange={(e) => setChargingId(e.target.value ? Number(e.target.value) : null)}
                  style={{ flex: 1, padding: "6px 10px", background: "var(--bg-surface-2)", border: "1px solid var(--border-color)", borderRadius: 6, color: "var(--text-primary)" }}
                >
                  <option value="">선택 안 함</option>
                  {chargingPois.map((p) => (
                    <option key={`c-${p.id}`} value={p.id}>{p.name}</option>
                  ))}
                </select>
              </div>
              <div className="robot-info__charging-row">
                <span className="robot-info__label">랙 위치</span>
                <select
                  className="robot-info__native-select"
                  value={standbyId ?? ""}
                  disabled={readOnly}
                  onChange={(e) => setStandbyId(e.target.value ? Number(e.target.value) : null)}
                  style={{ flex: 1, padding: "6px 10px", background: "var(--bg-surface-2)", border: "1px solid var(--border-color)", borderRadius: 6, color: "var(--text-primary)" }}
                >
                  <option value="">선택 안 함</option>
                  {standbyPois.map((p) => (
                    <option key={`s-${p.id}`} value={p.id}>{p.name}</option>
                  ))}
                </select>
              </div>
            </>
          )}

          {!readOnly && (
            <>
              <button
                type="button"
                className="robot-info__apply-btn"
                style={{ display: "block", margin: "12px auto 0" }}
                disabled={!isChanged || isApplying}
                onClick={handleApply}
              >
                {isApplying ? "적용 중..." : "적용"}
              </button>

              {isChanged && (
                <p className="robot-info__warning">
                  변경된 설정은 적용 버튼을 눌러야 반영됩니다.
                </p>
              )}
            </>
          )}
        </section>

        {/* Current Task */}
        {/* <section className="robot-info__section">
          <h3 className="robot-info__section-title">진행 작업</h3>
          {device.currentTask.length === 0 ? (
            <div className="robot-info__empty">진행중인 작업이 없습니다.</div>
          ) : (
            <div
              className={`robot-info__table-wrapper${shouldScrollTaskTable ? " robot-info__table-wrapper--scroll" : ""}`}
            >
              <table className="robot-info__table">
                <thead>
                  <tr>
                    <th>작업명</th>
                    <th>작업 상태</th>
                    <th>작업 유형</th>
                    <th>작업 등록일</th>
                    <th>시작 지점</th>
                    <th>종료 지점</th>
                    <th>담당자</th>
                  </tr>
                </thead>
                <tbody>
                  {device.currentTask.map((task, idx) => (
                    <tr key={`${task.taskId}-${idx}`}>
                      <td>{task.taskId}</td>
                      <td>
                        <span
                          className={`robot-info__task-state robot-info__task-state--${task.state}`}
                        >
                          {task.state}
                        </span>
                      </td>
                      <td>{task.type}</td>
                      <td>{task.createTime}</td>
                      <td>{task.start}</td>
                      <td>{task.end}</td>
                      <td>{task.oper}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section> */}
      </div>
    </Modal>
  );
}
