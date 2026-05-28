"use client";

import { useState } from "react";
import { apiPost, apiFetch } from "@/lib/api";
import type { ConnectedRobot } from "@/lib/types/map";

type BusinessItem = {
  business_id: number;
  name: string;
};

type AreaItem = {
  area_id: number;
  name: string;
  is_main_floor?: boolean;
};

type MapTopBarProps = {
  connectedRobot: ConnectedRobot;
  onConnectClick: () => void;
  businesses: BusinessItem[];
  selectedBusiness: string;
  onBusinessChange: (value: string) => void;
  areas: AreaItem[];
  selectedArea: string;
  onAreaChange: (value: string) => void;
  onSave: () => void;
  onSync: () => void;
  onRelocalize: () => void;
  onDelete: () => void;
  onApply?: () => void;
  syncDisabled?: boolean;
  applyDisabled?: boolean;
  onBusinessCreated?: (id: number, name: string) => void;
  onAreaUpdated?: () => void;
};

export function MapTopBar({
  connectedRobot,
  onConnectClick,
  businesses,
  selectedBusiness,
  onBusinessChange,
  areas,
  selectedArea,
  onAreaChange,
  onSave,
  onSync,
  onRelocalize,
  onDelete,
  onApply,
  syncDisabled = true,
  applyDisabled = false,
  onBusinessCreated,
  onAreaUpdated,
}: MapTopBarProps) {
  const [newBusinessName, setNewBusinessName] = useState("");
  const [showBusinessInput, setShowBusinessInput] = useState(false);
  const handleBusinessSelect = (value: string) => {
    if (value === "__add__") {
      setShowBusinessInput(true);
      return;
    }
    onBusinessChange(value);
  };

  const handleCreateBusiness = async () => {
    if (!newBusinessName.trim()) return;
    try {
      const name = newBusinessName.trim();
      const res = await apiPost<{ business_id: number }>("/api/map/businesses", { name });
      setNewBusinessName("");
      setShowBusinessInput(false);
      onBusinessCreated?.(res.business_id, name);
    } catch { /* ignore */ }
  };

  return (
    <>
      <div className="map-top-bar">
        <div className="map-top-bar__left">
          <label>
            <span className="map-selector-row__label">사업장: </span>
            {showBusinessInput ? (
              <span style={{ display: "inline-flex", gap: "4px" }}>
                <input
                  className="input"
                  style={{ width: "120px", padding: "4px 8px", fontSize: "var(--font-size-sm)" }}
                  placeholder="사업장 이름"
                  value={newBusinessName}
                  onChange={(e) => setNewBusinessName(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && handleCreateBusiness()}
                  autoFocus
                />
                <button className="btn btn--primary" style={{ padding: "4px 8px", fontSize: "var(--font-size-sm)" }} onClick={handleCreateBusiness}>확인</button>
                <button className="btn btn--outline" style={{ padding: "4px 8px", fontSize: "var(--font-size-sm)" }} onClick={() => { setShowBusinessInput(false); setNewBusinessName(""); }}>취소</button>
              </span>
            ) : (
              <select
                className="map-top-bar__dropdown"
                value={selectedBusiness}
                onChange={(e) => handleBusinessSelect(e.target.value)}
              >
                <option value="">사업장 선택</option>
                {businesses.map((b) => (
                  <option key={b.business_id} value={String(b.business_id)}>
                    {b.name}
                  </option>
                ))}
                <option value="__add__">+ 새 사업장 추가</option>
              </select>
            )}
          </label>
          <label>
            <span className="map-selector-row__label">영역: </span>
            <select
              className="map-top-bar__dropdown"
              value={selectedArea}
              onChange={(e) => onAreaChange(e.target.value)}
              disabled={!selectedBusiness}
            >
              <option value="">영역 선택</option>
              {areas.map((a) => (
                <option key={a.area_id} value={String(a.area_id)}>
                  {a.name}
                </option>
              ))}
            </select>
          </label>
        </div>

        <div className="map-top-bar__center">
          {onApply && (
            <button
              className="map-top-bar__btn"
              onClick={onApply}
              disabled={applyDisabled}
              title="현재 영역의 맵을 모니터링 메인 디폴트로 등록"
            >적용</button>
          )}
          <button className="map-top-bar__btn" onClick={onSave}>저장</button>
          <button className="map-top-bar__btn" onClick={onSync} disabled={syncDisabled}>동기화</button>
          <button className="map-top-bar__btn" onClick={onRelocalize}>위치재조정</button>
          <button className="map-top-bar__btn" onClick={onDelete}>삭제</button>
        </div>

        <div className="map-top-bar__right">
          {connectedRobot ? (
            <button
              className="map-top-bar__btn map-top-bar__btn--connected"
              onClick={onConnectClick}
            >
              🤖 {connectedRobot.name}
            </button>
          ) : (
            <button
              className="map-top-bar__btn map-top-bar__btn--connect"
              onClick={onConnectClick}
            >
              로봇 연결
            </button>
          )}
        </div>
      </div>
    </>
  );
}
