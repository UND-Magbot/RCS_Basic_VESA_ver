"use client";

import { useState, useEffect } from "react";
import { TopBar } from "../components/shell/TopBar";
import { SideNav, defaultNavItems } from "../components/shell/SideNav";
import { PasswordChangeTab } from "../components/ui/settings/PasswordChangeTab";
import { DbBackupTab } from "../components/ui/settings/DbBackupTab";
import "./settings.css";

function formatDateTime() {
  const now = new Date();
  const yyyy = now.getFullYear();
  const mm = String(now.getMonth() + 1).padStart(2, "0");
  const dd = String(now.getDate()).padStart(2, "0");
  const hh = String(now.getHours()).padStart(2, "0");
  const min = String(now.getMinutes()).padStart(2, "0");
  return `${yyyy}-${mm}-${dd} ${hh}:${min}`;
}

type Tab = "password-change" | "db-backup" | "system-name";

export default function SettingsPage() {
  const [navCollapsed, setNavCollapsed] = useState(true);
  const [currentDateTime, setCurrentDateTime] = useState(formatDateTime);
  const [activeTab, setActiveTab] = useState<Tab>("password-change");

  useEffect(() => {
    const timer = setInterval(() => setCurrentDateTime(formatDateTime()), 1000);
    return () => clearInterval(timer);
  }, []);

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
            <div className="settings-page">
              <header className="settings-page__header">
                <h1 className="settings-page__title">설정</h1>
                <div className="settings-page__tabs">
                  <button
                    className={`settings-page__tab${activeTab === "db-backup" ? " settings-page__tab--active" : ""}`}
                    onClick={() => setActiveTab("db-backup")}
                  >
                    DB 백업
                  </button>
                  <button
                    className={`settings-page__tab${activeTab === "password-change" ? " settings-page__tab--active" : ""}`}
                    onClick={() => setActiveTab("password-change")}
                  >
                    비밀번호 변경
                  </button>
                  <button
                    className={`settings-page__tab${activeTab === "system-name" ? " settings-page__tab--active" : ""}`}
                    onClick={() => setActiveTab("system-name")}
                  >
                    시스템 이름
                  </button>
                </div>
              </header>

              {activeTab === "password-change" && <PasswordChangeTab />}

              {activeTab === "db-backup" && <DbBackupTab />}

              {activeTab === "system-name" && <SystemNameTab />}
            </div>
          </main>
        </div>
      </div>
    </>
  );
}

function SystemNameTab() {
  const [name, setName] = useState("");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    const v = localStorage.getItem("system_name") || "";
    setName(v);
  }, []);

  const handleSave = () => {
    const trimmed = name.trim();
    localStorage.setItem("system_name", trimmed);
    window.dispatchEvent(new CustomEvent("system-name-change", { detail: trimmed }));
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

  const handleReset = () => {
    localStorage.removeItem("system_name");
    window.dispatchEvent(new CustomEvent("system-name-change", { detail: "UND RCS" }));
    setName("");
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

  return (
    <div style={{ padding: 24, maxWidth: 500 }}>
      <h3 style={{ marginTop: 0 }}>시스템 이름</h3>
      <p style={{ color: "var(--text-muted)", fontSize: 13, marginBottom: 16 }}>
        상단 타이틀에 표시되는 이름을 변경합니다. 비우고 저장하면 기본값(UND RCS)으로 돌아갑니다.
      </p>
      <input
        type="text"
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="UND RCS"
        style={{
          width: "100%", padding: "10px 12px", fontSize: 14,
          background: "var(--bg-surface-2)", border: "1px solid var(--border-color)",
          borderRadius: 6, color: "var(--text-primary)", marginBottom: 12,
        }}
      />
      <div style={{ display: "flex", gap: 8 }}>
        <button
          onClick={handleSave}
          style={{
            padding: "8px 16px", background: "var(--color-primary)", color: "#fff",
            border: "none", borderRadius: 6, cursor: "pointer", fontSize: 13,
          }}
        >
          저장
        </button>
        <button
          onClick={handleReset}
          style={{
            padding: "8px 16px", background: "transparent", color: "var(--text-primary)",
            border: "1px solid var(--border-color)", borderRadius: 6, cursor: "pointer", fontSize: 13,
          }}
        >
          기본값으로
        </button>
        {saved && <span style={{ alignSelf: "center", color: "var(--color-success, #4caf50)", fontSize: 13 }}>저장됨</span>}
      </div>
    </div>
  );
}
