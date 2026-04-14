// 경로
export type TaskRouteWaypoint = {
  id?: number;
  poi_id: number;
  poi_name?: string;
  order: number;
  waypoint_type: "pickup" | "dropoff" | "standby" | "charging";
  world_x?: number;
  world_y?: number;
};

export type WorkMode = "rack_pickup" | "delivery_no_rack" | "simple_move";

export type TaskRoute = {
  id: number;
  name: string;
  work_mode?: WorkMode;
  waypoints: TaskRouteWaypoint[];
  is_active: boolean;
  created_at: string | null;
};

export type TaskRouteCreate = {
  name: string;
  work_mode?: WorkMode;
  waypoints: { poi_id: number; order: number; waypoint_type: string; wait_sec?: number }[];
};

// 스케줄
export type RepeatType = "once" | "daily" | "weekly";

export type ScheduledTask = {
  id: number;
  name: string;
  route_id: number;
  route_name: string | null;
  robot_id: number;
  robot_name: string | null;
  start_time: string;
  end_time: string | null;
  repeat_type: RepeatType;
  repeat_days: string | null;
  start_date: string;
  end_date: string | null;
  is_active: boolean;
  last_run_at: string | null;
  next_run_at: string | null;
  created_at: string | null;
};

export type ScheduledTaskCreate = {
  name: string;
  robot_id: number;
  route_id: number;
  start_time: string;
  end_time?: string | null;
  repeat_type: RepeatType;
  repeat_days?: string | null;
  start_date: string;
  end_date?: string | null;
};

// 이력
export type TaskHistory = {
  id: number;
  task_id: number | null;
  task_name: string | null;
  route_name: string | null;
  robot_id: number;
  robot_name: string | null;
  pickup_poi_name: string | null;
  dropoff_poi_name: string | null;
  status: "running" | "succeeded" | "failed" | "cancelled";
  started_at: string | null;
  finished_at: string | null;
  error_message: string | null;
};
