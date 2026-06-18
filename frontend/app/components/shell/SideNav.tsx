"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { NavItem, SideNavProps } from "@/lib/types/shell";

export const defaultNavItems: NavItem[] = [
  { label: "모니터링", href: "/monitoring", match: "/monitoring", icon: "/icon/monitoring.svg" },
  { label: "로봇관리", href: "/robots", match: "/robots", icon: "/icon/robot.svg" },
  // VESA 운영: 작업관리(자동 경로/스케줄) 메뉴는 미사용 — 페이지 파일은 보존(직접 URL 접근만 가능)
  { label: "통계", href: "/stats", match: "/stats", icon: "/icon/stats.svg" },
  { label: "로그관리", href: "/logs", match: "/logs", icon: "/icon/log.svg" },
  { label: "맵관리", href: "/map", match: "/map", icon: "/icon/map.svg" },
  { label: "설정", href: "/settings", match: "/settings", icon: "/icon/settings.svg" },
];

export function SideNav({
  items,
  collapsed = false,
  onClose,
  onItemSelect,
}: SideNavProps) {
  const pathname = usePathname();

  const classes = [
    "side-nav",
    collapsed ? "side-nav--collapsed" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <>
      {!collapsed ? (
        <button
          className="side-nav__backdrop"
          aria-label="Close navigation"
          onClick={onClose}
        />
      ) : null}
      <nav className={classes} aria-label="Primary">
        <ul className="side-nav__list">
          {items.map((item) => {
            const isActive = item.match
              ? pathname.startsWith(item.match)
              : pathname === item.href;

            const itemClass = [
              "side-nav__item",
              isActive ? "side-nav__item--active" : "",
            ]
              .filter(Boolean)
              .join(" ");

            return (
              <li key={item.label} className={itemClass}>
                <Link
                  href={item.href}
                  className="side-nav__link"
                  aria-label={item.label}
                  title={collapsed ? item.label : undefined}
                  onClick={onItemSelect}
                >
                  <span className="side-nav__icon" aria-hidden="true">
                    <img src={item.icon} alt="" width={24} height={24} />
                  </span>
                  <span className="side-nav__label">{item.label}</span>
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>
    </>
  );
}
