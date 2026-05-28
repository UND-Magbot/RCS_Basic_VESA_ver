const API = process.env.NEXT_PUBLIC_API_URL || "";

async function apiFetch<T>(url: string): Promise<T> {
  const res = await fetch(`${API}${url}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function apiPost<T>(url: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API}${url}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function apiPut<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(`${API}${url}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function apiDelete(url: string): Promise<void> {
  const res = await fetch(`${API}${url}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
}

import type {
  TaskRoute, TaskRouteCreate,
  ScheduledTask, ScheduledTaskCreate,
  TaskHistory,
} from "@/lib/types/tasks";

// ── 경로 ──
export function getRoutes(robotId?: number) {
  const q = robotId ? `?robot_id=${robotId}` : "";
  return apiFetch<{ total: number; items: TaskRoute[] }>(`/api/tasks/routes${q}`);
}

export function createRoute(data: TaskRouteCreate) {
  return apiPost<TaskRoute>("/api/tasks/routes", data);
}

export function updateRoute(id: number, data: Partial<TaskRouteCreate>) {
  return apiPut<TaskRoute>(`/api/tasks/routes/${id}`, data);
}

export function deleteRoute(id: number) {
  return apiDelete(`/api/tasks/routes/${id}`);
}

// ── 스케줄 ──
export function getTasks(params?: { is_active?: boolean }) {
  const q = params?.is_active != null ? `?is_active=${params.is_active}` : "";
  return apiFetch<{ total: number; items: ScheduledTask[] }>(`/api/tasks${q}`);
}

export function createTask(data: ScheduledTaskCreate) {
  return apiPost<ScheduledTask>("/api/tasks", data);
}

export function updateTask(id: number, data: Partial<ScheduledTaskCreate & { is_active: boolean }>) {
  return apiPut<ScheduledTask>(`/api/tasks/schedule/${id}`, data);
}

export function deleteTask(id: number) {
  return apiDelete(`/api/tasks/schedule/${id}`);
}

export function toggleTask(id: number) {
  return apiPost<ScheduledTask>(`/api/tasks/schedule/${id}/toggle`);
}

export function runTaskNow(id: number) {
  return apiPost<{ message: string; task_id: number }>(`/api/tasks/schedule/${id}/run`);
}

export function manualRun(robotId: number, routeId: number) {
  return apiPost<{ message: string; history_id: number }>("/api/tasks/manual-run", { robot_id: robotId, route_id: routeId });
}

// ── 배치 수동 배차 (여러 로봇 동시 + 반복) ──
export type BatchAssignment = {
  robot_id: number;
  work_mode: "rack_pickup" | "delivery_no_rack" | "simple_move";
  pickup_poi_id: number;
  dropoff_poi_id: number;
  wait_sec: number;
  /** null 또는 0 = 무한 반복 */
  repeat_count: number | null;
};

export type BatchRunResponse = {
  started: Array<{ robot_id: number; history_id: number; repeat: string }>;
  skipped: Array<{ robot_id: number; reason: string }>;
};

export function manualRunBatch(assignments: BatchAssignment[]) {
  return apiPost<BatchRunResponse>("/api/tasks/manual-run-batch", { assignments });
}

// ── 이력 ──
export function getTaskHistory(id: number, params?: { skip?: number; limit?: number }) {
  const q = new URLSearchParams();
  if (params?.skip) q.set("skip", String(params.skip));
  if (params?.limit) q.set("limit", String(params.limit));
  const qs = q.toString() ? `?${q}` : "";
  return apiFetch<{ total: number; items: TaskHistory[] }>(`/api/tasks/history/${id}${qs}`);
}

export function getAllHistory(params?: { skip?: number; limit?: number }) {
  const q = new URLSearchParams();
  if (params?.skip) q.set("skip", String(params.skip));
  if (params?.limit) q.set("limit", String(params.limit));
  const qs = q.toString() ? `?${q}` : "";
  return apiFetch<{ total: number; items: TaskHistory[] }>(`/api/tasks/history/all${qs}`);
}

// ── POI 락 (다중 로봇 조율) ──
export type PoiLockEntry = {
  poi_id: number;
  poi_name: string | null;
  robot_id: number;
  robot_name: string | null;
};

export function getPoiLocks() {
  return apiFetch<{ locks: PoiLockEntry[] }>("/api/tasks/poi-locks");
}

export function releasePoiLocks(robotId: number) {
  return apiDelete(`/api/tasks/poi-locks/robot/${robotId}`);
}

// ── Zone 락 ──
export type ZoneLockEntry = {
  zone_id: number;
  zone_name: string | null;
  robot_id: number;
  robot_name: string | null;
};

export function getZoneLocks() {
  return apiFetch<{ locks: ZoneLockEntry[] }>("/api/tasks/zone-locks");
}

export function releaseZoneLocks(robotId: number) {
  return apiDelete(`/api/tasks/zone-locks/robot/${robotId}`);
}
