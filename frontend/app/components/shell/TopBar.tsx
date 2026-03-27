import { IconButton } from "../ui/IconButton";
import { AlarmPopover } from "./AlarmPopover";
import { UserDropdown } from "./UserDropdown";
import type { TopBarProps } from "@/lib/types/shell";

export function TopBar({ dateTime, onToggleNav, navExpanded }: TopBarProps) {
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
      <h2 className="top-bar__center">UND RCS</h2>
      <div className="top-bar__right">
        <span className="top-bar__datetime">{dateTime}</span>
        <AlarmPopover iconSrc="/icon/Icon_v2 (41).png" />
        <UserDropdown userName="관리자" iconSrc="/icon/Icon (8).png" />
      </div>
    </header>
  );
}
