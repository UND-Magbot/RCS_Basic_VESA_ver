/**
 * 모니터링 / 맵 관리 페이지 간 마지막 선택(사업장·영역) 공유.
 * localStorage 기반. 두 페이지의 첫 화면이 동일한 사업장·영역을 보여주도록.
 */

const BIZ_KEY = "rcs:selectedBusiness";
const AREA_KEY = "rcs:selectedArea";

export function getStoredBusiness(): string {
  if (typeof window === "undefined") return "";
  try {
    return window.localStorage.getItem(BIZ_KEY) || "";
  } catch {
    return "";
  }
}

export function getStoredArea(): string {
  if (typeof window === "undefined") return "";
  try {
    return window.localStorage.getItem(AREA_KEY) || "";
  } catch {
    return "";
  }
}

export function setStoredBusiness(id: string) {
  if (typeof window === "undefined") return;
  try {
    if (id) window.localStorage.setItem(BIZ_KEY, id);
    else window.localStorage.removeItem(BIZ_KEY);
  } catch {}
}

export function setStoredArea(id: string) {
  if (typeof window === "undefined") return;
  try {
    if (id) window.localStorage.setItem(AREA_KEY, id);
    else window.localStorage.removeItem(AREA_KEY);
  } catch {}
}
