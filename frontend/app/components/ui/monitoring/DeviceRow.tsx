"use client";

import { IconButton } from "../IconButton";
import type { DevicePower, DeviceRowProps, DeviceStatus } from "@/lib/types/monitoring";

const POWER_LABEL: Record<DevicePower, string> = {
  online: "온라인",
  offline: "오프라인",
};

const STATUS_LABEL: Record<DeviceStatus, string> = {
  idle: "대기",
  running: "운행 중",
  charging: "충전 중",
  error: "오류",
  warning: "경고",
  disable: "비활성",
};

export function DeviceRow({
  id,
  name,
  power,
  battery,
  status,
  ip,
  isExpanded = false,
  onToggleExpand,
  onInfo,
  onRemote,
}: DeviceRowProps) {
  const expanded = isExpanded;
  const isOffline = power === "offline";
  const isInvalidOnlineDisable = power === "online" && status === "disable";
  const effectiveStatus = isOffline ? "disable" : status;

  const canInfo = !isInvalidOnlineDisable;

  const batteryNum = parseFloat(battery);
  const isBatteryLow = Number.isFinite(batteryNum) && batteryNum <= 30;

  return (
    <div
      className={`device-row${expanded ? " device-row--expanded" : ""}`}
      onClick={() => onToggleExpand?.(id)}
    >
      <button
        type="button"
        className="device-row__summary"
        aria-expanded={expanded}
      >
        <span className="device-row__cell device-row__cell--robot">{name}</span>
        <span className={`device-row__cell device-row__cell--power power--${power}`}>{POWER_LABEL[power]}</span>
        <span className={`device-row__cell device-row__cell--battery${isBatteryLow ? " battery--danger" : ""}`}>
          {battery}
        </span>
        <span className="device-row__cell device-row__cell--status">
          <span className={`status-chip status-chip--${effectiveStatus}`}>
            {STATUS_LABEL[effectiveStatus]}
          </span>
        </span>
      </button>
      {expanded ? (
        <div
          className="device-row__actions"
          onClick={(event) => event.stopPropagation()}
        >
          <IconButton
            aria-label="Info"
            disabled={!canInfo}
            onClick={() => onInfo?.(id)}
          >
            정보
          </IconButton>
          {!isOffline && ip && (
            <IconButton
              aria-label="Remote"
              onClick={() => onRemote?.(id, ip)}
              style={{ background: "rgba(90,143,245,0.15)", color: "#5a8ff5", border: "1px solid rgba(90,143,245,0.4)" }}
            >
              원격제어
            </IconButton>
          )}
        </div>
      ) : null}
    </div>
  );
}
