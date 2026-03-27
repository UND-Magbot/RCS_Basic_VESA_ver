"use client";

import { useState, useCallback, useEffect } from "react";
import { useAlert } from "@/lib/context/AlertContext";
import { apiFetch } from "@/lib/api";
import { DirPickerModal } from "./DirPickerModal";
import "./DbBackupTab.css";
import "./DirPickerModal.css";

export function DbBackupTab() {
  const { showInfo } = useAlert();
  const STORAGE_KEY = "db_backup_path";
  const [savePath, setSavePath] = useState(() => {
    if (typeof window !== "undefined") {
      return localStorage.getItem(STORAGE_KEY) || "/home/administrator/";
    }
    return "/home/administrator/";
  });

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, savePath);
  }, [savePath]);
  const [isSaving, setIsSaving] = useState(false);
  const [showPicker, setShowPicker] = useState(false);

  const handleSave = useCallback(async () => {
    if (!savePath.trim()) {
      showInfo("알림", "저장 경로를 입력해주세요.");
      return;
    }
    setIsSaving(true);
    try {
      const res = await apiFetch<{ sql_path: string }>(
        "/api/backup/save",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ save_path: savePath.trim() }),
        }
      );
      showInfo("알림", `백업 완료\nSQL: ${res.sql_path}`);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "DB 백업에 실패했습니다.";
      showInfo("알림", msg);
    } finally {
      setIsSaving(false);
    }
  }, [savePath, showInfo]);

  return (
    <section className="settings-section">
      <h2 className="db-backup__title">DB 백업</h2>

      <div className="db-backup__field">
        <div className="db-backup__path-row">
          <input
            className="db-backup__path-input"
            type="text"
            value={savePath}
            onChange={(e) => setSavePath(e.target.value)}
            placeholder="서버 저장 경로"
            disabled={isSaving}
          />
          <button
            className="db-backup__btn db-backup__btn--select"
            onClick={() => setShowPicker(true)}
            disabled={isSaving}
          >
            탐색
          </button>
          <button
            className="db-backup__btn db-backup__btn--download"
            onClick={handleSave}
            disabled={isSaving}
          >
            {isSaving ? "백업 중..." : "백업"}
          </button>
        </div>
      </div>

      {showPicker && (
        <DirPickerModal
          initialPath={savePath || "/home/und/app/"}
          onConfirm={(path) => {
            setSavePath(path);
            setShowPicker(false);
          }}
          onClose={() => setShowPicker(false)}
        />
      )}
    </section>
  );
}
