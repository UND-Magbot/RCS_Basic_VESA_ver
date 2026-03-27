"use client";

import { useState, useEffect } from "react";
import { TopBar } from "../components/shell/TopBar";
import { SideNav, defaultNavItems } from "../components/shell/SideNav";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
  PieChart, Pie, Cell,
} from "recharts";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8002";

interface CompletionData {
  date: string;
  total: number;
  completed: number;
  failed: number;
  cancelled: number;
}
interface RobotData {
  robot_name: string;
  task_count: number;
  total_minutes: number;
}
interface RouteData {
  route_name: string;
  count: number;
  avg_minutes: number;
}

const COLORS = ["#5a8ff5", "#36dfc8", "#3de0a4", "#f5b731", "#f56565"];

export default function StatsPage() {
  const [navCollapsed, setNavCollapsed] = useState(true);
  const [currentDateTime, setCurrentDateTime] = useState("");
  const [days, setDays] = useState(7);
  const [startDate, setStartDate] = useState(() => {
    const d = new Date();
    d.setDate(d.getDate() - 7);
    return d.toISOString().split("T")[0];
  });
  const [endDate, setEndDate] = useState(() => new Date().toISOString().split("T")[0]);
  const [useCustomDate, setUseCustomDate] = useState(false);
  const [completion, setCompletion] = useState<CompletionData[]>([]);
  const [robots, setRobots] = useState<RobotData[]>([]);
  const [routes, setRoutes] = useState<RouteData[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fmt = () => {
      const now = new Date();
      return `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,"0")}-${String(now.getDate()).padStart(2,"0")} ${String(now.getHours()).padStart(2,"0")}:${String(now.getMinutes()).padStart(2,"0")}`;
    };
    setCurrentDateTime(fmt());
    const t = setInterval(() => setCurrentDateTime(fmt()), 60000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    const fetchAll = async () => {
      setLoading(true);
      try {
        const params = useCustomDate
          ? `start_date=${startDate}&end_date=${endDate}`
          : `days=${days}`;
        const [c, r, d] = await Promise.all([
          fetch(`${API}/api/tasks/stats/completion?${params}`).then((r) => r.json()),
          fetch(`${API}/api/tasks/stats/robot-utilization?${params}`).then((r) => r.json()),
          fetch(`${API}/api/tasks/stats/route-duration?${params}`).then((r) => r.json()),
        ]);
        setCompletion(c);
        setRobots(r);
        setRoutes(d);
      } catch {}
      setLoading(false);
    };
    fetchAll();
    const interval = setInterval(fetchAll, 30000);
    return () => clearInterval(interval);
  }, [days, startDate, endDate, useCustomDate]);

  const totalCompleted = completion.reduce((s, d) => s + d.completed, 0);
  const totalFailed = completion.reduce((s, d) => s + d.failed, 0);
  const totalCancelled = completion.reduce((s, d) => s + d.cancelled, 0);
  const totalAll = totalCompleted + totalFailed + totalCancelled;
  const completionRate = totalAll > 0 ? Math.round((totalCompleted / totalAll) * 100) : 0;

  const pieData = [
    { name: "완료", value: totalCompleted },
    { name: "실패", value: totalFailed },
    { name: "취소", value: totalCancelled },
  ].filter((d) => d.value > 0);

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
    <div className="stats-page">
      <div className="stats-page__header">
        <h2>데이터 통계</h2>
        <div className="stats-page__controls">
          <div className="stats-page__period">
            {[7, 14, 30].map((d) => (
              <button
                key={d}
                className={`stats-page__period-btn${!useCustomDate && days === d ? " stats-page__period-btn--active" : ""}`}
                onClick={() => { setUseCustomDate(false); setDays(d); }}
              >
                {d}일
              </button>
            ))}
          </div>
          <div className="stats-page__date-range">
            <input
              type="date"
              className="stats-page__date-input"
              value={startDate}
              onChange={(e) => { setStartDate(e.target.value); setUseCustomDate(true); }}
            />
            <span style={{ color: "var(--text-muted)" }}>~</span>
            <input
              type="date"
              className="stats-page__date-input"
              value={endDate}
              onChange={(e) => { setEndDate(e.target.value); setUseCustomDate(true); }}
            />
          </div>
        </div>
      </div>

      {loading ? (
        <div className="stats-page__loading">
          <div className="loading-spinner" />
        </div>
      ) : (
        <>
          {/* 요약 카드 */}
          <div className="stats-page__summary">
            <div className="stats-card stats-card--total">
              <span className="stats-card__icon">📋</span>
              <div>
                <span className="stats-card__label">총 작업</span>
                <span className="stats-card__value">{totalAll}<span className="stats-card__unit">건</span></span>
              </div>
            </div>
            <div className="stats-card stats-card--success">
              <span className="stats-card__icon">✅</span>
              <div>
                <span className="stats-card__label">성공</span>
                <span className="stats-card__value" style={{ color: "#3de0a4" }}>{totalCompleted}<span className="stats-card__unit">건</span></span>
              </div>
            </div>
            <div className="stats-card stats-card--fail">
              <span className="stats-card__icon">❌</span>
              <div>
                <span className="stats-card__label">실패</span>
                <span className="stats-card__value" style={{ color: "#f56565" }}>{totalFailed}<span className="stats-card__unit">건</span></span>
              </div>
            </div>
            <div className="stats-card stats-card--rate">
              <span className="stats-card__icon">📊</span>
              <div>
                <span className="stats-card__label">성공률</span>
                <span className="stats-card__value" style={{ color: "#5a8ff5" }}>{completionRate}<span className="stats-card__unit">%</span></span>
              </div>
            </div>
          </div>

          {/* 일별 작업 현황 */}
          <div className="stats-page__section">
            <h3>📈 일별 작업 현황</h3>
            <div className="stats-page__chart">
              {completion.length > 0 ? (
                <ResponsiveContainer width="100%" height={300}>
                  <BarChart data={completion} barGap={2} barCategoryGap="20%">
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" vertical={false} />
                    <XAxis
                      dataKey="date"
                      tick={{ fill: "rgba(255,255,255,0.6)", fontSize: 12 }}
                      tickFormatter={(v) => { const d = new Date(v); return `${d.getMonth()+1}/${d.getDate()}`; }}
                      axisLine={{ stroke: "rgba(255,255,255,0.1)" }}
                    />
                    <YAxis
                      tick={{ fill: "rgba(255,255,255,0.6)", fontSize: 12 }}
                      allowDecimals={false}
                      axisLine={{ stroke: "rgba(255,255,255,0.1)" }}
                      unit="건"
                    />
                    <Tooltip
                      contentStyle={{ background: "#0f1923", border: "1px solid rgba(90,143,245,0.3)", borderRadius: 8, boxShadow: "0 4px 16px rgba(0,0,0,0.4)" }}
                      labelStyle={{ color: "rgba(255,255,255,0.92)", fontWeight: 600, marginBottom: 4 }}
                      itemStyle={{ color: "rgba(255,255,255,0.8)" }}
                      labelFormatter={(v) => `${v}`}
                      cursor={{ fill: "rgba(90,143,245,0.08)" }}
                    />
                    <Legend
                      wrapperStyle={{ paddingTop: 12 }}
                      iconType="circle"
                      iconSize={8}
                    />
                    <Bar dataKey="completed" name="성공" fill="#3de0a4" radius={[4, 4, 0, 0]} />
                    <Bar dataKey="failed" name="실패" fill="#f56565" radius={[4, 4, 0, 0]} />
                    <Bar dataKey="cancelled" name="취소" fill="#8b8fa3" radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              ) : (
                <div className="stats-page__empty">해당 기간에 데이터가 없습니다</div>
              )}
            </div>
          </div>

          <div className="stats-page__row">
            {/* 작업 성공률 도넛 */}
            <div className="stats-page__section stats-page__section--half">
              <h3>🎯 작업 성공률</h3>
              <div className="stats-page__chart" style={{ position: "relative" }}>
                {pieData.length > 0 ? (
                  <>
                    <div style={{
                      position: "absolute", top: "50%", left: "50%",
                      transform: "translate(-50%, -50%)",
                      textAlign: "center", zIndex: 1, pointerEvents: "none",
                    }}>
                      <div style={{ fontSize: 28, fontWeight: 700, color: "#5a8ff5" }}>{completionRate}%</div>
                      <div style={{ fontSize: 11, color: "rgba(255,255,255,0.5)" }}>성공률</div>
                    </div>
                    <ResponsiveContainer width="100%" height={240}>
                      <PieChart>
                        <Pie
                          data={pieData}
                          cx="50%"
                          cy="50%"
                          innerRadius={60}
                          outerRadius={90}
                          dataKey="value"
                          strokeWidth={0}
                        >
                          {pieData.map((entry, i) => (
                            <Cell key={i} fill={
                              entry.name === "완료" ? "#3de0a4" :
                              entry.name === "실패" ? "#f56565" : "#8b8fa3"
                            } />
                          ))}
                        </Pie>
                        <Tooltip
                          contentStyle={{ background: "#0f1923", border: "1px solid rgba(90,143,245,0.3)", borderRadius: 8 }}
                          formatter={(value: unknown) => [`${value}건`]}
                        />
                        <Legend iconType="circle" iconSize={8} />
                      </PieChart>
                    </ResponsiveContainer>
                  </>
                ) : (
                  <div className="stats-page__empty">해당 기간에 데이터가 없습니다</div>
                )}
              </div>
            </div>

            {/* 로봇별 가동 현황 */}
            <div className="stats-page__section stats-page__section--half">
              <h3>🤖 로봇별 작업 현황</h3>
              <div className="stats-page__chart">
                {robots.length > 0 ? (
                  <ResponsiveContainer width="100%" height={240}>
                    <BarChart data={robots} layout="vertical" barSize={20}>
                      <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" horizontal={false} />
                      <XAxis
                        type="number"
                        tick={{ fill: "rgba(255,255,255,0.6)", fontSize: 12 }}
                        allowDecimals={false}
                        unit="건"
                        axisLine={{ stroke: "rgba(255,255,255,0.1)" }}
                      />
                      <YAxis
                        dataKey="robot_name"
                        type="category"
                        tick={{ fill: "rgba(255,255,255,0.72)", fontSize: 12 }}
                        width={140}
                        axisLine={false}
                        tickLine={false}
                      />
                      <Tooltip
                        contentStyle={{ background: "#0f1923", border: "1px solid rgba(90,143,245,0.3)", borderRadius: 8 }}
                        formatter={(value: unknown, name: unknown) => {
                          if (name === "작업 수") return [`${value}건`];
                          return [`${value}분`];
                        }}
                        cursor={{ fill: "rgba(90,143,245,0.08)" }}
                      />
                      <Legend iconType="circle" iconSize={8} />
                      <Bar dataKey="task_count" name="작업 수" fill="#5a8ff5" radius={[0, 4, 4, 0]} />
                      <Bar dataKey="total_minutes" name="가동 시간(분)" fill="#36dfc8" radius={[0, 4, 4, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                ) : (
                  <div className="stats-page__empty">해당 기간에 데이터가 없습니다</div>
                )}
              </div>
            </div>
          </div>

          {/* 경로별 평균 소요 시간 */}
          <div className="stats-page__section">
            <h3>⏱️ 경로별 평균 소요 시간</h3>
            <div className="stats-page__chart">
              {routes.length > 0 ? (
                <ResponsiveContainer width="100%" height={300}>
                  <BarChart data={routes} barSize={40} barCategoryGap="20%">
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" vertical={false} />
                    <XAxis
                      dataKey="route_name"
                      tick={{ fill: "rgba(255,255,255,0.72)", fontSize: 12 }}
                      axisLine={{ stroke: "rgba(255,255,255,0.1)" }}
                    />
                    <YAxis
                      tick={{ fill: "rgba(255,255,255,0.6)", fontSize: 12 }}
                      unit="분"
                      axisLine={{ stroke: "rgba(255,255,255,0.1)" }}
                    />
                    <Tooltip
                      contentStyle={{ background: "#0f1923", border: "1px solid rgba(90,143,245,0.3)", borderRadius: 8 }}
                      formatter={(value: unknown) => [`${value}분`, "평균 소요 시간"]}
                      cursor={{ fill: "rgba(90,143,245,0.08)" }}
                    />
                    <Legend iconType="circle" iconSize={8} />
                    <Bar dataKey="avg_minutes" name="평균 소요 시간(분)" fill="#f5b731" radius={[4, 4, 0, 0]} />
                    <Bar dataKey="count" name="실행 횟수" fill="#5a8ff5" radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              ) : (
                <div className="stats-page__empty">해당 기간에 데이터가 없습니다</div>
              )}
            </div>
          </div>
        </>
      )}
    </div>
        </main>
      </div>
    </div>
    </>
  );
}
