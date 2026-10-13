<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { Refresh, Connection, Position, Odometer, VideoPlay, Plus, MoreFilled, CircleCheck } from "@element-plus/icons-vue";
import {
  getMihomoStatus, getMihomoProxies, selectMihomoNode, testMihomoDelay, checkMihomoIp,
  batchMihomoDelay, startMihomo, getMihomoSubscription, saveMihomoSubscription, updateMihomoSubscription,
  activateMihomoSubscription, deleteMihomoSubscription, checkMihomoConnectivity,
  type MihomoDelayItem, type MihomoSubscriptionSettings, type MihomoSubscriptionProfile, type MihomoConnectivityItem,
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
const nodeInfo = ref<Record<string, { type: string; udp: boolean | null }>>({});

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
  try { subscription.value = await getMihomoSubscription(); }
  catch { ElMessage.error("读取订阅设置失败"); }
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
  try {
    const st = await getMihomoStatus();
    running.value = st.running;
    version.value = st.version || "";
    if (!st.running) {
      ElMessage.warning(st.message || "Mihomo 未运行");
      nodes.value = [];
      speedRows.value = [];
      return;
    }
    const px = await getMihomoProxies();
    if (!px.ok) {
      ElMessage.error(px.message || "读取节点失败");
      return;
    }
    mainGroup.value = px.main_group;
    currentNode.value = px.current;
    nodes.value = px.nodes;
    nodeInfo.value = px.node_info || {};
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
    ElMessage.error("加载失败");
  } finally {
    loading.value = false;
  }
};

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
    connectivityResults.value = {};
    proxyIp.value = "";
    proxyIpLabel.value = "";
  } catch {
    ElMessage.error("切换节点失败");
  } finally {
    applyingNode.value = null;
  }
};

const patchRow = (name: string, patch: Partial<MihomoDelayItem>, opts?: { persist?: boolean }) => {
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
        <el-button :icon="Refresh" :loading="loading" @click="loadAll">刷新</el-button>
      </div>
    </div>

    <el-alert v-if="!running && !loading" type="warning" show-icon :closable="false" style="margin-bottom:16px"
              title="Mihomo 未连接">
      <template #default>
        <p class="mh-alert-p">可在本页启动（等价于 <code>./devtools/mihomo/run.sh</code>），或于服务器手动执行该脚本。</p>
      </template>
    </el-alert>

    <el-card class="mh-subscription" shadow="never">
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
        <p class="mh-tip">链接保存在服务器 <code>env/.env.mihomo.local</code>，不提交 Git。保存后对该订阅点「更新」拉取配置。</p>
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

    <el-card shadow="never">
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
