"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import type { Business } from "@/lib/types/robots";
import "./BusinessSelectBox.css";

type Props = {
  businesses: Business[];
  selectedId: string;
  onChange: (businessId: string) => void;
  extraButton?: ReactNode;
};

export function BusinessSelectBox({ businesses, selectedId, onChange, extraButton }: Props) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  const selectedName =
    businesses.find((b) => b.id === selectedId)?.name ??
    businesses[0]?.name ??
    "";

  const handleClose = useCallback(() => setOpen(false), []);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") handleClose();
    };
    const onClick = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        handleClose();
      }
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onClick);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onClick);
    };
  }, [open, handleClose]);

  const handleSelect = (bizId: string) => {
    if (bizId !== selectedId) {
      onChange(bizId);
    }
    handleClose();
  };

  return (
    <div className="biz-select" ref={wrapRef} style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <button
        type="button"
        className="biz-select__trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="biz-select__label">{selectedName}</span>
        <span
          className={
            open
              ? "biz-select__arrow biz-select__arrow--open"
              : "biz-select__arrow"
          }
        >
          &#9662;
        </span>
      </button>

      {open && (
        <ul className="biz-select__menu" role="listbox">
          {businesses.map((biz) => (
            <li key={biz.id} role="option" aria-selected={biz.id === selectedId} data-value={biz.value ?? ""}>
              <button
                type="button"
                className={
                  biz.id === selectedId
                    ? "biz-select__option biz-select__option--selected"
                    : "biz-select__option"
                }
                onClick={() => handleSelect(biz.id)}
              >
                {biz.name}
              </button>
            </li>
          ))}
        </ul>
      )}

      {extraButton}
    </div>
  );
}
