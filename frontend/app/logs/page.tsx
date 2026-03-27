"use client";

import { useState, useEffect, useCallback } from "react";
import { TopBar } from "../components/shell/TopBar";
import { SideNav, defaultNavItems } from "../components/shell/SideNav";
import { LogFilter } from "../components/ui/logs/LogFilter";
import { LogTable } from "../components/ui/logs/LogTable";
import type { LogFilterState, LogItem } from "@/lib/types/logs";
import { apiFetch } from "@/lib/api";
import "./logs.css";

function formatDateTime() {
  const now = new Date();
  const yyyy = now.getFullYear();
  const mm = String(now.getMonth() + 1).padStart(2, "0");
  const dd = String(now.getDate()).padStart(2, "0");
  const hh = String(now.getHours()).padStart(2, "0");
  const min = String(now.getMinutes()).padStart(2, "0");
  return `${yyyy}-${mm}-${dd} ${hh}:${min}`;
}

function formatCreatedAt(raw: string): string {
  const d = new Date(raw);
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const min = String(d.getMinutes()).padStart(2, "0");
  return `${yyyy}-${mm}-${dd} ${hh}:${min}`;
}


function todayStr(): string {
  const now = new Date();
  const y = now.getFullYear();
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

const defaultFilters: LogFilterState = {
  message: "",
  errorType: "",
  startDate: todayStr(),
  endDate: todayStr(),
  startTime: "00:00",
  endTime: "23:59",
};

const PAGE_SIZE = 8;
const PAGE_GROUP = 5;

function buildParams(f: LogFilterState, skip: number, limit: number): string {
  const p = new URLSearchParams();
  p.set("skip", String(skip));
  p.set("limit", String(limit));
  if (f.message) p.set("message", f.message);
  if (f.errorType) p.set("display_category", f.errorType);
  p.set("date_from", `${f.startDate}T${f.startTime}:00`);
  p.set("date_to", `${f.endDate}T${f.endTime}:59`);
  return p.toString();
}

export default function LogsPage() {
  const [navCollapsed, setNavCollapsed] = useState(true);
  const [currentDateTime, setCurrentDateTime] = useState(formatDateTime);
  const [isLoading, setIsLoading] = useState(true);

  const [logs, setLogs] = useState<LogItem[]>([]);
  const [total, setTotal] = useState(0);
  const [filters, setFilters] = useState<LogFilterState>(defaultFilters);
  const [appliedFilters, setAppliedFilters] = useState<LogFilterState>(defaultFilters);
  const [currentPage, setCurrentPage] = useState(1);

  useEffect(() => {
    const timer = setInterval(() => setCurrentDateTime(formatDateTime()), 1000);
    return () => clearInterval(timer);
  }, []);

  const fetchLogs = useCallback(async (f: LogFilterState, page: number) => {
    setIsLoading(true);
    try {
      const qs = buildParams(f, (page - 1) * PAGE_SIZE, PAGE_SIZE);
      const res = await apiFetch<{ total: number; items: LogItem[] }>(
        `/api/logs?${qs}`
      );
      setTotal(res.total);
      setLogs(res.items);
    } catch {
      setLogs([]);
      setTotal(0);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchLogs(appliedFilters, currentPage);
  }, [appliedFilters, currentPage, fetchLogs]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  useEffect(() => {
    if (currentPage > totalPages) setCurrentPage(totalPages);
  }, [totalPages, currentPage]);

  const pageGroupStart =
    Math.floor((currentPage - 1) / PAGE_GROUP) * PAGE_GROUP + 1;
  const pageNumbers = Array.from(
    { length: Math.min(PAGE_GROUP, totalPages - pageGroupStart + 1) },
    (_, i) => pageGroupStart + i
  );

  const handleSearch = () => {
    setAppliedFilters({ ...filters });
    setCurrentPage(1);
  };

  const handleReset = () => {
    const today = todayStr();
    const resetFilters: LogFilterState = {
      ...defaultFilters,
      startDate: today,
      endDate: today,
    };
    setFilters(resetFilters);
    setAppliedFilters(resetFilters);
    setCurrentPage(1);
  };

  return (
    <>
      <div className="app-shell">
      <TopBar
        dateTime={currentDateTime}
        onToggleNav={() => setNavCollapsed((v) => !v)}
        navExpanded={!navCollapsed}
      />
      <div className="shell-body">
        <SideNav
          items={defaultNavItems}
          collapsed={navCollapsed}
          onClose={() => setNavCollapsed(true)}
          onItemSelect={() => setNavCollapsed(true)}
        />
        <main className="main-content">
          <div className="logs-page">
            <header className="logs-page__header">
              <h1 className="logs-page__title">로그 관리</h1>
            </header>

            <LogFilter
              filters={filters}
              onFilterChange={setFilters}
              onSearch={handleSearch}
              onReset={handleReset}
            />

            <LogTable logs={logs} />

            <div className="pagination">
              <button
                className="pagination__btn"
                disabled={currentPage <= 1}
                onClick={() => setCurrentPage((p) => p - 1)}
              >
                이전
              </button>
              {pageNumbers.map((num) => (
                <button
                  key={num}
                  className={`pagination__num${num === currentPage ? " pagination__num--active" : ""}`}
                  onClick={() => setCurrentPage(num)}
                >
                  {num}
                </button>
              ))}
              <button
                className="pagination__btn"
                disabled={currentPage >= totalPages}
                onClick={() => setCurrentPage((p) => p + 1)}
              >
                다음
              </button>
              <span className="pagination__info">
                총 {total} 개
              </span>
            </div>
          </div>
        </main>
      </div>
    </div>
    </>
  );
}
