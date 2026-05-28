"use client";

import { useState } from "react";
import type { POIEditPopupProps, POIType, RackSize } from "@/lib/types/map";

const poiTypes: { value: POIType; label: string }[] = [
  { value: "waypoint", label: "경유지" },
  { value: "jack", label: "작업 위치" },
  { value: "standby", label: "랙 위치" },
];

const rackSizes: { value: RackSize; label: string; dims: string }[] = [
  { value: "S600", label: "S600 (큰 랙)", dims: "0.83 × 0.87m" },
  { value: "S300", label: "S300 (작은 랙)", dims: "0.73 × 0.74m" },
];

export function POIEditPopup({
  poi,
  onUpdate,
  onDelete,
  onClose,
  getNextNameForType,
}: POIEditPopupProps) {
  const [name, setName] = useState(poi.name);
  const [type, setType] = useState<POIType>(poi.type);
  const [angle, setAngle] = useState(poi.angle != null ? String(poi.angle) : "");
  const [rackSize, setRackSize] = useState<RackSize>(poi.rackSize ?? "S600");

  const handleTypeChange = (newType: POIType) => {
    setType(newType);
    if (getNextNameForType) {
      setName(getNextNameForType(newType));
    }
  };

  const handleConfirm = () => {
    const parsedAngle = angle.trim() !== "" ? parseFloat(angle) : undefined;
    onUpdate(poi.id, {
      name: name.trim() || poi.name,
      type,
      angle: parsedAngle != null && !isNaN(parsedAngle) ? parsedAngle : undefined,
      rackSize: type === "standby" ? rackSize : undefined,
    });
  };

  return (
    <div className="poi-edit-overlay" onClick={onClose}>
      <div className="poi-edit-panel" onClick={(e) => e.stopPropagation()}>
        <h3 className="poi-edit-panel__title">POI 편집</h3>

        <div className="poi-edit-panel__body">
          {/* Name */}
          <div className="poi-edit-panel__field">
            <label className="poi-edit-panel__label">이름</label>
            <input
              className="poi-edit-panel__input"
              value={name}
              onChange={(e) => setName(e.target.value)}
              autoFocus
            />
          </div>

          {/* Location (read-only) */}
          <div className="poi-edit-panel__field">
            <label className="poi-edit-panel__label">위치</label>
            <input
              className="poi-edit-panel__input poi-edit-panel__input--readonly"
              value={`${poi.x.toFixed(4)}, ${poi.y.toFixed(4)}`}
              readOnly
            />
          </div>

          {/* Orientation */}
          <div className="poi-edit-panel__field">
            <label className="poi-edit-panel__label">방향 (rad)</label>
            <input
              className="poi-edit-panel__input"
              type="number"
              step="0.0001"
              placeholder="N/A"
              value={angle}
              onChange={(e) => setAngle(e.target.value)}
            />
          </div>

          {/* General Type of Points */}
          <div className="poi-edit-panel__section">
            <span className="poi-edit-panel__section-title">포인트 유형</span>
            <div className="poi-edit-panel__radio-group">
              {poiTypes.map((t) => (
                <label key={t.value} className="poi-edit-panel__radio">
                  <input
                    type="radio"
                    name="poiType"
                    value={t.value}
                    checked={type === t.value}
                    onChange={() => handleTypeChange(t.value)}
                  />
                  <span>{t.label}</span>
                </label>
              ))}
            </div>
          </div>

          {/* Rack size selector — 랙 위치(standby) 일 때만 */}
          {type === "standby" && (
            <div className="poi-edit-panel__section">
              <span className="poi-edit-panel__section-title">랙 사이즈</span>
              <div className="poi-edit-panel__radio-group">
                {rackSizes.map((r) => (
                  <label key={r.value} className="poi-edit-panel__radio">
                    <input
                      type="radio"
                      name="rackSize"
                      value={r.value}
                      checked={rackSize === r.value}
                      onChange={() => setRackSize(r.value)}
                    />
                    <span>{r.label} <span style={{ opacity: 0.6, fontSize: 12 }}>{r.dims}</span></span>
                  </label>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Actions */}
        <div className="poi-edit-panel__actions">
          <button
            className="poi-edit-panel__btn poi-edit-panel__btn--delete"
            onClick={() => onDelete(poi.id)}
          >
            삭제
          </button>
          <button
            className="poi-edit-panel__btn poi-edit-panel__btn--confirm"
            onClick={handleConfirm}
          >
            확인
          </button>
        </div>
      </div>
    </div>
  );
}
