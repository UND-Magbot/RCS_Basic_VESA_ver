"use client";

import { useEffect, useState } from "react";
import { IconButton } from "../ui/IconButton";
import { AlarmPopover } from "./AlarmPopover";
import { UserDropdown } from "./UserDropdown";
import type { TopBarProps } from "@/lib/types/shell";

const DEFAULT_NAME = "UND RCS";
const API = process.env.NEXT_PUBLIC_API_URL || "";

export function TopBar({ dateTime, onToggleNav, navExpanded }: TopBarProps) {
  const [systemName, setSystemName] = useState<string>(() => {
    // SSR-safe 초기값: 캐시된 localStorage 가 있으면 깜빡임 방지용으로 먼저 사용
    if (typeof window === "undefined") return DEFAULT_NAME;
    return localStorage.getItem("system_name") || DEFAULT_NAME;
  });

  useEffect(() => {
    // 서버에서 최신값 가져오기 (단일 진실 원천 — 태블릿/다른 PC 와 일치)
    fetch(`${API}/api/settings/system-name`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        const v = data?.system_name || DEFAULT_NAME;
        setSystemName(v);
        // 호환용 캐시
        if (v && v !== DEFAULT_NAME) localStorage.setItem("system_name", v);
        else localStorage.removeItem("system_name");
      })
      .catch(() => {
        // 서버 실패 시 localStorage 폴백 유지
      });

    const onChange = (e: Event) => {
      const detail = (e as CustomEvent).detail;
      if (typeof detail === "string") setSystemName(detail || DEFAULT_NAME);
    };
    window.addEventListener("system-name-change", onChange as EventListener);
    return () => window.removeEventListener("system-name-change", onChange as EventListener);
  }, []);

  return (
    <header
      className="top-bar"
      data-nav-collapsed={navExpanded === false ? "true" : "false"}
    >
      <div className="top-bar__left">
        {onToggleNav ? (
          <IconButton
            aria-label="Toggle navigation"
            aria-expanded={navExpanded}
            onClick={onToggleNav}
            className="top-bar__toggle"
            variant="ghost"
          >
            ☰
          </IconButton>
        ) : null}
      </div>
      <h2 className="top-bar__center">{systemName}</h2>
      <div className="top-bar__right">
        <span className="top-bar__datetime">{dateTime}</span>
        <AlarmPopover iconSrc="/icon/Icon_v2 (41).png" />
        <UserDropdown userName="관리자" iconSrc="/icon/Icon (8).png" />
      </div>
    </header>
  );
}
