export type WorkMode = "rack_pickup" | "delivery_no_rack" | "simple_move";
export type RobotType = "lifting" | "serving";

export const ROBOT_TYPE_WORK_MODES: Record<RobotType, WorkMode[]> = {
  lifting: ["rack_pickup", "delivery_no_rack", "simple_move"],
  serving: ["simple_move"],
};

export const ROBOT_TYPE_LABELS: Record<RobotType, string> = {
  lifting: "리프팅",
  serving: "서빙",
};

export const WORK_MODE_LABELS: Record<WorkMode, string> = {
  rack_pickup: "랙 픽업 (W1 → 배달 → 복귀)",
  delivery_no_rack: "배달 (랙 없이)",
  simple_move: "단순 이동",
};

export function isWorkModeAllowed(robotType: RobotType | string | null | undefined, mode: WorkMode | string): boolean {
  const type = (robotType || "lifting") as RobotType;
  const allowed = ROBOT_TYPE_WORK_MODES[type] || ["simple_move"];
  return allowed.includes(mode as WorkMode);
}
