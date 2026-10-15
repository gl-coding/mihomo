import axios from "axios";

const http = axios.create({
  baseURL: "/api",
  timeout: 30000,
  withCredentials: false,
});

export interface MihomoDelayItem {
  name: string;
  ok: boolean;
  delay?: number | null;
  testedAt?: number;
  message?: string;
}

export interface MihomoProxyGroup {
  name: string;
  now: string;
  options: string[];
}

export interface MihomoNodeInfo {
  type: string;
  udp: boolean | null;
  now?: string;
}

export interface MihomoSubscriptionProfile {
  id: string;
  name: string;
  url?: string;
  url_display: string;
  via_proxy: boolean;
  updated_at: string;
  active: boolean;
  has_cache: boolean;
  node_count?: number | null;
  traffic_upload?: number;
  traffic_download?: number;
  traffic_total?: number;
  expire_at?: number;
  profile_update_interval?: number;
}

export interface MihomoSubscriptionSettings {
  ok?: boolean;
  configured: boolean;
  url_display: string;
  via_proxy: boolean;
  updated_at: string;
  active_id: string;
  items: MihomoSubscriptionProfile[];
}

export interface MihomoConnectivityItem {
  id: string;
  name: string;
  reachable: boolean;
  state: string;
  http_status?: number | null;
  message?: string;
  ip?: string | null;
  elapsed_ms?: number;
  checked_at?: string;
}

export interface MihomoIpInfo {
  ok: boolean;
  ip?: string;
  label?: string;
  message?: string;
}

export const getMihomoStatus = () =>
  http.get<{ ok: boolean; running: boolean; version?: string; message?: string }>("/mihomo/status/").then((r) => r.data);

export const getMihomoSubscription = () =>
  http.get<MihomoSubscriptionSettings>("/mihomo/subscription/").then((r) => r.data);

export const saveMihomoSubscription = (payload: {
  id?: string;
  name?: string;
  url?: string;
  via_proxy?: boolean;
}) => http.post<MihomoSubscriptionSettings>("/mihomo/subscription/save/", payload).then((r) => r.data);

export const updateMihomoSubscription = (id?: string) =>
  http.post<MihomoSubscriptionSettings & { node_count?: number }>("/mihomo/subscription/update/", id ? { id } : {}).then((r) => r.data);

export const activateMihomoSubscription = (id: string) =>
  http.post<MihomoSubscriptionSettings & { node_count?: number }>("/mihomo/subscription/activate/", { id }).then((r) => r.data);

export const deleteMihomoSubscription = (id: string) =>
  http.post<MihomoSubscriptionSettings>("/mihomo/subscription/delete/", { id }).then((r) => r.data);

export const startMihomo = () =>
  http.post<{ ok: boolean; message: string; version?: string }>("/mihomo/start/", {}, { timeout: 60000 }).then((r) => r.data);

export const getMihomoProxies = () =>
  http.get<{
    ok: boolean;
    main_group: string;
    current: string;
    nodes: string[];
    groups: MihomoProxyGroup[];
    message?: string;
    node_info?: Record<string, MihomoNodeInfo>;
  }>("/mihomo/proxies/").then((r) => r.data);

export const selectMihomoNode = (node: string, group?: string) =>
  http.post<{ ok: boolean; message: string }>("/mihomo/select/", { node, group }).then((r) => r.data);

export const testMihomoDelay = (name: string) =>
  http.get<{ ok: boolean; delay?: number; message?: string }>("/mihomo/delay/", { params: { name }, timeout: 20000 }).then((r) => r.data);

export const batchMihomoDelay = (payload?: { names?: string[]; timeout?: number; workers?: number }) =>
  http.post<{ ok: boolean; message: string; items: MihomoDelayItem[] }>("/mihomo/delay-batch/", payload ?? {}, { timeout: 120000 }).then((r) => r.data);

export const checkMihomoIp = (params?: { node?: string; group?: string }) =>
  http.get<{
    ok: boolean;
    direct: MihomoIpInfo;
    proxy: MihomoIpInfo;
    message?: string;
    synced_groups?: string[];
  }>("/mihomo/ip-check/", { params, timeout: 45000 }).then((r) => r.data);

export const checkMihomoConnectivity = (targets: string[]) =>
  http.post<{ ok: boolean; items: MihomoConnectivityItem[] }>("/mihomo/connectivity/", { targets }, { timeout: 45000 }).then((r) => r.data);


export interface MihomoNetworkSettings {
  ok: boolean;
  supported: boolean;
  desktop: string;
  mode: "off" | "system" | "other" | "unknown";
  raw_mode?: string;
  target: string;
  addresses: Record<string, string>;
  matches_target: boolean;
  message: string;
}

export const getMihomoNetwork = () =>
  http.get<MihomoNetworkSettings>("/mihomo/network/").then((r) => r.data);

export const setMihomoNetwork = (mode: "off" | "system") =>
  http.post<MihomoNetworkSettings>("/mihomo/network/", { mode }, {
    headers: { "X-Mihomo-Network": "1" },
    timeout: 60000,
  }).then((r) => r.data);


export type MihomoProxyMode = "rule" | "global" | "direct";

export interface MihomoProxyModeSettings {
  ok: boolean;
  mode: MihomoProxyMode;
  tun_enabled: boolean;
  global_current: string;
  global_options: string[];
}

export const getMihomoProxyMode = () =>
  http.get<MihomoProxyModeSettings>("/mihomo/proxy-mode/").then((r) => r.data);

export const setMihomoProxyMode = (mode: MihomoProxyMode) =>
  http.post<MihomoProxyModeSettings>("/mihomo/proxy-mode/", { mode }, {
    headers: { "X-Mihomo-Network": "1" },
  }).then((r) => r.data);
