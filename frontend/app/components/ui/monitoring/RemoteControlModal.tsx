"use client";

import { useState, useCallback, useRef, useEffect } from "react";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8002";

interface RemoteControlModalProps {
  robotName: string;
  robotIp: string;
  onClose: () => void;
}

export function RemoteControlModal({ robotName, robotIp, onClose }: RemoteControlModalProps) {
  const [status, setStatus] = useState("");
  const [isRemoteMode, setIsRemoteMode] = useState(false);
  const movingRef = useRef(false);
  const intervalRef = useRef<NodeJS.Timeout | null>(null);

  const showStatus = (msg: string) => {
    setStatus(msg);
    setTimeout(() => setStatus(""), 3000);
  };

  const setControlMode = useCallback(async (mode: string) => {
    try {
      const res = await fetch(`${API}/api/robots/remote/control-mode/${robotIp}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode }),
      });
      if (res.ok) {
        setIsRemoteMode(mode === "remote");
        showStatus(mode === "remote" ? "원격 모드 활성화" : "자동 모드 복귀");
      } else {
        showStatus("모드 전환 실패");
      }
    } catch {
      showStatus("연결 실패");
    }
  }, [robotIp]);

  const sendTwist = useCallback(async (lv: number, av: number) => {
    try {
      await fetch(`${API}/api/robots/remote/twist/${robotIp}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ linear_velocity: lv, angular_velocity: av }),
      });
    } catch {}
  }, [robotIp]);

  const startMove = useCallback((lv: number, av: number) => {
    movingRef.current = true;
    sendTwist(lv, av);
    intervalRef.current = setInterval(() => {
      if (movingRef.current) sendTwist(lv, av);
    }, 300);
  }, [sendTwist]);

  const stopMove = useCallback(() => {
    movingRef.current = false;
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
    sendTwist(0, 0);
  }, [sendTwist]);

  const cancelMove = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/robots/remote/cancel-move/${robotIp}`, { method: "POST" });
      showStatus(res.ok ? "이동 취소 완료" : "이동 취소 실패");
    } catch {
      showStatus("연결 실패");
    }
  }, [robotIp]);

  const jackControl = useCallback(async (action: string) => {
    try {
      const res = await fetch(`${API}/api/robots/remote/jack/${robotIp}/${action}`, { method: "POST" });
      showStatus(res.ok ? "명령 전송 완료" : "명령 실패");
    } catch {
      showStatus("연결 실패");
    }
  }, [robotIp]);

  const dockToCharger = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/robots/remote/dock/${robotIp}`, { method: "POST" });
      showStatus(res.ok ? "충전소 복귀 명령 전송" : "충전소 복귀 실패");
    } catch {
      showStatus("연결 실패");
    }
  }, [robotIp]);

  const returnToStandby = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/robots/remote/return-to-standby/${robotIp}`, { method: "POST" });
      showStatus(res.ok ? "대기장소 복귀 시작" : "대기장소 복귀 실패");
    } catch {
      showStatus("연결 실패");
    }
  }, [robotIp]);

  const relocalize = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/robots/remote/relocalize/${robotIp}`, { method: "POST" });
      showStatus(res.ok ? "시스템 재시작 중... (약 90초)" : "시스템 재시작 실패");
    } catch {
      showStatus("연결 실패");
    }
  }, [robotIp]);

  const clearDispatch = useCallback(async () => {
    if (!window.confirm("이 로봇의 실행 중인 배차 작업을 삭제할까요?\n(로봇은 움직이지 않고 작업 기록만 정리됩니다)")) return;
    try {
      const res = await fetch(`${API}/api/robots/remote/clear-dispatch/${robotIp}`, { method: "POST" });
      showStatus(res.ok ? "배차 작업을 정리했습니다" : "정리 실패");
    } catch {
      showStatus("연결 실패");
    }
  }, [robotIp]);

  useEffect(() => {
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
      // 유지하던 조종용 WebSocket 연결 정리 (유휴 연결 누적 방지)
      fetch(`${API}/api/robots/remote/twist-close/${robotIp}`, { method: "POST" }).catch(() => {});
    };
  }, [robotIp]);

  const handleClose = () => {
    if (isRemoteMode) {
      stopMove();
      setControlMode("auto");
    }
    onClose();
  };

  return (
    <div className="remote-modal-backdrop" onClick={handleClose}>
      <div className="remote-modal" onClick={(e) => e.stopPropagation()}>
        <div className="remote-modal__header">
          <h3>원격 제어 — {robotName}</h3>
          <button className="remote-modal__close" onClick={handleClose}>✕</button>
        </div>

        <div className="remote-modal__body">
          {/* 모드 전환 */}
          <div className="remote-modal__section">
            <div className="remote-modal__mode-btns">
              <button
                className={`remote-modal__mode-btn${!isRemoteMode ? " remote-modal__mode-btn--active" : ""}`}
                onClick={() => setControlMode("auto")}
                disabled={!isRemoteMode}
              >자동 모드</button>
              <button
                className={`remote-modal__mode-btn${isRemoteMode ? " remote-modal__mode-btn--active" : ""}`}
                onClick={() => setControlMode("remote")}
                disabled={isRemoteMode}
              >원격 모드</button>
            </div>
          </div>

          {!isRemoteMode ? (
            <>
              {/* 자동 모드: 이동 정지 + 충전소 복귀 */}
              <div className="remote-modal__section">
                <h4>이동 제어</h4>
                <div className="remote-modal__jack-btns">
                  <button
                    className="remote-modal__action-btn"
                    style={{ borderColor: "rgba(245,101,101,0.4)", color: "var(--color-error)" }}
                    onClick={async () => {
                      await cancelMove();
                      try {
                        await fetch(`${API}/api/robots/remote/stop-all/${robotIp}`, { method: "POST" });
                      } catch {}
                    }}
                  >작업 정지</button>
                  <button
                    className="remote-modal__action-btn"
                    style={{ borderColor: "rgba(54,223,200,0.4)", color: "var(--color-info)" }}
                    onClick={dockToCharger}
                  >충전소 복귀</button>
                  <button
                    className="remote-modal__action-btn"
                    style={{ borderColor: "rgba(100,149,237,0.4)", color: "#6495ed" }}
                    onClick={returnToStandby}
                  >대기장소 복귀</button>
                  <button
                    className="remote-modal__action-btn"
                    style={{ borderColor: "rgba(160,160,160,0.4)", color: "var(--color-text-secondary)" }}
                    onClick={relocalize}
                  >시스템 재시작</button>
                </div>
              </div>

              {/* 자동 모드: 잭 제어 */}
              <div className="remote-modal__section">
                <h4>잭 제어</h4>
                <div className="remote-modal__jack-btns">
                  <button
                    className="remote-modal__action-btn remote-modal__action-btn--up"
                    onClick={() => jackControl("jack_up")}
                  >잭 업</button>
                  <button
                    className="remote-modal__action-btn remote-modal__action-btn--down"
                    onClick={() => jackControl("jack_down")}
                  >잭 다운</button>
                </div>
              </div>
            </>
          ) : (
            <>
              {/* 원격 모드: 방향 제어 */}
              <div className="remote-modal__section">
                <h4>방향 제어</h4>
                <div className="remote-modal__dpad">
                  <div className="remote-modal__dpad-row">
                    <div className="remote-modal__dpad-spacer" />
                    <button
                      className="remote-modal__dpad-btn"
                      onMouseDown={() => startMove(0.3, 0)}
                      onMouseUp={stopMove}
                      onMouseLeave={stopMove}
                      onTouchStart={() => startMove(0.3, 0)}
                      onTouchEnd={stopMove}
                    >▲<br /><span>전진</span></button>
                    <div className="remote-modal__dpad-spacer" />
                  </div>
                  <div className="remote-modal__dpad-row">
                    <button
                      className="remote-modal__dpad-btn"
                      onMouseDown={() => startMove(0, 0.5)}
                      onMouseUp={stopMove}
                      onMouseLeave={stopMove}
                      onTouchStart={() => startMove(0, 0.5)}
                      onTouchEnd={stopMove}
                    >◀<br /><span>좌회전</span></button>
                    <button
                      className="remote-modal__dpad-btn remote-modal__dpad-btn--stop"
                      onClick={stopMove}
                    >■<br /><span>정지</span></button>
                    <button
                      className="remote-modal__dpad-btn"
                      onMouseDown={() => startMove(0, -0.5)}
                      onMouseUp={stopMove}
                      onMouseLeave={stopMove}
                      onTouchStart={() => startMove(0, -0.5)}
                      onTouchEnd={stopMove}
                    >▶<br /><span>우회전</span></button>
                  </div>
                  <div className="remote-modal__dpad-row">
                    <div className="remote-modal__dpad-spacer" />
                    <button
                      className="remote-modal__dpad-btn"
                      onMouseDown={() => startMove(-0.2, 0)}
                      onMouseUp={stopMove}
                      onMouseLeave={stopMove}
                      onTouchStart={() => startMove(-0.2, 0)}
                      onTouchEnd={stopMove}
                    >▼<br /><span>후진</span></button>
                    <div className="remote-modal__dpad-spacer" />
                  </div>
                </div>
              </div>

              {/* 원격 모드: 잭 제어 */}
              <div className="remote-modal__section">
                <h4>잭 제어</h4>
                <div className="remote-modal__jack-btns">
                  <button
                    className="remote-modal__action-btn remote-modal__action-btn--up"
                    onClick={() => jackControl("jack_up")}
                  >잭 업</button>
                  <button
                    className="remote-modal__action-btn remote-modal__action-btn--down"
                    onClick={() => jackControl("jack_down")}
                  >잭 다운</button>
                </div>
              </div>
            </>
          )}

          {/* 배차 작업 강제 정리 (로봇 이동 없음) — 자동/원격 공통 */}
          <div className="remote-modal__section">
            <h4>배차 작업</h4>
            <button
              className="remote-modal__action-btn"
              style={{ borderColor: "rgba(245,101,101,0.5)", color: "var(--color-error)", width: "100%" }}
              onClick={clearDispatch}
            >작업 강제 종료 (이동 없이 정리)</button>
          </div>
        </div>

        {status && (
          <div className="remote-modal__status">{status}</div>
        )}
      </div>
    </div>
  );
}
