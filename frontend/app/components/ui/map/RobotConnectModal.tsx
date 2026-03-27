"use client";

import { useEffect, useState } from "react";
import { Modal } from "../Modal";
import { apiFetch } from "@/lib/api";
import type { RobotConnectModalProps } from "@/lib/types/map";

type RobotItem = {
  sn: string;
  name: string;
  ip_address: string | null;
  online?: boolean;
};

export function RobotConnectModal({
  open,
  onClose,
  onConnect,
}: RobotConnectModalProps) {
  const [search, setSearch] = useState("");
  const [selectedSn, setSelectedSn] = useState<string | null>(null);
  const [robots, setRobots] = useState<RobotItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 모달이 열릴 때 로봇 목록 + 온라인 체크 동시에
  useEffect(() => {
    if (!open) return;
    setLoading(true);
    setError(null);
    Promise.all([
      apiFetch<{ total: number; items: RobotItem[] }>("/api/map/robots"),
      apiFetch<{ total: number; items: { SN: string; ONLINE: string }[] }>("/api/robots/live"),
    ])
      .then(([mapData, liveData]) => {
        const onlineSet = new Set(
          liveData.items.filter((r) => r.ONLINE === "Online").map((r) => r.SN)
        );
        setRobots(mapData.items.map((r) => ({ ...r, online: onlineSet.has(r.sn) })));
      })
      .catch((err) => setError(err.message ?? "로봇 목록을 불러오지 못했습니다."))
      .finally(() => setLoading(false));
  }, [open]);

  const filtered = search
    ? robots.filter(
        (r) =>
          r.sn.toLowerCase().includes(search.toLowerCase()) ||
          r.name.toLowerCase().includes(search.toLowerCase())
      )
    : robots;

  const handleConnect = async () => {
    if (!selectedSn) return;
    setConnecting(true);
    setError(null);
    try {
      const res = await apiFetch<{ connected: boolean; sn: string; name: string; ip_address: string }>(
        `/api/map/connect/${selectedSn}`,
        { method: "POST" }
      );
      onConnect(res.sn, res.name, res.ip_address);
      setSearch("");
      setSelectedSn(null);
    } catch (err: any) {
      setError(err.message ?? "로봇에 연결하지 못했습니다.");
    } finally {
      setConnecting(false);
    }
  };

  const handleClose = () => {
    setSearch("");
    setSelectedSn(null);
    setError(null);
    onClose();
  };

  return (
    <Modal open={open} onClose={handleClose} title="로봇 연결" width="420px">
      <div className="robot-connect__search">
        <input
          className="input"
          placeholder="SN 또는 로봇명으로 검색..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      <div className="robot-connect__list">
        {loading ? (
          <div style={{ color: "var(--text-muted)", textAlign: "center", padding: "24px", display: "flex", flexDirection: "column", alignItems: "center", gap: "12px" }}>
            <div className="robot-connect__spinner" />
            로봇 목록을 불러오는 중...
          </div>
        ) : filtered.length === 0 ? (
          <div style={{ color: "var(--text-muted)", textAlign: "center", padding: "16px" }}>
            검색 결과가 없습니다.
          </div>
        ) : (
          filtered.map((robot) => (
            <button
              key={robot.sn}
              className={
                selectedSn === robot.sn
                  ? "robot-connect__item robot-connect__item--selected"
                  : "robot-connect__item"
              }
              onClick={() => setSelectedSn(robot.sn)}
            >
              <span
                className="robot-connect__item-name"
                style={robot.online ? { color: "var(--color-success)" } : undefined}
              >
                {robot.name}
              </span>
              <span className="robot-connect__item-sn">{robot.sn}</span>
            </button>
          ))
        )}
      </div>

      {error && (
        <div style={{ color: "var(--danger)", fontSize: "13px", padding: "4px 0" }}>
          {error}
        </div>
      )}

      <div className="robot-connect__actions">
        <button className="btn" onClick={handleClose}>
          취소
        </button>
        <button
          className="btn btn--primary"
          onClick={handleConnect}
          disabled={!selectedSn || connecting}
        >
          {connecting ? "연결 중..." : "연결"}
        </button>
      </div>
    </Modal>
  );
}
