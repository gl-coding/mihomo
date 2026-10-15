<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import NetworkSettings from "./NetworkSettings.vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { Refresh, Connection, Position, Odometer, VideoPlay, Plus, MoreFilled, CircleCheck, Document, ArrowRight, Clock, Guide } from "@element-plus/icons-vue";
import {
  getMihomoStatus, getMihomoProxies, selectMihomoNode, testMihomoDelay, checkMihomoIp,
  batchMihomoDelay, startMihomo, getMihomoSubscription, saveMihomoSubscription, updateMihomoSubscription,
  activateMihomoSubscription, deleteMihomoSubscription, checkMihomoConnectivity,
  type MihomoProxyGroup, type MihomoNodeInfo, type MihomoProxyModeSettings, type MihomoDelayItem, type MihomoSubscriptionSettings, type MihomoSubscriptionProfile, type MihomoConnectivityItem,
} from "./api";

const SPEED_CACHE_KEY = "mihomo-delay-results-v1";

type SpeedCachePayload = {
  mainGroup: string;
  nodesKey: string;
  sortOrder: string[];
  items: MihomoDelayItem[];
};

const nodesKey = (names: string[]) => names.join("\u0001");

const compareDelayRows = (a: MihomoDelayItem, b: MihomoDelayItem) => {
  const byName = a.name.localeCompare(b.name, "zh-CN");
  if (byName !== 0) return byName;
  if (a.ok && b.ok) return (a.delay ?? 99999) - (b.delay ?? 99999);
  if (a.ok) return -1;
  if (b.ok) return 1;
  return 0;
};

const mergeSortOrder = (saved: string[], nodeNames: string[]) => {
  const alive = new Set(nodeNames);
  const order = saved.filter((name) => alive.has(name));
  for (const name of nodeNames) {
    if (!order.includes(name)) order.push(name);
  }
  return order;
};

const loading = ref(false);
const running = ref(false);
const version = ref("");
const mainGroup = ref("🔰 选择节点");
const currentNode = ref("");
const selectedNode = ref("");
const nodes = ref<string[]>([]);
const nodeInfo = ref<Record<string, MihomoNodeInfo>>({});
const modeState = ref<MihomoProxyModeSettings | null>(null);
const proxyGroups = ref<MihomoProxyGroup[]>([]);
const chosenOverviewGroup = ref("");
const overviewSelectionError = ref("");
const overviewGroupOptions = computed(() => {
  if (modeState.value?.mode === "direct") return [];
  if (modeState.value?.mode === "global") {
    return proxyGroups.value.filter((group) => group.name === "GLOBAL");
  }
  return proxyGroups.value.filter((group) => group.name !== "GLOBAL");
});
const overviewSelectedGroup = computed(() => {
  const groups = overviewGroupOptions.value;
  return groups.find((group) => group.name === chosenOverviewGroup.value)
    || groups.find((group) => group.name === mainGroup.value) || groups[0] || null;
});
// 全局出口也能在第二行卡片修改，同步该接口读取的实际状态。
watch(modeState, (state) => {
  if (!state) return;
  const global = proxyGroups.value.find((group) => group.name === "GLOBAL");
  if (global) global.now = state.global_current;
  if (nodeInfo.value.GLOBAL) nodeInfo.value.GLOBAL.now = state.global_current;
});
const refreshToken = ref(0);
const extraDelayResults = ref<Record<string, MihomoDelayItem>>({});
const subscriptionLoading = ref(false);
const subscriptionError = ref("");
const nodeStateError = ref("");
const activeSubscription = computed(() => subscription.value?.items.find((row) => row.id === subscription.value?.active_id) || null);
const overviewNode = computed(() => {
  if (!running.value || nodeStateError.value) return { name: "", type: "", udp: null, chain: [] as string[] };
  if (modeState.value?.mode === "direct") return { name: "DIRECT", type: "Direct", udp: null, chain: [] as string[] };
  const root = overviewSelectedGroup.value?.now || (modeState.value?.mode === "global" ? modeState.value.global_current : currentNode.value);
  const chain: string[] = [];
  let name = root;
  while (name && !chain.includes(name)) {
    chain.push(name);
    const next = nodeInfo.value[name]?.now;
    if (!next || chain.includes(next)) break;
    name = next;
  }
  return { name, type: nodeInfo.value[name]?.type || "", udp: nodeInfo.value[name]?.udp ?? null, chain };
});
const overviewGroup = computed(() => modeState.value?.mode === "direct" ? "直连模式" : overviewSelectedGroup.value?.name || mainGroup.value);
const overviewDelay = computed(() => speedRows.value.find((row) => row.name === overviewNode.value.name) || extraDelayResults.value[overviewNode.value.name]);
const currentNodeTesting = computed(() => batchLoading.value || singleRowLoading.value.has(overviewNode.value.name));
const modeLabel = computed(() => modeState.value ? { rule: "规则", global: "全局", direct: "直连" }[modeState.value.mode] : "主策略组");
const scrollToSection = (id: string) => {
  const section = document.getElementById(id);
  section?.scrollIntoView({ behavior: "smooth", block: "start" });
  section?.focus({ preventScroll: true });
};

const directIp = ref("");
const proxyIp = ref("");
const directIpLabel = ref("");
const proxyIpLabel = ref("");
const ipLoading = ref(false);
const verifyAllLoading = ref(false);
const connectivityTargets = [
  { id: "google", name: "Google", host: "google.com" },
  { id: "chatgpt", name: "ChatGPT", host: "chatgpt.com" },
  { id: "ipify", name: "IPify", host: "api.ipify.org" },
];
const connectivityResults = ref<Record<string, MihomoConnectivityItem>>({});
const connectivityLoading = ref(new Set<string>());

const runConnectivity = async (targets: string[]) => {
  if (targets.some((id) => connectivityLoading.value.has(id))) return;
  targets.forEach((id) => connectivityLoading.value.add(id));
  try {
    const result = await checkMihomoConnectivity(targets);
    for (const item of result.items) connectivityResults.value[item.id] = item;
  } catch {
    ElMessage.error("连通性测试失败或超时，请稍后重试");
  } finally {
    targets.forEach((id) => connectivityLoading.value.delete(id));
  }
};

const speedRows = ref<MihomoDelayItem[]>([]);
const speedSortOrder = ref<string[]>([]);
const batchLoading = ref(false);
const singleRowLoading = ref(new Set<string>());
const applyingNode = ref<string | null>(null);
const startLoading = ref(false);
const subscription = ref<MihomoSubscriptionSettings | null>(null);
const subscriptionDialogVisible = ref(false);
const subscriptionEditingId = ref<string | null>(null);
const subscriptionName = ref("");
const subscriptionUrl = ref("");
const subscriptionProxy = ref(false);
const subscriptionSaving = ref(false);
const subscriptionUpdatingId = ref<string | null>(null);
const subscriptionActivatingId = ref<string | null>(null);
const subscriptionQuickUrl = ref("");

const subscriptionBusy = computed(
  () => subscriptionUpdatingId.value !== null || subscriptionActivatingId.value !== null,
);

const hostnameFromUrl = (url: string) => {
  try {
    return new URL(url).hostname;
  } catch {
    return "";
  }
};

const subscriptionHostname = (row: MihomoSubscriptionProfile) => {
  if (row.url) {
    const host = hostnameFromUrl(row.url);
    if (host) return host;
  }
  return row.url_display.replace(/^https?:\/\//, "").replace(/\/…$/, "") || "—";
};

const formatTrafficGB = (n?: number | null) => {
  if (n == null || n < 0 || Number.isNaN(n)) return "—";
  const gb = n / 1024 ** 3;
  if (gb >= 100) return `${Math.round(gb)}GB`;
  if (gb >= 10) return `${gb.toFixed(1)}GB`;
  return `${gb.toFixed(2)}GB`;
};

const subscriptionTrafficUsed = (row: MihomoSubscriptionProfile) =>
  (row.traffic_upload ?? 0) + (row.traffic_download ?? 0);

const subscriptionTrafficText = (row: MihomoSubscriptionProfile) => {
  const used = subscriptionTrafficUsed(row);
  const hasTraffic = row.traffic_upload != null || row.traffic_download != null || row.traffic_total != null;
  if (!hasTraffic) return "— / —";
  if (row.traffic_total != null && row.traffic_total > 0) {
    return `${formatTrafficGB(used)} / ${formatTrafficGB(row.traffic_total)}`;
  }
  return `${formatTrafficGB(used)} / —`;
};

const subscriptionTrafficPercent = (row: MihomoSubscriptionProfile) => {
  const total = row.traffic_total;
  if (total == null || total <= 0) return 0;
  return Math.min(100, Math.round((subscriptionTrafficUsed(row) / total) * 1000) / 10);
};

const subscriptionExpireShort = (row: MihomoSubscriptionProfile) => {
  const exp = row.expire_at;
  if (exp == null) return "—";
  if (exp === 0) return "不限";
  const d = new Date(exp * 1000);
  if (Number.isNaN(d.getTime())) return "—";
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
};

const formatRelativeUpdated = (iso?: string) => {
  if (!iso) return "尚未更新";
  const diff = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(diff) || diff < 0) return "刚刚";
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return "刚刚";
  if (mins < 60) return `${mins} 分钟前`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} 小时前`;
  return `${Math.floor(hours / 24)} 天前`;
};

const pasteQuickUrl = async () => {
  try {
    subscriptionQuickUrl.value = (await navigator.clipboard.readText()).trim();
  } catch {
    ElMessage.warning("无法读取剪贴板，请手动粘贴链接");
  }
};

const handleQuickImport = () => {
  const url = subscriptionQuickUrl.value.trim();
  if (!url) {
    ElMessage.warning("请先填写或粘贴订阅链接");
    return;
  }
  subscriptionEditingId.value = null;
  subscriptionName.value = hostnameFromUrl(url) || "新订阅";
  subscriptionUrl.value = url;
  subscriptionProxy.value = false;
  subscriptionDialogVisible.value = true;
};

const loadSubscription = async () => {
  if (subscriptionLoading.value) return;
  subscriptionLoading.value = true;
  subscriptionError.value = "";
  try { subscription.value = await getMihomoSubscription(); }
  catch { subscriptionError.value = "读取订阅设置失败"; ElMessage.error(subscriptionError.value); }
  finally { subscriptionLoading.value = false; }
};

const openAddSubscription = () => {
  subscriptionEditingId.value = null;
  subscriptionName.value = "";
  subscriptionUrl.value = "";
  subscriptionProxy.value = false;
  subscriptionDialogVisible.value = true;
};

const openEditSubscription = (row: MihomoSubscriptionProfile) => {
  subscriptionEditingId.value = row.id;
  subscriptionName.value = row.name;
  subscriptionUrl.value = row.url || "";
  subscriptionProxy.value = row.via_proxy;
  subscriptionDialogVisible.value = true;
};

const apiError = (error: unknown, fallback: string) =>
  (error as { response?: { data?: { message?: string } } })?.response?.data?.message || fallback;

const handleSaveSubscription = async () => {
  const name = subscriptionName.value.trim();
  const url = subscriptionUrl.value.trim();
  const isNew = !subscriptionEditingId.value;
  if (isNew && (!name || !url)) {
    ElMessage.warning("请填写订阅名称和链接"); return;
  }
  if (!isNew && !name) {
    ElMessage.warning("订阅名称不能为空"); return;
  }
  if (isNew && !url) {
    ElMessage.warning("请输入订阅链接"); return;
  }
  subscriptionSaving.value = true;
  try {
    const payload: { id?: string; name?: string; url?: string; via_proxy: boolean } = {
      via_proxy: subscriptionProxy.value,
    };
    if (subscriptionEditingId.value) payload.id = subscriptionEditingId.value;
    if (name) payload.name = name;
    if (url) payload.url = url;
    subscription.value = await saveMihomoSubscription(payload);
    subscriptionDialogVisible.value = false;
    ElMessage.success("订阅已保存");
  } catch (error: unknown) {
    ElMessage.error(apiError(error, "保存订阅失败"));
  } finally { subscriptionSaving.value = false; }
};

const handleUpdateProfile = async (row: MihomoSubscriptionProfile) => {
  if (subscriptionBusy.value || subscriptionSaving.value) return;
  subscriptionUpdatingId.value = row.id;
  try {
    const result = await updateMihomoSubscription(row.id);
    subscription.value = result;
    if (row.active) {
      clearSpeedCache();
      speedRows.value = [];
      selectedNode.value = "";
      connectivityResults.value = {};
      proxyIp.value = "";
      proxyIpLabel.value = "";
      await loadAll();
    }
    ElMessage.success(result.message || "订阅已更新");
  } catch (error: unknown) {
    ElMessage.error(apiError(error, "订阅更新失败或超时"));
  } finally { subscriptionUpdatingId.value = null; }
};

const handleActivateProfile = async (row: MihomoSubscriptionProfile) => {
  if (row.active || subscriptionBusy.value || subscriptionSaving.value || batchLoading.value || startLoading.value) return;
  subscriptionActivatingId.value = row.id;
  try {
    const result = await activateMihomoSubscription(row.id);
    subscription.value = result;
    clearSpeedCache();
    speedRows.value = [];
    selectedNode.value = "";
    connectivityResults.value = {};
    proxyIp.value = "";
    proxyIpLabel.value = "";
    ElMessage.success(result.message || "已切换订阅");
    await loadAll();
  } catch (error: unknown) {
    ElMessage.error(apiError(error, "启用订阅失败"));
  } finally { subscriptionActivatingId.value = null; }
};

const handleDeleteProfile = async (row: MihomoSubscriptionProfile) => {
  if (subscriptionBusy.value || subscriptionSaving.value) return;
  if (row.active) {
    ElMessage.warning("当前正在使用的订阅无法删除，请先启用其他订阅"); return;
  }
  try {
    await ElMessageBox.confirm(`确定删除订阅「${row.name}」？`, "删除订阅", { type: "warning" });
  } catch { return; }
  try {
    subscription.value = await deleteMihomoSubscription(row.id);
    ElMessage.success("订阅已删除");
  } catch (error: unknown) {
    ElMessage.error(apiError(error, "删除失败"));
  }
};

const speedSorted = computed(() => {
  const map = new Map(speedRows.value.map((r) => [r.name, r]));
  if (speedSortOrder.value.length) {
    const ordered = speedSortOrder.value
      .map((name) => map.get(name))
      .filter((row): row is MihomoDelayItem => row !== undefined);
    const rest = speedRows.value
      .filter((r) => !speedSortOrder.value.includes(r.name))
      .sort(compareDelayRows);
    return [...ordered, ...rest];
  }
  return [...speedRows.value].sort(compareDelayRows);
});

const loadSpeedCache = (mainGroup: string, nodeNames: string[]) => {
  try {
    const raw = localStorage.getItem(SPEED_CACHE_KEY);
    if (!raw) return { items: new Map<string, MihomoDelayItem>(), sortOrder: [] as string[] };
    const data = JSON.parse(raw) as SpeedCachePayload;
    if (data.mainGroup !== mainGroup || data.nodesKey !== nodesKey(nodeNames)) {
      return { items: new Map<string, MihomoDelayItem>(), sortOrder: [] as string[] };
    }
    const items = new Map((data.items || []).map((row) => [row.name, row]));
    return { items, sortOrder: mergeSortOrder(data.sortOrder || [], nodeNames) };
  } catch {
    return { items: new Map<string, MihomoDelayItem>(), sortOrder: [] as string[] };
  }
};

const persistSpeedCache = () => {
  if (!mainGroup.value || !nodes.value.length) return;
  const payload: SpeedCachePayload = {
    mainGroup: mainGroup.value,
    nodesKey: nodesKey(nodes.value),
    sortOrder: speedSortOrder.value.length
      ? mergeSortOrder(speedSortOrder.value, nodes.value)
      : [...speedRows.value].sort(compareDelayRows).map((r) => r.name),
    items: speedRows.value,
  };
  try {
    localStorage.setItem(SPEED_CACHE_KEY, JSON.stringify(payload));
  } catch {
    /* quota / private mode */
  }
};

const clearSpeedCache = () => {
  speedSortOrder.value = [];
  extraDelayResults.value = {};
  try { localStorage.removeItem(SPEED_CACHE_KEY); } catch { /* ignore */ }
};

const delayTagType = (ms: number | null | undefined) => {
  if (ms == null) return "info";
  if (ms < 150) return "success";
  if (ms < 400) return "warning";
  return "danger";
};

const runStartMihomo = async () => {
  startLoading.value = true;
  try {
    const res = await startMihomo();
    if (res.ok) {
      ElMessage.success(res.message);
      if (res.version) version.value = res.version;
      await loadAll();
      return;
    }
    ElMessage.error(res.message || "启动失败");
  } catch (e: unknown) {
    const msg = (e as { response?: { data?: { message?: string } } })?.response?.data?.message;
    ElMessage.error(msg || "启动失败或超时，请查看 devtools/mihomo/cache/mihomo.log");
  } finally {
    startLoading.value = false;
  }
};

const loadAll = async () => {
  loading.value = true;
  nodeStateError.value = "";
  try {
    const st = await getMihomoStatus();
    running.value = st.running;
    version.value = st.version || "";
    if (!st.running) {
      ElMessage.warning(st.message || "Mihomo 未运行");
      nodes.value = [];
      speedRows.value = [];
      currentNode.value = "";
      nodeInfo.value = {};
      proxyGroups.value = [];
      return;
    }
    const px = await getMihomoProxies();
    if (!px.ok) {
      nodeStateError.value = px.message || "读取节点失败";
      ElMessage.error(nodeStateError.value);
      return;
    }
    mainGroup.value = px.main_group;
    currentNode.value = px.current;
    nodes.value = px.nodes;
    nodeInfo.value = px.node_info || {};
    proxyGroups.value = px.groups;
    if (!px.groups.some((group) => group.name === chosenOverviewGroup.value)) chosenOverviewGroup.value = px.main_group;
    if (!selectedNode.value || !px.nodes.includes(selectedNode.value)) {
      selectedNode.value = px.current;
    }
    const cached = loadSpeedCache(px.main_group, px.nodes);
    const live = new Map(speedRows.value.map((r) => [r.name, r]));
    speedSortOrder.value = speedSortOrder.value.length
      ? mergeSortOrder(speedSortOrder.value, px.nodes)
      : cached.sortOrder;
    speedRows.value = px.nodes.map((name) => (
      live.get(name) ?? cached.items.get(name) ?? { name, ok: false, delay: null }
    ));
    if (!speedSortOrder.value.length && speedRows.value.some((r) => r.ok || r.delay != null)) {
      speedSortOrder.value = [...speedRows.value].sort(compareDelayRows).map((r) => r.name);
    }
    persistSpeedCache();
  } catch {
    nodeStateError.value = "无法读取当前节点，请刷新重试";
    ElMessage.error("加载失败");
  } finally {
    loading.value = false;
    refreshToken.value += 1;
  }
};

const refreshPage = () => Promise.allSettled([loadAll(), loadSubscription()]);

const applyNodeByName = async (name: string) => {
  if (!name) return;
  applyingNode.value = name;
  try {
    const res = await selectMihomoNode(name, mainGroup.value);
    if (!res.ok) {
      ElMessage.error(res.message);
      return;
    }
    ElMessage.success(res.message);
    selectedNode.value = name;
    currentNode.value = name;
    if (nodeInfo.value[mainGroup.value]) nodeInfo.value[mainGroup.value].now = name;
    const group = proxyGroups.value.find((item) => item.name === mainGroup.value);
    if (group) group.now = name;
    connectivityResults.value = {};
    proxyIp.value = "";
    proxyIpLabel.value = "";
  } catch {
    ElMessage.error("切换节点失败");
  } finally {
    applyingNode.value = null;
  }
};

const chooseOverviewGroup = (group: string) => {
  chosenOverviewGroup.value = group;
  overviewSelectionError.value = "";
};

const chooseOverviewNode = async (name: string) => {
  const group = overviewSelectedGroup.value;
  if (!group || group.now === name || !group.options.includes(name) || applyingNode.value !== null
      || loading.value || subscriptionBusy.value || modeState.value?.mode === "direct") return;
  const groupName = group.name;
  applyingNode.value = name;
  overviewSelectionError.value = "";
  try {
    const result = await selectMihomoNode(name, groupName);
    if (!result.ok) throw new Error(result.message);
    connectivityResults.value = {};
    proxyIp.value = "";
    proxyIpLabel.value = "";
    // 切换后读取实际组状态，不将尚未生效的选择显示为当前节点。
    if (groupName === mainGroup.value) selectedNode.value = name;
    await loadAll();
    if (proxyGroups.value.find((item) => item.name === groupName)?.now !== name) {
      overviewSelectionError.value = "当前组状态已变化，请确认实际选中的节点";
    } else {
      ElMessage.success(`已切换 ${groupName} → ${name}`);
    }
  } catch (error) {
    overviewSelectionError.value = apiError(error, "节点切换失败，请重试");
    await loadAll();
  } finally {
    applyingNode.value = null;
  }
};

const optionDelay = (name: string) => speedRows.value.find((row) => row.name === name) || extraDelayResults.value[name];

const patchRow = (name: string, patch: Partial<MihomoDelayItem>, opts?: { persist?: boolean }) => {
  if (!speedRows.value.some((row) => row.name === name)) {
    extraDelayResults.value[name] = { name, ok: false, ...extraDelayResults.value[name], ...patch, testedAt: Date.now() };
  }
  speedRows.value = speedRows.value.map((r) => (r.name === name ? {
    ...r, ...patch, testedAt: patch.ok !== undefined || patch.delay !== undefined ? Date.now() : r.testedAt,
  } : r));
  if (opts?.persist !== false) persistSpeedCache();
};

const runBatchSpeed = async () => {
  batchLoading.value = true;
  speedRows.value = nodes.value.map((name) => ({ name, ok: false, delay: null, message: "测速中…" }));
  try {
    const res = await batchMihomoDelay({ timeout: 5000, workers: 4 });
    speedRows.value = res.items.map((row) => ({ ...row, testedAt: Date.now() }));
    speedSortOrder.value = [...speedRows.value].sort(compareDelayRows).map((r) => r.name);
    persistSpeedCache();
    ElMessage.success(res.message);
  } catch {
    speedRows.value = speedRows.value.map((row) => ({ ...row, message: "测速失败或超时", testedAt: Date.now() }));
    persistSpeedCache();
    ElMessage.error("批量测速失败或超时");
  } finally {
    batchLoading.value = false;
  }
};

const testOneRow = async (row: MihomoDelayItem) => {
  if (singleRowLoading.value.has(row.name)) return;
  singleRowLoading.value.add(row.name);
  patchRow(row.name, { message: "测速中…" }, { persist: false });
  try {
    const res = await testMihomoDelay(row.name);
    if (res.ok) patchRow(row.name, { ok: true, delay: res.delay ?? null, message: undefined });
    else patchRow(row.name, { ok: false, delay: null, message: res.message || "失败" });
  } catch {
    patchRow(row.name, { ok: false, delay: null, message: "失败" });
  } finally {
    singleRowLoading.value.delete(row.name);
  }
};

const testCurrentNode = () => {
  const name = overviewNode.value.name;
  if (!name || name === "DIRECT" || name === "REJECT") return;
  void testOneRow(overviewDelay.value || { name, ok: false });
};

const pickNode = (name: string) => {
  selectedNode.value = name;
};

const runIpCheck = async (opts?: { quiet?: boolean }) => {
  ipLoading.value = true;
  directIp.value = "";
  proxyIp.value = "";
  directIpLabel.value = "";
  proxyIpLabel.value = "";
  try {
    const node = selectedNode.value || currentNode.value;
    const res = await checkMihomoIp(node ? { node, group: mainGroup.value } : undefined);
    directIp.value = res.direct?.ok ? (res.direct.ip || "—") : (res.direct?.message || "失败");
    proxyIp.value = res.proxy?.ok ? (res.proxy.ip || "—") : (res.proxy?.message || "失败");
    directIpLabel.value = res.direct?.ok ? (res.direct.label || "") : "";
    proxyIpLabel.value = res.proxy?.ok ? (res.proxy.label || "") : "";
    if (!opts?.quiet) {
      if (res.proxy?.ok) ElMessage.success("IP 验证完成");
      else ElMessage.warning(res.message || "代理 IP 获取失败");
    }
    return res;
  } catch {
    if (!opts?.quiet) ElMessage.error("IP 验证失败");
    return null;
  } finally {
    ipLoading.value = false;
  }
};

const runConnectivityVerify = () => {
  runConnectivity(connectivityTargets.map((item) => item.id));
};

const runOneClickVerify = async () => {
  if (!running.value || verifyAllLoading.value || subscriptionBusy.value) return;
  verifyAllLoading.value = true;
  try {
    await runIpCheck({ quiet: true });
    await runConnectivity(connectivityTargets.map((item) => item.id));
    ElMessage.success("一键验证完成");
  } catch {
    ElMessage.error("一键验证未完成");
  } finally {
    verifyAllLoading.value = false;
  }
};

onMounted(() => { loadAll(); loadSubscription(); });
</script>

<template>
  <div class="mh-page">
    <div class="mh-header">
      <div>
        <h2>代理管理（Mihomo）</h2>
        <p class="mh-sub">
          选节点、测<strong>延迟</strong>（Mihomo delay，非下载带宽）与出口 IP。未运行时请点击「启动 Mihomo」。
        </p>
      </div>
      <div class="mh-header-actions">
        <el-button v-if="!running" type="success" :icon="VideoPlay" :loading="startLoading" @click="runStartMihomo">
          启动 Mihomo
        </el-button>
        <el-button :icon="Refresh" :loading="loading" @click="refreshPage">刷新</el-button>
      </div>
    </div>

    <el-alert v-if="!running && !loading" type="warning" show-icon :closable="false" style="margin-bottom:16px"
              title="Mihomo 未连接">
      <template #default>
        <p class="mh-alert-p">可在本页启动（等价于 <code>./devtools/mihomo/run.sh</code>），或于服务器手动执行该脚本。</p>
      </template>
    </el-alert>

    <div class="mh-overview-grid">
      <section class="mh-overview-card" aria-labelledby="current-subscription-title">
        <div class="mh-overview-head">
          <h3 id="current-subscription-title"><el-icon><Document /></el-icon>当前订阅</h3>
          <div class="mh-overview-tools">
            <el-button text circle :icon="Refresh" :loading="subscriptionLoading || subscriptionUpdatingId === activeSubscription?.id"
                       :disabled="subscriptionBusy || subscriptionSaving || subscriptionLoading"
                       aria-label="更新当前订阅" @click="activeSubscription ? handleUpdateProfile(activeSubscription) : loadSubscription()" />
            <el-button text :icon="ArrowRight" size="small" @click="scrollToSection('subscriptions')">订阅管理</el-button>
          </div>
        </div>
        <el-skeleton v-if="subscriptionLoading && !subscription" :rows="3" animated />
        <p v-else-if="subscriptionError" class="mh-fail" role="alert">{{ subscriptionError }}</p>
        <template v-else-if="activeSubscription">
          <div class="mh-overview-title-row">
            <strong class="mh-overview-name" :title="activeSubscription.name">{{ activeSubscription.name }}</strong>
            <el-tag type="success" size="small" effect="plain">当前配置</el-tag>
          </div>
          <p class="mh-overview-source" :title="subscriptionHostname(activeSubscription)">{{ subscriptionHostname(activeSubscription) }}<template v-if="activeSubscription.node_count != null"> · {{ activeSubscription.node_count }} 个节点</template></p>
          <div class="mh-overview-quota">
            <span>已用 / 总量</span><strong>{{ subscriptionTrafficText(activeSubscription) }}</strong>
          </div>
          <el-progress :percentage="subscriptionTrafficPercent(activeSubscription)" :show-text="false" :stroke-width="6"
                       :status="subscriptionTrafficPercent(activeSubscription) >= 90 ? 'warning' : undefined" />
          <div class="mh-overview-footer">
            <span><el-icon><Clock /></el-icon>{{ formatRelativeUpdated(activeSubscription.updated_at) }}</span>
            <span>{{ activeSubscription.expire_at == null ? '到期时间未提供' : activeSubscription.expire_at === 0 ? '不限期' : '到期 ' + subscriptionExpireShort(activeSubscription) }}</span>
          </div>
        </template>
        <div v-else class="mh-overview-empty">
          <strong>尚未启用订阅</strong><p>导入订阅，或在订阅管理中选择一份配置。</p>
          <el-button size="small" type="primary" plain :icon="Plus" @click="openAddSubscription">导入订阅</el-button>
        </div>
      </section>

      <section class="mh-overview-card" aria-labelledby="current-node-title">
        <div class="mh-overview-head">
          <h3 id="current-node-title"><el-icon><Connection /></el-icon>当前节点</h3>
          <div class="mh-overview-tools">
            <el-button text circle :icon="Odometer" :loading="currentNodeTesting"
                       :disabled="!running || loading || !overviewNode.name || overviewNode.name === 'DIRECT' || overviewNode.name === 'REJECT' || subscriptionBusy || currentNodeTesting"
                       aria-label="测试当前节点延迟" @click="testCurrentNode" />
            <el-button text :icon="ArrowRight" size="small" @click="scrollToSection('nodes')">节点管理</el-button>
          </div>
        </div>
        <el-skeleton v-if="loading && !currentNode" :rows="3" animated />
        <p v-else-if="nodeStateError" class="mh-fail" role="alert">{{ nodeStateError }}</p>
        <template v-else-if="running && (overviewNode.name || overviewSelectedGroup)">
          <div class="mh-overview-node-summary">
            <div class="mh-overview-node-text">
              <strong class="mh-overview-name" :title="overviewNode.name">{{ overviewNode.name === 'DIRECT' ? '直连 · DIRECT' : overviewNode.name || '请选择节点' }}</strong>
              <div class="mh-overview-node-meta">
                <span v-if="overviewNode.type">{{ overviewNode.type }}</span>
                <el-tag size="small" effect="plain">{{ modeLabel }}</el-tag>
                <el-tag v-if="overviewNode.udp" size="small" type="info" effect="plain">UDP</el-tag>
              </div>
            </div>
            <div class="mh-overview-delay-pill" :class="overviewDelay?.ok ? 'latency-' + delayTagType(overviewDelay.delay) : ''" aria-live="polite">
              <span v-if="currentNodeTesting">测速中…</span>
              <span v-else-if="overviewNode.name === 'DIRECT'">直连</span>
              <span v-else-if="overviewDelay?.ok && overviewDelay.delay != null">{{ overviewDelay.delay }}<small>ms</small></span>
              <span v-else>{{ overviewDelay?.message ? '超时 / 失败' : '未测速' }}</span>
            </div>
          </div>
          <div class="mh-overview-selectors">
            <fieldset class="mh-overview-field">
              <legend>代理组</legend>
              <el-select :model-value="overviewSelectedGroup?.name" filterable class="mh-overview-select"
                         aria-label="当前节点代理组" :placeholder="modeState?.mode === 'direct' ? '直连模式不使用代理组' : '选择代理组'"
                         :disabled="loading || subscriptionBusy || applyingNode !== null || modeState?.mode === 'global' || modeState?.mode === 'direct'"
                         @change="chooseOverviewGroup">
                <el-option v-for="group in overviewGroupOptions" :key="group.name" :label="group.name" :value="group.name">
                  <div class="mh-node-option"><span>{{ group.name }}</span><small>{{ group.options.length }} 项</small></div>
                </el-option>
              </el-select>
            </fieldset>
            <fieldset class="mh-overview-field">
              <legend>节点</legend>
              <el-select :model-value="overviewSelectedGroup?.now" filterable class="mh-overview-select"
                         aria-label="当前代理组节点" :loading="applyingNode !== null" :placeholder="modeState?.mode === 'direct' ? 'DIRECT' : '选择节点'"
                         :disabled="loading || subscriptionBusy || batchLoading || applyingNode !== null || !overviewSelectedGroup || modeState?.mode === 'direct'"
                         @change="chooseOverviewNode">
                <el-option v-for="name in overviewSelectedGroup?.options || []" :key="name" :label="name" :value="name">
                  <div class="mh-node-option">
                    <el-icon><Guide v-if="nodeInfo[name]?.now" /><Connection v-else /></el-icon>
                    <span>{{ name }}</span>
                    <small v-if="optionDelay(name)?.ok" :class="'latency-' + delayTagType(optionDelay(name)?.delay)">{{ optionDelay(name)?.delay }} ms</small>
                    <small v-else>{{ nodeInfo[name]?.type || '' }}</small>
                  </div>
                </el-option>
              </el-select>
            </fieldset>
          </div>
          <p v-if="overviewSelectionError" class="mh-overview-selection-error" role="alert">{{ overviewSelectionError }}</p>
          <p v-if="modeState?.mode === 'global'" class="mh-overview-selection-note">全局模式固定使用 GLOBAL，可直接切换其出口。</p>
          <p v-else-if="modeState?.mode !== 'direct'" class="mh-overview-selection-note">选择代理组仅切换查看范围；选择节点立即应用到该组。</p>
          <div class="mh-overview-footer">
            <span><i class="mh-overview-live-dot" />Mihomo {{ version || '运行中' }}</span>
            <span class="mh-overview-chain" :title="[overviewGroup, ...overviewNode.chain].join(' → ')">{{ [overviewGroup, ...overviewNode.chain.slice(0, -1)].join(' → ') }}</span>
          </div>
        </template>
        <div v-else class="mh-overview-empty">
          <strong>{{ running ? '暂无可用节点' : 'Mihomo 未运行' }}</strong>
          <p>{{ running ? '检查当前订阅与策略组配置。' : '启动 Mihomo 后显示当前节点及延迟。' }}</p>
          <el-button v-if="!running" size="small" type="success" plain :icon="VideoPlay" :loading="startLoading" @click="runStartMihomo">启动 Mihomo</el-button>
        </div>
      </section>
    </div>

    <NetworkSettings :running="running" :refresh-token="refreshToken" @state-change="modeState = $event" />

    <el-card id="subscriptions" class="mh-subscription" shadow="never" tabindex="-1">
      <template #header>
        <span>代理订阅</span>
      </template>
      <div class="mh-sub-toolbar">
        <el-input v-model="subscriptionQuickUrl" clearable placeholder="订阅文件链接"
                  @keyup.enter="handleQuickImport">
          <template #append>
            <el-button @click="pasteQuickUrl">粘贴</el-button>
          </template>
        </el-input>
        <el-button :disabled="subscriptionBusy || !subscriptionQuickUrl.trim()" @click="handleQuickImport">导入</el-button>
        <el-button type="primary" :icon="Plus" :disabled="subscriptionBusy" @click="openAddSubscription">新建</el-button>
      </div>
      <div v-if="subscription?.items?.length" class="mh-profile-grid">
        <article v-for="row in subscription.items" :key="row.id" class="mh-profile-card"
                 :class="{ 'is-active': row.active, 'is-busy': subscriptionBusy, 'is-stale': !row.has_cache }"
                 tabindex="0" :aria-label="row.name" @click="handleActivateProfile(row)"
                 @keydown.enter.self="handleActivateProfile(row)" @keydown.space.self.prevent="handleActivateProfile(row)">
          <div class="mh-profile-top">
            <strong class="mh-profile-name" :title="row.name">{{ row.name }}</strong>
            <div class="mh-profile-top-actions" @click.stop>
              <el-button text circle :icon="Refresh"
                         :loading="subscriptionUpdatingId === row.id"
                         :disabled="subscriptionBusy || batchLoading || startLoading"
                         aria-label="更新订阅"
                         @click="handleUpdateProfile(row)" />
              <el-dropdown trigger="click" :disabled="subscriptionBusy"
                           @command="(command: string) => command === 'edit' ? openEditSubscription(row) : handleDeleteProfile(row)">
                <el-button text circle :icon="MoreFilled" :disabled="subscriptionBusy" aria-label="更多" />
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item command="edit">编辑</el-dropdown-item>
                    <el-dropdown-item command="delete" :disabled="row.active" divided>删除</el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </div>
          </div>
          <div class="mh-profile-meta">
            <span class="mh-profile-host" :title="subscriptionHostname(row)">{{ subscriptionHostname(row) }}</span>
            <span class="mh-profile-time">{{ formatRelativeUpdated(row.updated_at) }}</span>
          </div>
          <div class="mh-profile-quota">
            <span>{{ subscriptionTrafficText(row) }}</span>
            <span>{{ subscriptionExpireShort(row) }}</span>
          </div>
          <div class="mh-profile-progress" aria-hidden="true">
            <div class="mh-profile-progress__bar" :style="{ width: subscriptionTrafficPercent(row) + '%' }" />
          </div>
          <p class="mh-profile-foot">
            <span v-if="row.active">当前使用</span>
            <span v-else-if="!row.has_cache">尚未下载 · 点更新</span>
            <span v-else>{{ row.node_count == null ? '' : row.node_count + ' 节点' }} · {{ row.via_proxy ? '代理下载' : '直连' }}</span>
          </p>
        </article>
      </div>
      <el-empty v-else description="尚未添加订阅" :image-size="64" />
      <p class="mh-footnote">点击卡片切换当前订阅；右侧刷新图标仅更新该订阅。未启用的订阅更新不会重载正在使用的代理。</p>
    </el-card>

    <el-dialog v-model="subscriptionDialogVisible"
               :title="subscriptionEditingId ? '编辑订阅' : '导入订阅'" width="560px"
               :close-on-click-modal="!subscriptionSaving">
      <el-form label-position="top" @submit.prevent="handleSaveSubscription">
        <el-form-item label="名称" required>
          <el-input v-model="subscriptionName" maxlength="80" show-word-limit placeholder="例如：iKuuu 主线路" />
        </el-form-item>
        <el-form-item label="订阅链接" :required="!subscriptionEditingId">
          <el-input v-model="subscriptionUrl" type="password" show-password autocomplete="off"
                    placeholder="Clash / Mihomo YAML 订阅 URL" />
        </el-form-item>
        <el-form-item label="下载方式">
          <el-radio-group v-model="subscriptionProxy">
            <el-radio-button :value="false">直连</el-radio-button>
            <el-radio-button :value="true">通过当前代理</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <p class="mh-tip">链接保存在 <code>env/.env.mihomo.local</code>（本仓根目录），不提交 Git。保存后对该订阅点「更新」拉取配置。</p>
      </el-form>
      <template #footer>
        <el-button :disabled="subscriptionSaving" @click="subscriptionDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="subscriptionSaving" @click="handleSaveSubscription">保存</el-button>
      </template>
    </el-dialog>

    <el-card shadow="never" class="mh-check-panel">
      <template #header>
        <div class="mh-table-head">
          <span>出口 IP 与网站连通性</span>
          <div class="mh-check-actions">
            <el-button size="small" type="primary" :icon="CircleCheck" :loading="verifyAllLoading"
                       :disabled="!running || subscriptionBusy || verifyAllLoading || ipLoading || connectivityLoading.size > 0"
                       @click="runOneClickVerify">
              一键验证
            </el-button>
            <el-button size="small" :icon="Position" :loading="ipLoading"
                       :disabled="!running || verifyAllLoading || connectivityLoading.size > 0"
                       @click="runIpCheck()">
              验证 IP
            </el-button>
            <el-button size="small" :icon="Connection"
                       :loading="connectivityLoading.size > 0"
                       :disabled="!running || subscriptionBusy || verifyAllLoading || connectivityLoading.size > 0"
                       @click="runConnectivityVerify">
              联通验证
            </el-button>
          </div>
        </div>
      </template>
      <div class="mh-check-row">
        <div class="mh-check-card">
          <div class="mh-check-card__head">
            <span class="mh-check-card__label"><el-icon><Connection /></el-icon> 本机直连</span>
          </div>
          <code class="mh-check-card__ip">{{ directIp || "—" }}</code>
          <p v-if="directIpLabel" class="mh-check-card__meta">{{ directIpLabel }}</p>
          <p v-else class="mh-check-card__meta mh-muted">出口 IP 与归属</p>
        </div>
        <div class="mh-check-card mh-check-card--accent">
          <div class="mh-check-card__head">
            <span class="mh-check-card__label"><el-icon><Connection /></el-icon> 代理出口</span>
          </div>
          <code class="mh-check-card__ip">{{ proxyIp || "—" }}</code>
          <p v-if="proxyIpLabel" class="mh-check-card__meta">{{ proxyIpLabel }}</p>
          <p v-else class="mh-check-card__meta mh-muted">经 7890，同步所选节点</p>
        </div>
        <div v-for="target in connectivityTargets" :key="target.id" class="mh-check-card">
          <div class="mh-check-card__head">
            <span class="mh-check-card__label">{{ target.name }}</span>
            <el-button link type="primary" size="small"
                       :loading="connectivityLoading.has(target.id)"
                       :disabled="!running || subscriptionBusy || connectivityLoading.has(target.id)"
                       @click="runConnectivity([target.id])">测</el-button>
          </div>
          <span class="mh-check-card__host">{{ target.host }}</span>
          <div class="mh-check-card__status">
            <el-tag v-if="connectivityLoading.has(target.id)" type="info" size="small" effect="plain">测试中</el-tag>
            <el-tag v-else-if="connectivityResults[target.id]" size="small" effect="plain"
                    :type="connectivityResults[target.id].state === 'success' ? 'success' : connectivityResults[target.id].state === 'restricted' ? 'warning' : 'danger'">
              {{ connectivityResults[target.id].state === "success" ? "正常" : connectivityResults[target.id].state === "restricted" ? "受限" : "失败" }}
            </el-tag>
            <span v-else class="mh-muted">未测</span>
          </div>
          <p class="mh-check-card__meta">
            <template v-if="connectivityResults[target.id] && !connectivityLoading.has(target.id)">
              <span v-if="connectivityResults[target.id].http_status">HTTP {{ connectivityResults[target.id].http_status }} · </span>
              {{ connectivityResults[target.id].elapsed_ms }} ms
              <span v-if="connectivityResults[target.id].ip"> · {{ connectivityResults[target.id].ip }}</span>
            </template>
            <span v-else class="mh-muted">HTTP / 耗时</span>
          </p>
        </div>
      </div>
      <p class="mh-footnote mh-check-note">
        「一键验证」= 出口 IP + 网站联通；「联通验证」仅测 Google / ChatGPT / IPify。验证 IP 会同步当前节点到 GLOBAL 等策略组。
      </p>
    </el-card>

    <el-card id="nodes" shadow="never" tabindex="-1">
      <template #header>
        <div class="mh-table-head">
          <span>节点延迟测速</span>
          <el-button type="primary" :icon="Odometer" :loading="batchLoading" :disabled="!running || !nodes.length"
                     @click="runBatchSpeed">
            全部测速
          </el-button>
        </div>
      </template>
      <div class="mh-node-grid">
        <article v-for="row in speedSorted" :key="row.name" class="mh-node-card"
                 :class="{ 'is-current': currentNode === row.name, 'is-selected': selectedNode === row.name }"
                 tabindex="0" :aria-label="row.name" @click="pickNode(row.name)"
                 @keydown.enter.self="pickNode(row.name)" @keydown.space.self.prevent="pickNode(row.name)">
          <h3 class="mh-node-name" :title="row.name">{{ row.name }}</h3>
          <div class="mh-node-measure">
            <div class="mh-node-latency" :class="'delay-' + delayTagType(row.delay)">
              <template v-if="batchLoading || singleRowLoading.has(row.name)">测速中</template>
              <template v-else-if="row.ok && row.delay != null">{{ row.delay }}<span>ms</span></template>
              <template v-else>{{ row.message ? '超时 / 失败' : '未测速' }}</template>
            </div>
          <div class="mh-node-actions">
            <el-button link size="small" type="primary" :loading="singleRowLoading.has(row.name)"
                       :disabled="!running || batchLoading || subscriptionBusy || singleRowLoading.has(row.name)"
                       @click.stop="testOneRow(row)">测速</el-button>
            <el-button link size="small" type="primary"
                       :loading="applyingNode === row.name"
                       :disabled="!running || batchLoading || subscriptionBusy || applyingNode !== null || currentNode === row.name"
                       @click.stop="applyNodeByName(row.name)">{{ currentNode === row.name ? '已应用' : '应用' }}</el-button>
          </div>
          </div>
        </article>
      </div>
      <el-empty v-if="!speedSorted.length" :description="running ? '暂无可用节点' : '启动代理后查看节点'" />
      <p class="mh-footnote">点击卡片可选中节点；「全部测速」后按<strong>节点名称</strong>排序并记住顺序（刷新仍保留）。单节点测速只更新数值不重排。</p>
    </el-card>
  </div>
</template>

<style scoped>
.mh-overview-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.mh-overview-card { min-width: 0; display: flex; flex-direction: column; padding: 18px 20px; border: 1px solid #e4e7ed; border-radius: 12px; background: #fff; }
.mh-overview-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 16px; }
.mh-overview-head h3 { display: flex; align-items: center; gap: 8px; margin: 0; font-size: 15px; font-weight: 600; color: #334155; }
.mh-overview-head h3 .el-icon { color: #64748b; font-size: 18px; }
.mh-overview-tools { display: flex; align-items: center; gap: 4px; flex-shrink: 0; }
.mh-overview-title-row { display: flex; align-items: center; gap: 10px; }
.mh-overview-name { min-width: 0; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #1e293b; font-size: 18px; line-height: 1.5; font-weight: 600; }
.mh-overview-source { margin: 6px 0 20px; color: #94a3b8; font-size: 12px; text-overflow: ellipsis; overflow: hidden; white-space: nowrap; }
.mh-overview-quota { display: flex; justify-content: space-between; flex-wrap: wrap; gap: 8px; margin: auto 0 10px; color: #64748b; font-size: 12px; }
.mh-overview-quota strong { color: #475569; font-size: 13px; font-weight: 500; }
.mh-overview-footer { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 6px 12px; margin-top: 16px; color: #94a3b8; font-size: 11px; line-height: 1.7; }
.mh-overview-footer > span { display: inline-flex; gap: 5px; align-items: center; }
.mh-overview-footer .el-icon { font-size: 13px; }
.mh-overview-node-meta { display: flex; align-items: center; gap: 7px; margin: 9px 0 16px; min-width: 0; }
.mh-overview-node-meta > span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #94a3b8; font-size: 12px; }
.mh-overview-node-meta .el-tag { flex-shrink: 0; }
.mh-overview-node-summary { display: flex; align-items: center; gap: 12px; justify-content: space-between; padding: 12px 14px; border: 1px solid #dbeafe; border-radius: 8px; background: #f4f8ff; }
.mh-overview-node-text { min-width: 0; flex: 1; }
.mh-overview-node-text > .mh-overview-name { display: block; font-size: 16px; }
.mh-overview-node-summary .mh-overview-node-meta { margin: 7px 0 0; }
.mh-overview-delay-pill { flex-shrink: 0; border: 1px solid currentColor; border-radius: 14px; padding: 3px 9px; color: #94a3b8; font-size: 12px; font-variant-numeric: tabular-nums; white-space: nowrap; }
.mh-overview-delay-pill small { margin-left: 3px; font-size: 10px; }
.mh-overview-selectors { display: grid; gap: 10px; margin-top: 14px; }
.mh-overview-field { min-width: 0; margin: 0; padding: 0 8px 3px; border: 1px solid #dcdfe6; border-radius: 7px; transition: border-color .15s; }
.mh-overview-field:focus-within { border-color: #3b82f6; }
.mh-overview-field legend { margin-left: 3px; padding: 0 5px; color: #64748b; font-size: 11px; }
.mh-overview-select { display: block; width: 100%; }
.mh-overview-select :deep(.el-select__wrapper) { box-shadow: none !important; background: transparent; padding: 2px 4px 5px; min-height: 30px; }
.mh-overview-selection-note { margin: 10px 0 0; color: #94a3b8; font-size: 11px; line-height: 1.6; }
.mh-overview-selection-error { margin: 10px 0 0; color: #dc2626; font-size: 12px; line-height: 1.6; }
.mh-overview-chain { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mh-node-option { display: flex; align-items: center; gap: 8px; min-width: 0; }
.mh-node-option > span { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.mh-node-option > small { flex-shrink: 0; font-size: 11px; }
.mh-node-option > .el-icon { color: #94a3b8; flex-shrink: 0; }
.latency-success { color: #16a34a; } .latency-warning { color: #d97706; } .latency-danger { color: #dc2626; }
.mh-overview-live-dot { width: 6px; height: 6px; border-radius: 50%; background: #22c55e; }
.mh-overview-empty { flex: 1; display: flex; flex-direction: column; align-items: flex-start; justify-content: center; min-height: 134px; color: #64748b; font-size: 14px; }
.mh-overview-empty p { margin: 8px 0 14px; color: #94a3b8; font-size: 12px; line-height: 1.7; }
#subscriptions, #nodes { scroll-margin-top: 16px; }
@media (max-width: 760px) { .mh-overview-grid { grid-template-columns: 1fr; } }
@media (max-width: 400px) { .mh-overview-card { padding: 16px 12px; } .mh-overview-head h3 { font-size: 14px; } }

.mh-sub-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  margin-bottom: 16px;
}
.mh-sub-toolbar .el-input { flex: 1 1 280px; min-width: 0; }
.mh-profile-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
}
.mh-profile-card {
  position: relative;
  min-width: 0;
  padding: 12px 14px 10px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #fff;
  box-shadow: 0 1px 2px rgb(15 23 42 / 4%);
  cursor: pointer;
  transition: border-color .15s, box-shadow .15s;
  overflow: hidden;
}
.mh-profile-card:hover { border-color: #cbd5e1; box-shadow: 0 2px 8px rgb(15 23 42 / 6%); }
.mh-profile-card:focus-visible { outline: 2px solid #93c5fd; outline-offset: 2px; }
.mh-profile-card.is-active {
  border-color: #3b82f6;
  box-shadow: 0 0 0 1px #3b82f6;
}
.mh-profile-card.is-busy { cursor: default; }
.mh-profile-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}
.mh-profile-name {
  flex: 1;
  min-width: 0;
  font-size: 15px;
  font-weight: 600;
  color: #1e293b;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.mh-profile-top-actions { display: flex; align-items: center; flex-shrink: 0; margin: -4px -6px -4px 0; }
.mh-profile-meta,
.mh-profile-quota {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 12px;
  color: #64748b;
  line-height: 1.4;
}
.mh-profile-meta { margin-bottom: 6px; }
.mh-profile-host {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.mh-profile-time { flex-shrink: 0; color: #94a3b8; font-size: 11px; }
.mh-profile-quota {
  margin-bottom: 8px;
  color: #475569;
  font-size: 12px;
}
.mh-profile-progress {
  height: 3px;
  border-radius: 2px;
  background: #e2e8f0;
  overflow: hidden;
}
.mh-profile-progress__bar {
  height: 100%;
  border-radius: 2px;
  background: linear-gradient(90deg, #60a5fa, #3b82f6);
  transition: width .25s ease;
}
.mh-profile-card.is-active .mh-profile-progress__bar { background: linear-gradient(90deg, #3b82f6, #2563eb); }
.mh-profile-foot {
  margin: 8px 0 0;
  font-size: 11px;
  color: #94a3b8;
  line-height: 1.35;
}
.mh-profile-card.is-active .mh-profile-foot { color: #3b82f6; font-weight: 500; }
@media (max-width: 1100px) { .mh-profile-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 640px) {
  .mh-profile-grid { grid-template-columns: 1fr; }
  .mh-sub-toolbar .el-button { flex: 1; }
}
.mh-check-panel :deep(.el-card__header) { padding: 12px 16px; }
.mh-check-panel :deep(.el-card__body) { padding: 12px 16px 14px; }
.mh-check-actions { display: flex; gap: 8px; flex-shrink: 0; }
.mh-check-row {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 10px;
  align-items: stretch;
}
.mh-check-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  min-height: 96px;
  padding: 10px 12px;
  border: 1px solid #e8ecf1;
  border-radius: 8px;
  background: linear-gradient(180deg, #fcfdfe 0%, #f8fafc 100%);
}
.mh-check-card--accent {
  background: linear-gradient(180deg, #f5f9ff 0%, #eff6ff 100%);
  border-color: #c7ddfd;
}
.mh-check-card__head { display: flex; align-items: center; justify-content: space-between; gap: 6px; min-height: 22px; }
.mh-check-card__label { display: inline-flex; align-items: center; gap: 4px; font-size: 12px; font-weight: 600; color: #475569; }
.mh-check-card__host { font-size: 11px; color: #94a3b8; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mh-check-card__ip {
  display: block;
  margin: 2px 0 0;
  font-size: 13px;
  font-weight: 600;
  line-height: 1.35;
  color: #0f172a;
  word-break: break-all;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}
.mh-check-card__status { min-height: 24px; display: flex; align-items: center; }
.mh-check-card__meta {
  margin: auto 0 0;
  font-size: 11px;
  line-height: 1.4;
  color: #64748b;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
.mh-check-note { margin-top: 10px; margin-bottom: 0; }
@media (max-width: 1200px) { .mh-check-row { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
@media (max-width: 720px) { .mh-check-row { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 480px) { .mh-check-row { grid-template-columns: 1fr; } }
.mh-node-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 8px; }
.mh-node-card { display: flex; flex-direction: column; justify-content: center; gap: 3px; min-width: 0; height: 64px; box-sizing: border-box; padding: 7px 10px; border: 1px solid #edf0f5; border-left: 3px solid transparent; border-radius: 6px; background: #fafbfc; cursor: pointer; transition: background-color .15s, border-color .15s; }
.mh-node-card:hover { background: #f0f3f8; }
.mh-node-card:focus-visible { outline: 2px solid #a5b4fc; outline-offset: 2px; }
.mh-node-card.is-selected { border-color: #bfdbfe; background: #f4f8ff; }
.mh-node-card.is-current { border-color: #dbeafe; border-left-color: #3b82f6; background: #eaf2ff; }
.mh-node-name { margin: 0; min-width: 0; font-size: 13px; font-weight: 500; line-height: 20px; color: #334155; white-space: nowrap; text-overflow: ellipsis; overflow: hidden; }
.mh-node-measure { display: flex; align-items: center; justify-content: space-between; gap: 6px; min-height: 20px; }
.mh-node-latency { color: #94a3b8; font-size: 12px; font-weight: 500; white-space: nowrap; font-variant-numeric: tabular-nums; }
.mh-node-latency span { margin-left: 3px; font-size: 11px; font-weight: 400; color: inherit; }
.mh-node-latency.delay-success { color: #16a34a; }
.mh-node-latency.delay-warning { color: #d97706; }
.mh-node-latency.delay-danger { color: #dc2626; }
.mh-node-actions { display: flex; align-items: center; flex-shrink: 0; gap: 10px; }
.mh-node-actions :deep(.el-button) { margin: 0; height: 20px; padding: 0; font-size: 12px; }
@media (max-width: 600px) { .mh-node-grid { grid-template-columns: 1fr; } }
.mh-subscription-info { display: grid; grid-template-columns: 2fr 1fr 1fr; gap: 20px; }
.mh-subscription-info > div { display: grid; gap: 8px; }
.mh-subscription-info strong { font-size: 13px; color: #334155; overflow-wrap: anywhere; }
@media (max-width: 700px) { .mh-subscription-info { grid-template-columns: 1fr; } }
.mh-page { display: flex; flex-direction: column; gap: 16px; }
.mh-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; }
.mh-header-actions { display: flex; gap: 8px; flex-shrink: 0; }
.mh-alert-p { margin: 0 0 10px; font-size: 13px; line-height: 1.5; }
.mh-alert-p code { font-size: 12px; }
.mh-header h2 { margin: 0 0 6px; font-size: 20px; }
.mh-sub { margin: 0; font-size: 13px; color: #64748b; line-height: 1.5; }
.mh-sub code { font-size: 12px; }
.mh-delay { margin-left: 12px; font-size: 13px; color: #16a34a; font-weight: 600; }
.mh-tip { font-size: 13px; color: #64748b; margin: 0 0 12px; line-height: 1.5; }
.mh-table-head { display: flex; justify-content: space-between; align-items: center; }
.mh-fail { font-size: 12px; color: #dc2626; }
.mh-muted { font-size: 12px; color: #94a3b8; }
.mh-footnote { margin: 12px 0 0; font-size: 12px; color: #94a3b8; }
</style>
