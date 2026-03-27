"use client";

import { useState, useCallback, useEffect, useRef } from "react";
import { useAlert } from "@/lib/context/AlertContext";
import { verifyPassword, changePassword } from "@/lib/api/settings";
import "./PasswordChangeTab.css";

const PW_REGEX = /^\d{4}$/;

export function PasswordChangeTab() {
  const { showInfo, alertModal } = useAlert();
  const [currentPw, setCurrentPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [isChanging, setIsChanging] = useState(false);
  const shouldReloadRef = useRef(false);

  useEffect(() => {
    if (shouldReloadRef.current && !alertModal) {
      window.location.reload();
    }
  }, [alertModal]);

  const handleSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();

      setIsChanging(true);
      try {
        const { valid } = await verifyPassword(currentPw);
        if (!valid) {
          showInfo("알림", "현재 비밀번호가 일치하지 않습니다.");
          return;
        }

        if (!PW_REGEX.test(newPw)) {
          showInfo("알림", "비밀번호는 4자리 숫자로 설정해주세요.");
          return;
        }

        await changePassword(currentPw, newPw);
        shouldReloadRef.current = true;
        showInfo("알림", "비밀번호가 변경되었습니다.");
      } catch {
        showInfo("알림", "비밀번호 변경에 실패했습니다.");
      } finally {
        setIsChanging(false);
      }
    },
    [currentPw, newPw, showInfo]
  );

  return (
    <section className="settings-section">
      <div className="pw-tab">
        <div className="pw-tab__section">
          <h2 className="pw-tab__section-title">비밀번호 변경</h2>
          <form className="pw-tab__form" onSubmit={handleSubmit}>
            <div className="pw-tab__field">
              <label className="pw-tab__label" htmlFor="currentPw">
                현재 비밀번호
              </label>
              <input
                id="currentPw"
                className="pw-tab__input"
                type="password"
                placeholder="현재 비밀번호를 입력해주세요"
                value={currentPw}
                onChange={(e) => setCurrentPw(e.target.value)}
                autoComplete="off"
              />
            </div>

            <div className="pw-tab__field">
              <label className="pw-tab__label" htmlFor="newPw">
                새 비밀번호
              </label>
              <input
                id="newPw"
                className="pw-tab__input"
                type="password"
                placeholder="4자리 숫자"
                maxLength={4}
                inputMode="numeric"
                value={newPw}
                onChange={(e) => setNewPw(e.target.value)}
                autoComplete="off"
              />
            </div>

            <button
              className="pw-tab__submit-btn"
              type="submit"
              disabled={isChanging || !currentPw || !newPw}
            >
              비밀번호 변경
            </button>
          </form>
        </div>
      </div>
    </section>
  );
}
