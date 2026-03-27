export type DevicePower = "online" | "offline";

export type DeviceStatus = "idle" | "running" | "charging" | "error" | "warning" | "disable";

export type DeviceRowProps = {
  id: string;
  name: string;
  power: DevicePower;
  battery: string;
  status: DeviceStatus;
  ip?: string;
  isExpanded?: boolean;
  onToggleExpand?: (deviceId: string) => void;
  onInfo?: (deviceId: string) => void;
  onRemote?: (deviceId: string, ip: string) => void;
};

export type DeviceTaskState =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "cancelled";

export type DeviceBaseInfo = {
  sn: string;
  robotName: string | null;
  model: string | null;
  nickname: string | null;
  axbotVersion: string | null;
  platform: string | null;
};

export type DeviceOperational = {
  busiName: string | null;
  online: boolean;
  runState: string | null;
  power: number | null;
  signal: number | null;
  enable: boolean;
};

export type DeviceTask = {
  taskId: string;
  state: DeviceTaskState;
  type: string;
  createTime: string;
  start: string;
  end: string;
  oper: string;
};

export type DeviceDetail = DeviceBaseInfo &
  DeviceOperational & {
    currentTask: DeviceTask[];
  };

export type OverlayItem = {
  label: string;
  checked: boolean;
};

export type OverlayCardProps = {
  items: OverlayItem[];
  onItemToggle: (label: string, checked: boolean) => void;
};

export type LayerButtonProps = {
  isActive: boolean;
  onToggle: () => void;
};

export type MapModeButtonProps = {
  mapMode: "2d" | "3d";
  onMapModeChange: (mode: "2d" | "3d") => void;
};
