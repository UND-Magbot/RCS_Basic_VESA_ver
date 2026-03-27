"use client";

import { useState, useRef, useEffect, useCallback } from "react";

interface WheelTimePickerProps {
  value: string; // "HH:MM"
  onChange: (value: string) => void;
  allowEmpty?: boolean;
  label?: string;
}

const ITEM_HEIGHT = 36;
const VISIBLE_ITEMS = 5;
const HALF = Math.floor(VISIBLE_ITEMS / 2);

function WheelColumn({ items, selected, onSelect }: {
  items: string[];
  selected: string;
  onSelect: (v: string) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const dragRef = useRef({ dragging: false, startY: 0, startScroll: 0 });
  const snapTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const scrollToIdx = useCallback((idx: number, smooth = true) => {
    if (!ref.current) return;
    ref.current.scrollTo({ top: idx * ITEM_HEIGHT, behavior: smooth ? "smooth" : "auto" });
  }, []);

  useEffect(() => {
    const idx = items.indexOf(selected);
    if (idx >= 0) scrollToIdx(idx, false);
  }, [selected, items, scrollToIdx]);

  const snapToNearest = useCallback(() => {
    if (!ref.current) return;
    const idx = Math.round(ref.current.scrollTop / ITEM_HEIGHT);
    const clamped = Math.max(0, Math.min(items.length - 1, idx));
    scrollToIdx(clamped);
    if (items[clamped] !== selected) {
      onSelect(items[clamped]);
    }
  }, [items, selected, onSelect, scrollToIdx]);

  const isInitScroll = useRef(true);
  useEffect(() => { isInitScroll.current = true; }, [selected]);

  const handleScroll = () => {
    if (!ref.current) return;
    if (isInitScroll.current) { isInitScroll.current = false; return; }
    if (snapTimer.current) clearTimeout(snapTimer.current);
    snapTimer.current = setTimeout(snapToNearest, 100);
    const idx = Math.round(ref.current.scrollTop / ITEM_HEIGHT);
    const clamped = Math.max(0, Math.min(items.length - 1, idx));
    if (items[clamped] !== selected) {
      onSelect(items[clamped]);
    }
  };

  // 마우스 드래그 지원
  const handleMouseDown = (e: React.MouseEvent) => {
    dragRef.current = { dragging: true, startY: e.clientY, startScroll: ref.current?.scrollTop || 0 };
    e.preventDefault();
  };
  const handleMouseMove = useCallback((e: MouseEvent) => {
    if (!dragRef.current.dragging || !ref.current) return;
    const dy = dragRef.current.startY - e.clientY;
    ref.current.scrollTop = dragRef.current.startScroll + dy;
  }, []);
  const handleMouseUp = useCallback(() => {
    if (!dragRef.current.dragging) return;
    dragRef.current.dragging = false;
    snapToNearest();
  }, [snapToNearest]);

  useEffect(() => {
    document.addEventListener("mousemove", handleMouseMove);
    document.addEventListener("mouseup", handleMouseUp);
    return () => {
      document.removeEventListener("mousemove", handleMouseMove);
      document.removeEventListener("mouseup", handleMouseUp);
    };
  }, [handleMouseMove, handleMouseUp]);

  return (
    <div className="wheel-col" style={{ height: ITEM_HEIGHT * VISIBLE_ITEMS, position: "relative" }}>
      <div className="wheel-col__highlight" style={{
        position: "absolute",
        top: HALF * ITEM_HEIGHT,
        left: 0, right: 0,
        height: ITEM_HEIGHT,
        background: "rgba(90, 143, 245, 0.15)",
        borderRadius: 6,
        pointerEvents: "none",
        zIndex: 1,
      }} />
      <div
        ref={ref}
        className="wheel-col__scroll"
        onScroll={handleScroll}
        onMouseDown={handleMouseDown}
        style={{
          height: "100%",
          overflowY: "auto",
          scrollSnapType: "y mandatory",
          paddingTop: HALF * ITEM_HEIGHT,
          paddingBottom: HALF * ITEM_HEIGHT,
          position: "relative",
          zIndex: 2,
        }}
      >
        {items.map((item) => (
          <div
            key={item}
            onClick={() => {
              onSelect(item);
              const idx = items.indexOf(item);
              scrollToIdx(idx);
            }}
            style={{
              height: ITEM_HEIGHT,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              scrollSnapAlign: "start",
              fontSize: item === selected ? 18 : 14,
              fontWeight: item === selected ? 600 : 400,
              color: item === selected ? "var(--text-primary)" : "var(--text-muted)",
              cursor: "pointer",
              transition: "all 0.15s",
            }}
          >
            {item}
          </div>
        ))}
      </div>
    </div>
  );
}

export function WheelTimePicker({ value, onChange, allowEmpty, label }: WheelTimePickerProps) {
  const [isOpen, setIsOpen] = useState(false);

  const parts = value ? value.split(":") : ["09", "00"];
  const hour = parts[0] || "09";
  const minute = parts[1] || "00";

  const hourNum = parseInt(hour);
  const isPM = hourNum >= 12;
  const displayHour = hourNum === 0 ? "12" : hourNum > 12 ? String(hourNum - 12) : String(hourNum);

  const periods = ["오전", "오후"];
  const hours = Array.from({ length: 12 }, (_, i) => String(i + 1));
  const minutes = Array.from({ length: 60 }, (_, i) => String(i).padStart(2, "0"));

  const currentPeriod = isPM ? "오후" : "오전";

  const handleChange = (period: string, h: string, m: string) => {
    let h24 = parseInt(h);
    if (period === "오후" && h24 !== 12) h24 += 12;
    if (period === "오전" && h24 === 12) h24 = 0;
    onChange(`${String(h24).padStart(2, "0")}:${m}`);
  };

  const displayText = value
    ? `${currentPeriod} ${displayHour.padStart(2, "0")}:${minute}`
    : (allowEmpty ? "종료 없음" : "선택");

  return (
    <div className="wheel-time-picker">
      {label && <span className="wheel-time-picker__label">{label}</span>}
      <button
        type="button"
        className="wheel-time-picker__trigger"
        onClick={() => setIsOpen(!isOpen)}
      >
        {displayText}
      </button>

      {isOpen && (
        <div className="wheel-time-picker__dropdown">
          {allowEmpty && (
            <button
              type="button"
              className="wheel-time-picker__empty-btn"
              onClick={() => { onChange(""); setIsOpen(false); }}
            >종료 없음</button>
          )}
          <div className="wheel-time-picker__wheels">
            <WheelColumn
              items={periods}
              selected={currentPeriod}
              onSelect={(p) => handleChange(p, displayHour, minute)}
            />
            <WheelColumn
              items={hours}
              selected={displayHour}
              onSelect={(h) => handleChange(currentPeriod, h, minute)}
            />
            <WheelColumn
              items={minutes}
              selected={minute}
              onSelect={(m) => handleChange(currentPeriod, displayHour, m)}
            />
          </div>
          <button
            type="button"
            className="wheel-time-picker__done"
            onClick={() => setIsOpen(false)}
          >확인</button>
        </div>
      )}
    </div>
  );
}
