"use client";

import type { MapTool, MapToolbarTopProps } from "@/lib/types/map";

// 모드 토글 도구 (캔버스 클릭으로 위치 지정)
const modeTools: { key: MapTool; icon: string; label: string }[] = [
  { key: "point", icon: "●", label: "포인트" },
  { key: "jackPoint", icon: "⚑", label: "작업 포인트" },
  { key: "virtualwall", icon: "▯", label: "가상벽" },
  { key: "del", icon: "✕", label: "삭제" },
];

export function MapToolbarTop({
  onUndo,
  onFullscreen,
  isFullscreen,
  activeTool,
  onToolChange,
  onChargingPile,
  onCurrentPos,
}: MapToolbarTopProps) {
  return (
    <>
      <button
        className="map-toolbar-top__fullscreen"
        onClick={onFullscreen}
        title={isFullscreen ? "전체화면 해제" : "전체화면"}
      >
        {isFullscreen ? "⊡" : "⊞"}
      </button>

      <div className="map-toolbar-top">
        <div className="map-toolbar-top__bar">
          {/* 되돌리기 — 즉시 실행 */}
          <button
            className="map-toolbar-top__item"
            onClick={onUndo}
            title="되돌리기"
          >
            <span className="map-toolbar-top__item-icon">↩</span>
            되돌리기
          </button>

          {/* 모드 도구들 */}
          {modeTools.map((t) => (
            <button
              key={t.key}
              className={
                activeTool === t.key
                  ? "map-toolbar-top__item map-toolbar-top__item--active"
                  : "map-toolbar-top__item"
              }
              onClick={() => onToolChange(t.key)}
              title={t.label}
            >
              <span className="map-toolbar-top__item-icon">{t.icon}</span>
              {t.label}
            </button>
          ))}

          {/* 충전소 — 로봇 현재 위치에 즉시 생성 */}
          <button
            className="map-toolbar-top__item"
            onClick={onChargingPile}
            title="로봇 현재 위치에 충전소 자동 생성"
          >
            <span className="map-toolbar-top__item-icon">⚡</span>
            충전소
          </button>

          {/* 현 위치 — 로봇 현재 위치에 일반 POI 즉시 생성 */}
          <button
            className="map-toolbar-top__item"
            onClick={onCurrentPos}
            title="로봇 현재 위치에 포인트 자동 생성"
          >
            <span className="map-toolbar-top__item-icon">◎</span>
            현 위치
          </button>
        </div>
      </div>
    </>
  );
}
