"use client";

import { useState, useRef, useEffect } from "react";
import "./MonthYearPicker.css";

const MONTHS = [
  "1월", "2월", "3월", "4월", "5월", "6월",
  "7월", "8월", "9월", "10월", "11월", "12월",
];

const YEAR_RANGE = 10;

type MonthYearPickerProps = {
  year: number;
  month: number;
  onApply: (year: number, month: number) => void;
  onCancel: () => void;
};

export function MonthYearPicker({ year, month, onApply, onCancel }: MonthYearPickerProps) {
  const [selMonth, setSelMonth] = useState(month);
  const [selYear, setSelYear] = useState(year);
  const yearRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (yearRef.current) {
      const active = yearRef.current.querySelector(".my-picker__year--active") as HTMLElement | null;
      if (active) {
        active.scrollIntoView({ block: "center" });
      }
    }
  }, []);

  return (
    <div className="my-picker">
      <div className="my-picker__body">
        <div className="my-picker__months">
          {MONTHS.map((m, i) => (
            <button
              key={m}
              type="button"
              className={`my-picker__month${i === selMonth ? " my-picker__month--active" : ""}`}
              onClick={() => setSelMonth(i)}
            >
              {m}
            </button>
          ))}
        </div>

        <div className="my-picker__years" ref={yearRef}>
          {Array.from({ length: YEAR_RANGE * 2 + 1 }, (_, i) => year - YEAR_RANGE + i).map(
            (y) => (
              <button
                key={y}
                type="button"
                className={`my-picker__year${y === selYear ? " my-picker__year--active" : ""}`}
                onClick={() => setSelYear(y)}
              >
                {y}
              </button>
            )
          )}
        </div>
      </div>

      <div className="my-picker__footer">
        <button type="button" className="btn" onClick={onCancel}>
          취소
        </button>
        <button
          type="button"
          className="btn btn--primary"
          onClick={() => onApply(selYear, selMonth)}
        >
          확인
        </button>
      </div>
    </div>
  );
}
