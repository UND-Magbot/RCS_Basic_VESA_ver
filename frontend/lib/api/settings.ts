import { apiFetch } from "@/lib/api";
import type { BusinessGroup } from "@/lib/types/settings";

// ─── 사용자 목록 ─────────────────────────────────────

interface UserResponseItem {
  id: number;
  login_id: string;
  username: string;
  is_active: boolean;
  role: number;
  role_name: string;
}

interface UserListResponse {
  total: number;
  items: UserResponseItem[];
}

const ROLE_GROUP_LABEL: Record<number, string> = {
  1: "관리자",
  2: "사용자",
};

export async function fetchUsers(): Promise<BusinessGroup[]> {
  const data = await apiFetch<UserListResponse>("/api/users?limit=500");

  const activeUsers = data.items.filter((u) => u.is_active);

  const grouped = new Map<number, UserResponseItem[]>();
  for (const user of activeUsers) {
    const list = grouped.get(user.role) ?? [];
    list.push(user);
    grouped.set(user.role, list);
  }

  const groups: BusinessGroup[] = [];
  for (const role of [1, 2]) {
    const users = grouped.get(role);
    if (!users?.length) continue;
    groups.push({
      id: `role-${role}`,
      name: ROLE_GROUP_LABEL[role] ?? `역할 ${role}`,
      users: users.map((u) => ({
        id: u.id,
        loginId: u.login_id,
        username: u.username,
        role: u.role,
        roleName: u.role_name,
      })),
    });
  }

  return groups;
}

// ─── 비밀번호 ────────────────────────────────────────

export async function verifyPassword(
  password: string
): Promise<{ valid: boolean }> {
  const token = localStorage.getItem("auth_token");
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers["Authorization"] = `Bearer ${token}`;

  return apiFetch<{ valid: boolean }>("/api/auth/verify-password", {
    method: "POST",
    headers,
    body: JSON.stringify({ password }),
  });
}

export async function changePassword(
  currentPassword: string,
  newPassword: string
): Promise<void> {
  const token = localStorage.getItem("auth_token");
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers["Authorization"] = `Bearer ${token}`;

  await apiFetch("/api/auth/change-password", {
    method: "PUT",
    headers,
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
  });
}

// ─── DB 백업 ─────────────────────────────────────────

function generateMockSql(): string {
  return [
    "-- Hyundai Glovis RCS Database Backup (Mock)",
    `-- Generated: ${new Date().toISOString()}`,
    "",
    "CREATE TABLE users (",
    "  id SERIAL PRIMARY KEY,",
    "  login_id VARCHAR(50) NOT NULL UNIQUE,",
    "  username VARCHAR(100) NOT NULL,",
    "  role INTEGER NOT NULL DEFAULT 2,",
    "  is_active BOOLEAN NOT NULL DEFAULT TRUE,",
    "  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
    ");",
    "",
    "INSERT INTO users (id, login_id, username, role) VALUES",
    "  (1, 'admin', '관리자', 1),",
    "  (2, 'user01', '사용자01', 2),",
    "  (3, 'user02', '사용자02', 2);",
    "",
    "CREATE TABLE robots (",
    "  id SERIAL PRIMARY KEY,",
    "  name VARCHAR(100) NOT NULL,",
    "  status VARCHAR(20) DEFAULT 'idle'",
    ");",
    "",
    "INSERT INTO robots (id, name, status) VALUES",
    "  (1, 'AGV-001', 'running'),",
    "  (2, 'AGV-002', 'idle'),",
    "  (3, 'AGV-003', 'charging');",
    "",
  ].join("\n");
}

// GET /api/backup/db
export async function downloadDbBackup(): Promise<void> {
  const token = localStorage.getItem("auth_token");
  let blob: Blob;
  let suggestedName = "db_backup.sql";

  try {
    const res = await fetch(
      `${process.env.NEXT_PUBLIC_API_URL}/api/backup/db`,
      { headers: { Authorization: `Bearer ${token}` } }
    );

    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(
        (body as { detail?: string }).detail ?? "DB 백업에 실패했습니다."
      );
    }

    const disposition = res.headers.get("Content-Disposition");
    const nameMatch = disposition?.match(/filename="?([^"]+)"?/);
    suggestedName = nameMatch?.[1] ?? suggestedName;
    blob = await res.blob();
  } catch {
    // API 미연결 시 mock SQL 데이터로 fallback
    const now = new Date().toISOString().slice(0, 10);
    suggestedName = `db_backup_${now}.sql`;
    blob = new Blob([generateMockSql()], { type: "application/sql" });
  }

  if ("showSaveFilePicker" in window) {
    const handle = await (
      window as unknown as {
        showSaveFilePicker: (opts: {
          suggestedName: string;
          types: { description: string; accept: Record<string, string[]> }[];
        }) => Promise<FileSystemFileHandle>;
      }
    ).showSaveFilePicker({
      suggestedName,
      types: [
        {
          description: "SQL Files",
          accept: { "application/sql": [".sql"] },
        },
      ],
    });
    const writable = await handle.createWritable();
    await writable.write(blob);
    await writable.close();
  } else {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = suggestedName;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }
}
