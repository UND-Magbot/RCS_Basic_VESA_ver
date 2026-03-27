export interface BusinessGroup {
  id: string;
  name: string;
  users: BusinessUser[];
}

export interface BusinessUser {
  id: number;
  loginId: string;
  username: string;
  role: number;
  roleName: string;
}
