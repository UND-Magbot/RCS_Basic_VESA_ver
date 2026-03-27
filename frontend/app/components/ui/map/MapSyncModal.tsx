"use client";

import { useEffect, useState } from "react";
import { Modal } from "../Modal";
import { apiFetch } from "@/lib/api";
import type { MapSyncModalProps } from "@/lib/types/map";

type RobotItem = {
  sn: string;
  name: string;
  ip_address: string | null;
};

type SyncResult = {
  sn: string;
  name: string;
  status: "pending" | "loading" | "success" | "error";
  message?: string;
};

export function MapSyncModal({
  open,
  onClose,
  mappingId,
  mapId,
  areaName,
  onSyncComplete,
}: MapSyncModalProps) {
  const [search, setSearch] = useState("");
  const [selectedSns, setSelectedSns] = useState<Set<string>>(new Set());
  const [robots, setRobots] = useState<RobotItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [results, setResults] = useState<SyncResult[]>([]);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    setError(null);
    setResults([]);
    setSelectedSns(new Set());
    apiFetch<{ total: number; items: RobotItem[] }>("/api/map/robots")
      .then((data) => setRobots(data.items))
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

  const toggleSelect = (sn: string) => {
    setSelectedSns((prev) => {
      const next = new Set(prev);
      if (next.has(sn)) next.delete(sn);
      else next.add(sn);
      return next;
    });
  };

  const handleSelectAll = () => {
    const allFilteredSns = filtered
      .filter((r) => r.ip_address)
      .map((r) => r.sn);
    const allSelected = allFilteredSns.every((sn) => selectedSns.has(sn));
    if (allSelected) {
      setSelectedSns(new Set());
    } else {
      setSelectedSns(new Set(allFilteredSns));
    }
  };

  const handleSync = async () => {
    if (selectedSns.size === 0) return;
    setSyncing(true);
    setError(null);

    const targets = robots.filter(
      (r) => selectedSns.has(r.sn) && r.ip_address
    );

    const initialResults: SyncResult[] = targets.map((r) => ({
      sn: r.sn,
      name: r.name,
      status: "pending",
    }));
    setResults(initialResults);

    const promises = targets.map(async (robot) => {
      setResults((prev) =>
        prev.map((r) =>
          r.sn === robot.sn ? { ...r, status: "loading" as const } : r
        )
      );

      try {
        // 백엔드에서 맵 데이터 업로드 + current-map 설정 + 포즈 설정 일괄 처리
        await apiFetch(`/api/map/maps/${mapId}/sync-to-robot`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            robot_ip: robot.ip_address,
            area_name: areaName,
            method: "full",
          }),
        });

        // overlay(충전소, 가상벽, rack area) 동기화
        try {
          await apiFetch(`/api/map/maps/${mapId}/sync-overlays`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ robot_ip: robot.ip_address }),
          });
        } catch {
          // overlay 실패해도 맵 동기화는 성공
        }

        setResults((prev) =>
          prev.map((r) =>
            r.sn === robot.sn
              ? { ...r, status: "success" as const, message: "맵 업로드 + overlay 적용 완료" }
              : r
          )
        );
      } catch (err: any) {
        setResults((prev) =>
          prev.map((r) =>
            r.sn === robot.sn
              ? {
                  ...r,
                  status: "error" as const,
                  message: err.message ?? "동기화에 실패했습니다.",
                }
              : r
          )
        );
      }
    });

    await Promise.allSettled(promises);
    setSyncing(false);
    onSyncComplete?.();
  };

  const handleClose = () => {
    if (syncing) return;
    setSearch("");
    setSelectedSns(new Set());
    setError(null);
    setResults([]);
    onClose();
  };

  const hasResults = results.length > 0;

  return (
    <Modal open={open} onClose={handleClose} title="맵 동기화 (Sync)" width="460px">
      {!hasResults ? (
        <>
          <div className="robot-connect__search">
            <input
              className="input"
              placeholder="SN 또는 로봇명으로 검색..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>

          <div style={{ marginBottom: "var(--space-2)" }}>
            <button
              className="btn"
              style={{ fontSize: "12px", padding: "4px 8px" }}
              onClick={handleSelectAll}
            >
              {filtered.filter((r) => r.ip_address).every((r) => selectedSns.has(r.sn)) && filtered.length > 0
                ? "전체 해제"
                : "전체 선택"}
            </button>
            <span style={{ marginLeft: "var(--space-2)", color: "var(--text-muted)", fontSize: "13px" }}>
              {selectedSns.size}대 선택됨
            </span>
          </div>

          <div className="robot-connect__list">
            {loading ? (
              <div style={{ color: "var(--text-muted)", textAlign: "center", padding: "16px" }}>
                로봇 목록을 불러오는 중...
              </div>
            ) : filtered.length === 0 ? (
              <div style={{ color: "var(--text-muted)", textAlign: "center", padding: "16px" }}>
                검색 결과가 없습니다.
              </div>
            ) : (
              filtered.map((robot) => {
                const noIp = !robot.ip_address;
                return (
                  <button
                    key={robot.sn}
                    className={
                      selectedSns.has(robot.sn)
                        ? "robot-connect__item robot-connect__item--selected"
                        : "robot-connect__item"
                    }
                    onClick={() => !noIp && toggleSelect(robot.sn)}
                    disabled={noIp}
                    style={noIp ? { opacity: 0.5, cursor: "not-allowed" } : undefined}
                  >
                    <span className="robot-connect__item-name">
                      {selectedSns.has(robot.sn) ? "☑ " : "☐ "}
                      {robot.name}
                    </span>
                    <span className="robot-connect__item-sn">
                      {robot.sn}
                      {noIp && " (IP 미등록)"}
                    </span>
                  </button>
                );
              })
            )}
          </div>
        </>
      ) : (
        <div className="robot-connect__list">
          {results.map((r) => (
            <div
              key={r.sn}
              className="robot-connect__item"
              style={{ cursor: "default" }}
            >
              <span className="robot-connect__item-name">
                {r.status === "loading" && "⏳ "}
                {r.status === "success" && "✅ "}
                {r.status === "error" && "❌ "}
                {r.status === "pending" && "⏸ "}
                {r.name}
              </span>
              <span
                className="robot-connect__item-sn"
                style={{
                  color:
                    r.status === "success"
                      ? "var(--color-success, #4caf50)"
                      : r.status === "error"
                      ? "var(--danger, #f44336)"
                      : undefined,
                }}
              >
                {r.message ?? r.sn}
              </span>
            </div>
          ))}
        </div>
      )}

      {error && (
        <div style={{ color: "var(--danger)", fontSize: "13px", padding: "4px 0" }}>
          {error}
        </div>
      )}

      <div className="robot-connect__actions">
        <button className="btn" onClick={handleClose} disabled={syncing}>
          {hasResults ? "닫기" : "취소"}
        </button>
        {!hasResults && (
          <button
            className="btn btn--primary"
            onClick={handleSync}
            disabled={selectedSns.size === 0 || syncing}
          >
            {syncing ? "동기화 중..." : `동기화 (${selectedSns.size}대)`}
          </button>
        )}
      </div>
    </Modal>
  );
}
