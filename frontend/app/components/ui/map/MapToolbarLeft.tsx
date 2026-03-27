"use client";

import type { MapTool, MapToolbarLeftProps } from "@/lib/types/map";

const tools: { key: MapTool; icon: string; label: string }[] = [
  { key: "point", icon: "●", label: "포인트" },
  { key: "jackPoint", icon: "⚑", label: "작업 포인트" },
  { key: "virtualwall", icon: "▯", label: "가상벽" },
  { key: "del", icon: "✕", label: "삭제" },
];

export function MapToolbarLeft({ activeTool, onToolChange }: MapToolbarLeftProps) {
  return (
    <div className="map-toolbar-left">
      {tools.map((tool) => (
        <button
          key={tool.key}
          className={
            activeTool === tool.key
              ? "map-toolbar-left__item map-toolbar-left__item--active"
              : "map-toolbar-left__item"
          }
          onClick={() => onToolChange(tool.key)}
          title={tool.label}
        >
          <span className="map-toolbar-left__icon">{tool.icon}</span>
          <span className="map-toolbar-left__label">{tool.label}</span>
        </button>
      ))}
    </div>
  );
}
