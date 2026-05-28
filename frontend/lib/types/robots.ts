import type { DeviceTask } from "./monitoring";

export type RunState = "EXECUTING" | "IDLE" | "CHARGING";

export type OnlineStatus = "Online" | "Offline";

export type RobotType = "lifting" | "serving";

export type RobotDevice = {
  id: string;
  sn: string;
  robotName: string;
  model: string;
  runState: RunState | null;
  online: boolean;
  signal: number | null;
  power: number | null;
  enable: boolean;
  nickname: string | null;
  ip: string | null;
  axbotVersion: string | null;
  platform: string | null;
  busiName: string | null;
  buildingName: string | null;
  robotType: RobotType;
  currentTask: DeviceTask[];
};

export type RobotFilterState = {
  searchText: string;
  model: string;
  runState: RunState | "";
  online: OnlineStatus | "";
};

export type RobotFilterProps = {
  filters: RobotFilterState;
  models: string[];
  onFilterChange: (filters: RobotFilterState) => void;
  onSearch: () => void;
};

export type RobotTableProps = {
  devices: RobotDevice[];
  onEnableToggle: (deviceId: string) => void;
  onInfoClick: (deviceId: string) => void;
  togglingDeviceId: string | null;
};

export type RobotDeviceInfoProps = {
  device: RobotDevice | null;
  onClose: () => void;
  onEnableToggle: (deviceId: string) => void;
  togglingDeviceId: string | null;
  showChargingStation?: boolean;
  readOnly?: boolean;
};

export type ConfirmModalProps = {
  open: boolean;
  title: string;
  message: string;
  onConfirm: () => void;
  onCancel: () => void;
};

/* ─── Settings ─── */

export type DeploymentStatus = "DEPLOYED" | "UNDEPLOYED";

export type Business = {
  id: string;
  name: string;
  value?: string;
};

export type Building = {
  id: string;
  name: string;
  businessId: string;
};

export type DeploymentPayload = {
  businessId: string;
  buildingId: string;
  deploymentDate: string;
  status: "DEPLOYED";
};

export type RobotSettingsModalProps = {
  device: RobotDevice | null;
  open: boolean;
  onClose: () => void;
  onSave: (deviceId: string, data: DeploymentPayload) => void;
};

export type SettingsDetailModalProps = {
  device: RobotDevice | null;
  open: boolean;
  onClose: () => void;
};

export type OperatedRobotTableProps = {
  devices: RobotDevice[];
};
