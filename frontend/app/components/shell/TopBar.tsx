"use client";

import { useEffect, useState } from "react";
import { IconButton } from "../ui/IconButton";
import { AlarmPopover } from "./AlarmPopover";
import { UserDropdown } from "./UserDropdown";
import type { TopBarProps } from "@/lib/types/shell";

const DEFAULT_NAME = "UND RCS";

export function TopBar({ dateTime, onToggleNav, navExpanded }: TopBarProps) {
  const [systemName, setSystemName] = useState(DEFAULT_NAME);

  useEffect(() => {
    const saved = typeof window !== "undefined" ? localStorage.getItem("system_name") : null;
    if (saved) setSystemName(saved);
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
