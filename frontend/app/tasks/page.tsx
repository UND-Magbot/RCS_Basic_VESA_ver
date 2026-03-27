"use client";

import { useState, useEffect, useCallback } from "react";
import { TopBar } from "../components/shell/TopBar";
import { SideNav, defaultNavItems } from "../components/shell/SideNav";
import type {
  ScheduledTask, ScheduledTaskCreate, RepeatType,
  TaskRoute, TaskRouteCreate, TaskRouteWaypoint,
  TaskHistory,
} from "@/lib/types/tasks";
import {
  getTasks, createTask, updateTask, deleteTask, toggleTask, runTaskNow,
  getRoutes, createRoute, updateRoute, deleteRoute,
  getAllHistory,
} from "@/lib/api/tasks";

import { RouteMapView } from "../components/ui/tasks/RouteMapView";
import { WheelTimePicker } from "../components/ui/tasks/WheelTimePicker";
import "./tasks.css";

const API = process.env.NEXT_PUBLIC_API_URL || "";

type RobotOption = { id: number; name: string; ip: string };
type PoiOption = { id: number; name: string; type: string; world_x: number; world_y: number };

const DAY_LABELS = ["월", "화", "수", "목", "금", "토", "일"];
const DAY_VALUES = ["1", "2", "3", "4", "5", "6", "7"];

function formatDt(s: string | null) {
  if (!s) return "-";
  const d = new Date(s);
  return d.toLocaleString("ko-KR", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function repeatLabel(type: string, days: string | null) {
  if (type === "once") return "1회";
  if (type === "daily") return "매일";
  if (type === "weekly" && days) {
    const labels = days.split(",").map((d) => DAY_LABELS[Number(d) - 1] || d);
    return `매주 ${labels.join(",")}`;
  }
  return type;
}

export default function TasksPage() {
  const [navCollapsed, setNavCollapsed] = useState(true);
  const [activeTab, setActiveTab] = useState<"schedule" | "routes">("schedule");
  const [isLoading, setIsLoading] = useState(true);
  const [alertModal, setAlertModal] = useState<{ message: string; onConfirm?: () => void } | null>(null);

  const showAlert = (message: string) => setAlertModal({ message });
  const showConfirm = (message: string, onConfirm: () => void) => setAlertModal({ message, onConfirm });

  // 공통 데이터
  const [robots, setRobots] = useState<RobotOption[]>([]);
  const [pois, setPois] = useState<PoiOption[]>([]);

  // 스케줄 데이터
  const [tasks, setTasks] = useState<ScheduledTask[]>([]);
  const [history, setHistory] = useState<TaskHistory[]>([]);
  const [historyTotal, setHistoryTotal] = useState(0);
  const [historyPage, setHistoryPage] = useState(1);
  const HISTORY_PAGE_SIZE = 10;
  const [showScheduleModal, setShowScheduleModal] = useState(false);
  const [editTask, setEditTask] = useState<ScheduledTask | null>(null);

  // 경로 데이터
  const [routes, setRoutes] = useState<TaskRoute[]>([]);
  const [editRoute, setEditRoute] = useState<TaskRoute | null>(null);

  // 스케줄 모달 폼
  const [fName, setFName] = useState("");
  const [fRobotId, setFRobotId] = useState<number>(0);
  const [fRouteId, setFRouteId] = useState<number>(0);
  const [fStartTime, setFStartTime] = useState("09:00");
  const [fEndTime, setFEndTime] = useState("");
  const [fRepeatType, setFRepeatType] = useState<RepeatType>("once");
  const [fRepeatDays, setFRepeatDays] = useState<Set<string>>(new Set());
  const [fStartDate, setFStartDate] = useState(new Date().toISOString().split("T")[0]);
  const [fEndDate, setFEndDate] = useState("");

  // 경로 편집 상태
  const [rName, setRName] = useState("");
  const [rWaypoints, setRWaypoints] = useState<{ poi_id: number; poi_name: string; waypoint_type: string; wait_sec: number }[]>([]);
  const [isEditingRoute, setIsEditingRoute] = useState(false);

  const fetchHistory = useCallback(async (page: number) => {
    try {
      const data = await getAllHistory({ limit: HISTORY_PAGE_SIZE, skip: (page - 1) * HISTORY_PAGE_SIZE });
      setHistory(data.items);
      setHistoryTotal(data.total || 0);
    } catch {}
  }, []);

  const fetchAll = useCallback(async () => {
    try {
      const [taskData, routeData, historyData, robotRes, poiRes] = await Promise.all([
        getTasks(),
        getRoutes(),
        getAllHistory({ limit: HISTORY_PAGE_SIZE, skip: 0 }),
        fetch(`${API}/api/robots/live`, { cache: "no-store" }).then((r) => r.json()),
        fetch(`${API}/api/map/active-pois`, { cache: "no-store" }).then((r) => r.json()).catch(() => []),
      ]);
      setTasks(taskData.items);
      setRoutes(routeData.items);
      setHistory(historyData.items);
      setHistoryTotal(historyData.total || 0);
      setRobots(
        (robotRes.items || [])
          .filter((r: any) => r.ONLINE === "Online")
          .map((r: any) => ({ id: r.ID, name: r.NICKNAME || r.SN, ip: r.IP }))
      );
      setPois(
        (Array.isArray(poiRes) ? poiRes : []).map((p: any) => ({
          id: p.id, name: p.name, type: p.poi_type || "general",
          world_x: p.world_x || 0, world_y: p.world_y || 0,
        }))
      );
    } catch { /* ignore */ }
    setIsLoading(false);
  }, []);

  useEffect(() => {
    fetchAll();
    const interval = setInterval(fetchAll, 15000);
    return () => clearInterval(interval);
  }, [fetchAll]);

  // 페이지 변경 시 이력만 갱신
  useEffect(() => {
    fetchHistory(historyPage);
  }, [historyPage, fetchHistory]);

  // ── 스케줄 핸들러 ──
  const openScheduleCreate = () => {
    setEditTask(null);
    setFName("");
    setFRobotId(robots[0]?.id || 0);
    setFRouteId(routes[0]?.id || 0);
    setFStartTime("09:00");
    setFEndTime("");
    setFRepeatType("once");
    setFRepeatDays(new Set());
    setFStartDate(new Date().toISOString().split("T")[0]);
    setFEndDate("");
    setShowScheduleModal(true);
  };

  const openScheduleEdit = (t: ScheduledTask) => {
    setEditTask(t);
    setFName(t.name);
    setFRobotId(t.robot_id || 0);
    setFRouteId(t.route_id);
    setFStartTime(t.start_time);
    setFEndTime(t.end_time || "");
    setFRepeatType(t.repeat_type);
    setFRepeatDays(new Set(t.repeat_days?.split(",") || []));
    setFStartDate(t.start_date);
    setFEndDate(t.end_date || "");
    setShowScheduleModal(true);
  };

  const handleScheduleSubmit = async () => {
    if (!fName || !fRobotId || !fRouteId || !fStartTime || !fStartDate) return;
    const dupTask = tasks.find((t) => t.name === fName && t.id !== editTask?.id);
    if (dupTask) {
      showAlert("같은 이름의 스케줄이 이미 존재합니다.");
      return;
    }
    const data: ScheduledTaskCreate = {
      name: fName,
      robot_id: fRobotId,
      route_id: fRouteId,
      start_time: fStartTime,
      end_time: fEndTime || null,
      repeat_type: fRepeatType,
      repeat_days: fRepeatType === "weekly" ? Array.from(fRepeatDays).join(",") : null,
      start_date: fStartDate,
      end_date: fEndDate || null,
    };
    try {
      if (editTask) {
        await updateTask(editTask.id, data);
      } else {
        await createTask(data);
      }
      setShowScheduleModal(false);
      const taskData = await getTasks();
      setTasks(taskData.items);
      showAlert("스케줄이 저장되었습니다.");
    } catch (e) {
      showAlert(`저장 실패: ${e}`);
    }
  };

  const toggleDow = (d: string) => {
    setFRepeatDays((prev) => {
      const next = new Set(prev);
      next.has(d) ? next.delete(d) : next.add(d);
      return next;
    });
  };

  // ── 경로 핸들러 ──
  const startRouteCreate = () => {
    setEditRoute(null);
    setRName("");
    setRWaypoints([]);
    setIsEditingRoute(true);
  };

  const startRouteEdit = (r: TaskRoute) => {
    setEditRoute(r);
    setRName(r.name);
    setRWaypoints(r.waypoints.map((w: any) => ({
      poi_id: w.poi_id,
      poi_name: w.poi_name || "",
      waypoint_type: w.waypoint_type,
      wait_sec: w.wait_sec || 0,
    })));
    setIsEditingRoute(true);
  };

  const cancelRouteEdit = () => {
    setIsEditingRoute(false);
    setEditRoute(null);
  };

  const addWaypoint = (poi: PoiOption) => {
    let autoType: string;
    if (poi.type === "charging") {
      autoType = "charging";
    } else if (rWaypoints.length === 0) {
      autoType = "pickup";
    } else if (rWaypoints.length === 1) {
      autoType = "dropoff";
    } else {
      autoType = "standby";
    }
    setRWaypoints((prev) => [...prev, { poi_id: poi.id, poi_name: poi.name, waypoint_type: autoType, wait_sec: 0 }]);
  };

  const removeWaypoint = (idx: number) => {
    setRWaypoints((prev) => prev.filter((_, i) => i !== idx));
  };

  const moveWaypoint = (idx: number, dir: -1 | 1) => {
    const newIdx = idx + dir;
    if (newIdx < 0 || newIdx >= rWaypoints.length) return;
    setRWaypoints((prev) => {
      const next = [...prev];
      [next[idx], next[newIdx]] = [next[newIdx], next[idx]];
      return next;
    });
  };

  const changeWaypointType = (idx: number, type: string) => {
    setRWaypoints((prev) => prev.map((w, i) => i === idx ? { ...w, waypoint_type: type } : w));
  };

  const changeWaypointWait = (idx: number, sec: number) => {
    setRWaypoints((prev) => prev.map((w, i) => i === idx ? { ...w, wait_sec: sec } : w));
  };

  const handleRouteSave = async () => {
    if (!rName || rWaypoints.length < 2) {
      showAlert("경로 이름과 최소 2개 POI가 필요합니다");
      return;
    }
    const dupRoute = routes.find((r) => r.name === rName && r.id !== editRoute?.id);
    if (dupRoute) {
      showAlert("같은 이름의 경로가 이미 존재합니다.");
      return;
    }
    const waypoints: TaskRouteCreate["waypoints"] = rWaypoints.map((w, i) => ({
      poi_id: w.poi_id, order: i, waypoint_type: w.waypoint_type, wait_sec: w.wait_sec,
    }));
    const data: TaskRouteCreate = { name: rName, waypoints };
    try {
      if (editRoute) {
        await updateRoute(editRoute.id, data);
      } else {
        await createRoute(data);
      }
      setIsEditingRoute(false);
      setEditRoute(null);
      const routeData = await getRoutes();
      setRoutes(routeData.items);
      showAlert("경로가 저장되었습니다.");
    } catch (e) {
      showAlert(`저장 실패: ${e}`);
    }
  };

  return (
    <div className="app-shell">
      <TopBar dateTime="" navExpanded={!navCollapsed} onToggleNav={() => setNavCollapsed((p) => !p)} />
      <div className="shell-body">
        <SideNav items={defaultNavItems} collapsed={navCollapsed} />
        <main className="main-content">
          <div className="tasks-page">
            {/* 탭 헤더 */}
            <div className="tasks-tabs">
              <button
                className={`tasks-tabs__btn ${activeTab === "schedule" ? "tasks-tabs__btn--active" : ""}`}
                onClick={() => setActiveTab("schedule")}
              >
                스케줄
              </button>
              <button
                className={`tasks-tabs__btn ${activeTab === "routes" ? "tasks-tabs__btn--active" : ""}`}
                onClick={() => setActiveTab("routes")}
              >
                경로 관리
              </button>
            </div>

            {isLoading ? (
              <div className="tasks-loading"><div className="spinner" /></div>
            ) : activeTab === "schedule" ? (
              /* ══════ 스케줄 탭 ══════ */
              <>
                <div className="tasks-header">
                  <h3>스케줄 목록</h3>
                  <button className="tasks-btn tasks-btn--primary" onClick={openScheduleCreate}>+ 스케줄 추가</button>
                </div>

                <div className="tasks-table-wrap">
                  <table className="tasks-table">
                    <thead>
                      <tr>
                        <th>이름</th>
                        <th>경로</th>
                        <th>로봇</th>
                        <th>시간</th>
                        <th>반복</th>
                        <th>활성</th>
                        <th>마지막 실행</th>
                        <th>다음 실행</th>
                        <th>동작</th>
                      </tr>
                    </thead>
                    <tbody>
                      {tasks.length === 0 ? (
                        <tr><td colSpan={9} className="tasks-table__empty">등록된 스케줄이 없습니다</td></tr>
                      ) : (
                        tasks.map((t) => (
                          <tr key={t.id}>
                            <td>{t.name}</td>
                            <td>{t.route_name || "-"}</td>
                            <td>{t.robot_name || "-"}</td>
                            <td>{t.start_time}{t.end_time ? ` ~ ${t.end_time}` : ""}</td>
                            <td>{repeatLabel(t.repeat_type, t.repeat_days)}</td>
                            <td>
                              <button className={`tasks-toggle ${t.is_active ? "tasks-toggle--on" : ""}`}
                                onClick={() => toggleTask(t.id).then(() => getTasks().then((d) => setTasks(d.items)))}>
                                {t.is_active ? "ON" : "OFF"}
                              </button>
                            </td>
                            <td>{formatDt(t.last_run_at)}</td>
                            <td>{formatDt(t.next_run_at)}</td>
                            <td className="tasks-table__actions">
                              <button className="tasks-btn tasks-btn--run" onClick={() => runTaskNow(t.id).then(() => getTasks().then((d) => setTasks(d.items)))}>실행</button>
                              <button className="tasks-btn tasks-btn--edit" onClick={() => openScheduleEdit(t)}>편집</button>
                              <button className="tasks-btn tasks-btn--del" onClick={() => showConfirm("스케줄을 삭제하시겠습니까?", () => deleteTask(t.id).then(() => getTasks().then((d) => setTasks(d.items))))}>삭제</button>
                            </td>
                          </tr>
                        ))
                      )}
                    </tbody>
                  </table>
                </div>

                {/* 실행 이력 */}
                <div className="tasks-history">
                  <h3>실행 이력</h3>
                  <table className="tasks-table tasks-table--history">
                    <thead>
                      <tr><th>작업</th><th>경로</th><th>로봇</th><th>상태</th><th>시작</th><th>종료</th><th>에러</th></tr>
                    </thead>
                    <tbody>
                      {history.length === 0 ? (
                        <tr><td colSpan={7} className="tasks-table__empty">실행 이력이 없습니다</td></tr>
                      ) : (
                        history.map((h) => (
                          <tr key={h.id}>
                            <td>{h.task_name || "-"}</td>
                            <td>{h.route_name || "-"}</td>
                            <td>{h.robot_name || "-"}</td>
                            <td>
                              <span className={`tasks-status tasks-status--${h.status}`}>
                                {h.status === "running" ? "실행중" : h.status === "succeeded" ? "성공" : h.status === "failed" ? "실패" : "취소"}
                              </span>
                            </td>
                            <td>{formatDt(h.started_at)}</td>
                            <td>{formatDt(h.finished_at)}</td>
                            <td className="tasks-table__error">{h.error_message || "-"}</td>
                          </tr>
                        ))
                      )}
                    </tbody>
                  </table>
                  {historyTotal > HISTORY_PAGE_SIZE && (
                    <div className="tasks-pagination">
                      <button
                        className="tasks-pagination__btn"
                        disabled={historyPage <= 1}
                        onClick={() => setHistoryPage((p) => p - 1)}
                      >◀</button>
                      {Array.from(
                        { length: Math.ceil(historyTotal / HISTORY_PAGE_SIZE) },
                        (_, i) => i + 1
                      ).map((num) => (
                        <button
                          key={num}
                          className={`tasks-pagination__btn${num === historyPage ? " tasks-pagination__btn--active" : ""}`}
                          onClick={() => setHistoryPage(num)}
                        >
                          {num}
                        </button>
                      ))}
                      <button
                        className="tasks-pagination__btn"
                        disabled={historyPage >= Math.ceil(historyTotal / HISTORY_PAGE_SIZE)}
                        onClick={() => setHistoryPage((p) => p + 1)}
                      >▶</button>
                    </div>
                  )}
                </div>
              </>
            ) : (
              /* ══════ 경로 관리 탭 ══════ */
              <>
                {!isEditingRoute ? (
                  /* 경로 목록 */
                  <>
                    <div className="tasks-header">
                      <h3>경로 목록</h3>
                      <button className="tasks-btn tasks-btn--primary" onClick={startRouteCreate}>+ 경로 추가</button>
                    </div>
                    <div className="tasks-table-wrap">
                      <table className="tasks-table">
                        <thead>
                          <tr><th>이름</th><th>경로</th><th>동작</th></tr>
                        </thead>
                        <tbody>
                          {routes.length === 0 ? (
                            <tr><td colSpan={3} className="tasks-table__empty">등록된 경로가 없습니다</td></tr>
                          ) : (
                            routes.map((r) => (
                              <tr key={r.id}>
                                <td>{r.name}</td>
                                <td>{r.waypoints.map((w) => `${w.poi_name}(${w.waypoint_type === "pickup" ? "픽업" : w.waypoint_type === "dropoff" ? "드롭오프" : "대기"})`).join(" → ")}</td>
                                <td className="tasks-table__actions">
                                  <button className="tasks-btn tasks-btn--edit" onClick={() => startRouteEdit(r)}>편집</button>
                                  <button className="tasks-btn tasks-btn--del" onClick={() => showConfirm("경로를 삭제하시겠습니까?", () => deleteRoute(r.id).then(() => getRoutes().then((d) => setRoutes(d.items))))}>삭제</button>
                                </td>
                              </tr>
                            ))
                          )}
                        </tbody>
                      </table>
                    </div>
                  </>
                ) : (
                  /* 경로 편집 화면 */
                  <div className="route-editor">
                    <div className="route-editor__top">
                      <div className="route-editor__fields">
                        <label>
                          경로 이름
                          <input value={rName} onChange={(e) => setRName(e.target.value)} placeholder="예: J1→J2 이동" />
                        </label>
                      </div>
                      <div className="route-editor__actions">
                        <button className="tasks-btn" onClick={cancelRouteEdit}>취소</button>
                        <button className="tasks-btn tasks-btn--primary" onClick={handleRouteSave}>경로 저장</button>
                      </div>
                    </div>

                    <div className="route-editor__body">
                      {/* 왼쪽: 맵 뷰 */}
                      <div className="route-editor__map">
                        <RouteMapView
                          pois={pois}
                          selectedWaypoints={rWaypoints.map((w) => ({ poi_id: w.poi_id, waypoint_type: w.waypoint_type }))}
                          onPoiClick={(poi) => addWaypoint(poi)}
                        />
                      </div>

                      {/* 오른쪽: 선택된 웨이포인트 카드 */}
                      <div className="route-editor__waypoints">
                        <h4>경로 순서</h4>
                        {rWaypoints.length === 0 ? (
                          <div className="route-editor__empty">왼쪽에서 POI를 클릭하여 추가하세요</div>
                        ) : (
                          <div className="route-editor__cards">
                            {rWaypoints.map((w, idx) => (
                              <div key={`${w.poi_id}-${idx}`}>
                                <div className="route-card">
                                  <div className="route-card__header">
                                    <span className="route-card__order">{idx + 1}</span>
                                    <button className="route-card__remove" onClick={() => removeWaypoint(idx)}>✕</button>
                                  </div>
                                  <div className="route-card__name">{w.poi_name}</div>
                                  <select
                                    className="route-card__type-select"
                                    value={w.waypoint_type}
                                    onChange={(e) => changeWaypointType(idx, e.target.value)}
                                  >
                                    <option value="pickup">픽업</option>
                                    <option value="dropoff">드롭오프</option>
                                    <option value="standby">대기</option>
                                    <option value="charging">충전</option>
                                  </select>
                                  <div className="route-card__wait">
                                    <span>대기</span>
                                    <input
                                      type="number"
                                      min={0}
                                      value={w.wait_sec}
                                      onChange={(e) => changeWaypointWait(idx, Number(e.target.value))}
                                    />
                                    <span>초</span>
                                  </div>
                                  <div className="route-card__arrows">
                                    <button onClick={() => moveWaypoint(idx, -1)} disabled={idx === 0}>▲</button>
                                    <button onClick={() => moveWaypoint(idx, 1)} disabled={idx === rWaypoints.length - 1}>▼</button>
                                  </div>
                                </div>
                                {idx < rWaypoints.length - 1 && <div className="route-card__connector">│</div>}
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>
                  </div>
                )}
              </>
            )}
          </div>

          {/* ══════ 스케줄 생성/편집 모달 (CLOi 스타일) ══════ */}
          {showScheduleModal && (
            <div className="tasks-modal-backdrop" onClick={() => setShowScheduleModal(false)}>
              <div className="tasks-modal tasks-modal--wide" onClick={(e) => e.stopPropagation()}>
                <h3>{editTask ? "스케줄 편집" : "스케줄 추가"}</h3>
                <div className="tasks-modal__form">
                  <label>
                    스케줄 이름
                    <input value={fName} onChange={(e) => setFName(e.target.value)} placeholder="예: 오전 자재 이동" />
                  </label>
                  <label>
                    로봇
                    <select value={fRobotId} onChange={(e) => setFRobotId(Number(e.target.value))}>
                      <option value={0}>선택</option>
                      {robots.map((r) => (
                        <option key={r.id} value={r.id}>{r.name} ({r.ip})</option>
                      ))}
                    </select>
                  </label>
                  <label>
                    업무 경로
                    <select value={fRouteId} onChange={(e) => setFRouteId(Number(e.target.value))}>
                      <option value={0}>경로 선택</option>
                      {routes.map((r) => (
                        <option key={r.id} value={r.id}>{r.name}</option>
                      ))}
                    </select>
                  </label>

                  {/* 시간 설정 */}
                  <div className="tasks-modal__section">
                    <span className="tasks-modal__section-label">실행 시간</span>
                    <div className="tasks-modal__time-wheel-row">
                      <WheelTimePicker
                        label="시작"
                        value={fStartTime}
                        onChange={setFStartTime}
                      />
                      <span className="tasks-modal__time-sep">~</span>
                      <WheelTimePicker
                        label="종료"
                        value={fEndTime}
                        onChange={setFEndTime}
                        allowEmpty
                      />
                    </div>
                  </div>

                  {/* 날짜 / 반복 */}
                  <div className="tasks-modal__section">
                    <span className="tasks-modal__section-label">시작 날짜</span>
                    <input type="date" value={fStartDate} onChange={(e) => setFStartDate(e.target.value)} />
                  </div>

                  <div className="tasks-modal__section">
                    <span className="tasks-modal__section-label">반복</span>
                    <div className="tasks-modal__toggle-row">
                      {(["once", "daily", "weekly"] as RepeatType[]).map((rt) => (
                        <button
                          key={rt}
                          type="button"
                          className={`tasks-toggle-btn ${fRepeatType === rt ? "tasks-toggle-btn--active" : ""}`}
                          onClick={() => setFRepeatType(rt)}
                        >
                          {rt === "once" ? "1회" : rt === "daily" ? "매일" : "매주"}
                        </button>
                      ))}
                    </div>
                  </div>

                  {fRepeatType === "weekly" && (
                    <div className="tasks-modal__section">
                      <span className="tasks-modal__section-label">요일 선택</span>
                      <div className="tasks-modal__dow-row">
                        {DAY_VALUES.map((d, i) => (
                          <button
                            key={d}
                            type="button"
                            className={`tasks-dow-btn ${fRepeatDays.has(d) ? "tasks-dow-btn--active" : ""}`}
                            onClick={() => toggleDow(d)}
                          >
                            {DAY_LABELS[i]}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}

                  {fRepeatType !== "once" && (
                    <div className="tasks-modal__section">
                      <span className="tasks-modal__section-label">반복 종료</span>
                      <div className="tasks-modal__row">
                        <label className="tasks-modal__radio">
                          <input type="radio" name="endtype" checked={!fEndDate} onChange={() => setFEndDate("")} />
                          없음
                        </label>
                        <label className="tasks-modal__radio">
                          <input type="radio" name="endtype" checked={!!fEndDate} onChange={() => setFEndDate(fStartDate)} />
                          날짜 지정
                        </label>
                        {fEndDate && (
                          <input type="date" value={fEndDate} onChange={(e) => setFEndDate(e.target.value)} className="tasks-modal__date-input" />
                        )}
                      </div>
                    </div>
                  )}
                </div>
                <div className="tasks-modal__footer">
                  <button className="tasks-btn" onClick={() => setShowScheduleModal(false)}>취소</button>
                  <button className="tasks-btn tasks-btn--primary" onClick={handleScheduleSubmit}>
                    {editTask ? "저장" : "스케줄 추가"}
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* 알림/확인 모달 */}
          {alertModal && (
            <div className="tasks-modal-backdrop" onClick={() => setAlertModal(null)}>
              <div className="tasks-alert-modal" onClick={(e) => e.stopPropagation()}>
                <p className="tasks-alert-modal__msg">{alertModal.message}</p>
                <div className="tasks-alert-modal__footer">
                  {alertModal.onConfirm ? (
                    <>
                      <button className="tasks-btn" onClick={() => setAlertModal(null)}>취소</button>
                      <button className="tasks-btn tasks-btn--primary" onClick={() => { alertModal.onConfirm!(); setAlertModal(null); }}>확인</button>
                    </>
                  ) : (
                    <button className="tasks-btn tasks-btn--primary" onClick={() => setAlertModal(null)}>확인</button>
                  )}
                </div>
              </div>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
