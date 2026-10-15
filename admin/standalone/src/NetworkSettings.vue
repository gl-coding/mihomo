<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import { Monitor, Connection, Refresh, Setting, Guide, Compass, Promotion, CircleCheck } from "@element-plus/icons-vue";
import {
  getMihomoNetwork, setMihomoNetwork, getMihomoProxyMode, setMihomoProxyMode, selectMihomoNode,
  type MihomoNetworkSettings, type MihomoProxyMode, type MihomoProxyModeSettings,
} from "./api";

const props = defineProps<{ running: boolean; refreshToken?: number }>();
const emit = defineEmits<{ "state-change": [state: MihomoProxyModeSettings | null] }>();
const network = ref<MihomoNetworkSettings | null>(null);
const core = ref<MihomoProxyModeSettings | null>(null);
const activeTab = ref<"system" | "tun">("system");
const detailsOpen = ref(false);
const systemTabButton = ref<HTMLButtonElement | null>(null);
const tunTabButton = ref<HTMLButtonElement | null>(null);
const focusTab = (tab: "system" | "tun") => {
  activeTab.value = tab;
  (tab === "system" ? systemTabButton : tunTabButton).value?.focus();
};
const networkLoading = ref(false);
const networkSaving = ref(false);
const coreLoading = ref(false);
const modeSaving = ref<MihomoProxyMode | null>(null);
const globalSaving = ref(false);
const networkError = ref("");
const coreError = ref("");
const networkEnabled = computed(() => network.value?.supported === true && network.value.mode === "system");
const systemStatus = computed(() => {
  if (networkLoading.value && !network.value) return "正在读取系统代理状态…";
  if (!network.value?.supported) return network.value?.message || networkError.value || "系统代理状态暂不可用";
  if (network.value.mode === "other") return "当前使用 PAC 或其他代理模式，开启后将切换为手动代理。";
  if (networkEnabled.value && !network.value.matches_target) return "系统代理已开启，但地址与当前 Mihomo 不一致。";
  return networkEnabled.value ? "系统代理已开启，遵循系统设置的应用将通过 Mihomo 访问网络。" : "系统代理已关闭，可开启以供 Chrome 等应用使用。";
});
const modes = [
  { value: "rule" as const, label: "规则", icon: Guide, description: "按订阅规则分流，匹配对应的代理节点或直连。" },
  { value: "global" as const, label: "全局", icon: Compass, description: "进入 Mihomo 的流量统一使用下方 GLOBAL 出口。" },
  { value: "direct" as const, label: "直连", icon: Promotion, description: "进入 Mihomo 的流量直接连接目标，不经过代理节点。" },
];
const modeDescription = computed(() => {
  if (modeSaving.value) return "正在切换代理模式…";
  if (!core.value) return coreLoading.value ? "正在读取内核运行模式…" : "启动 Mihomo 后，可选择流量转发方式。";
  return modes.find((item) => item.value === core.value?.mode)?.description || "未知代理模式";
});
const errorMessage = (error: unknown, fallback: string) =>
  (error as { response?: { data?: { message?: string } } })?.response?.data?.message || fallback;

const loadNetwork = async () => {
  if (networkSaving.value || networkLoading.value) return;
  networkLoading.value = true;
  try {
    network.value = await getMihomoNetwork();
    networkError.value = "";
  } catch (error) {
    network.value = null;
    networkError.value = errorMessage(error, "无法读取系统代理，请刷新重试");
  } finally { networkLoading.value = false; }
};
const loadCore = async () => {
  if (modeSaving.value || globalSaving.value || coreLoading.value) return;
  coreLoading.value = true;
  try {
    core.value = await getMihomoProxyMode();
    coreError.value = "";
  } catch (error) {
    core.value = null;
    coreError.value = errorMessage(error, "无法读取代理模式，请刷新重试");
  } finally { coreLoading.value = false; }
};
const refreshSettings = () => Promise.allSettled([loadNetwork(), loadCore()]);

const toggleSystemProxy = async (enabled: boolean) => {
  if (networkSaving.value || networkLoading.value) return;
  networkSaving.value = true;
  networkError.value = "";
  try {
    network.value = await setMihomoNetwork(enabled ? "system" : "off");
    ElMessage.success(enabled ? "已开启系统代理" : "已关闭系统代理");
  } catch (error) {
    networkError.value = errorMessage(error, "系统代理设置失败");
    try { network.value = await getMihomoNetwork(); } catch { network.value = null; }
  } finally { networkSaving.value = false; }
};
const changeMode = async (mode: MihomoProxyMode) => {
  if (!core.value || modeSaving.value || coreLoading.value || mode === core.value.mode) return;
  modeSaving.value = mode;
  coreError.value = "";
  try {
    core.value = await setMihomoProxyMode(mode);
    ElMessage.success(`已切换为${modes.find((item) => item.value === mode)?.label}模式`);
  } catch (error) {
    coreError.value = errorMessage(error, "代理模式切换失败");
    try { core.value = await getMihomoProxyMode(); } catch { core.value = null; }
  } finally { modeSaving.value = null; }
};
const changeGlobalNode = async (node: string) => {
  if (globalSaving.value || modeSaving.value || !core.value) return;
  globalSaving.value = true;
  coreError.value = "";
  try {
    const result = await selectMihomoNode(node, "GLOBAL");
    if (!result.ok) throw new Error("切换失败");
    core.value = await getMihomoProxyMode();
    ElMessage.success("已更新全局出口");
  } catch (error) {
    coreError.value = errorMessage(error, "全局出口切换失败，请刷新后重试");
    try { core.value = await getMihomoProxyMode(); } catch { core.value = null; }
  } finally { globalSaving.value = false; }
};

const onFocus = () => { void refreshSettings(); };
onMounted(() => { void refreshSettings(); window.addEventListener("focus", onFocus); });
onUnmounted(() => window.removeEventListener("focus", onFocus));
watch(() => props.running, () => { void loadCore(); });
watch(() => props.refreshToken, () => { void refreshSettings(); });
watch(core, (state) => emit("state-change", state));
</script>

<template>
  <div class="mh-control-grid">
    <section class="mh-control-card" aria-labelledby="network-title">
      <div class="mh-control-heading">
        <h3 id="network-title"><el-icon><Connection /></el-icon>网络设置</h3>
        <div class="mh-control-tools">
          <el-tag v-if="network?.desktop" size="small" effect="plain" type="info">{{ network.desktop }}</el-tag>
          <el-button text circle :icon="Refresh" :loading="networkLoading" :disabled="networkSaving"
                     aria-label="刷新网络设置" @click="loadNetwork" />
          <el-button text circle :icon="Setting" aria-label="查看系统代理详情" @click="detailsOpen = true" />
        </div>
      </div>
      <div class="mh-choice-row" role="tablist" aria-label="网络设置类型">
        <button ref="systemTabButton" id="system-tab" type="button" role="tab" :aria-selected="activeTab === 'system'" aria-controls="network-panel"
                :tabindex="activeTab === 'system' ? 0 : -1"
                class="mh-choice" :class="{ 'is-active': activeTab === 'system' }" @click="activeTab = 'system'"
                @keydown.right.prevent="focusTab('tun')" @keydown.left.prevent="focusTab('tun')"
                @keydown.home.prevent="focusTab('system')" @keydown.end.prevent="focusTab('tun')">
          <el-icon><Monitor /></el-icon><span>系统代理</span>
          <i v-if="networkEnabled" class="mh-status-dot" aria-label="已开启" />
        </button>
        <button ref="tunTabButton" id="tun-tab" type="button" role="tab" :aria-selected="activeTab === 'tun'" aria-controls="network-panel"
                :tabindex="activeTab === 'tun' ? 0 : -1"
                class="mh-choice" :class="{ 'is-active': activeTab === 'tun' }" @click="activeTab = 'tun'"
                @keydown.left.prevent="focusTab('system')" @keydown.right.prevent="focusTab('system')"
                @keydown.home.prevent="focusTab('system')" @keydown.end.prevent="focusTab('tun')">
          <el-icon><Connection /></el-icon><span>虚拟网卡</span>
          <i v-if="core?.tun_enabled" class="mh-status-dot" aria-label="已开启" />
        </button>
      </div>
      <div id="network-panel" role="tabpanel" :aria-labelledby="activeTab === 'system' ? 'system-tab' : 'tun-tab'">
        <p class="mh-mode-description" aria-live="polite">{{ activeTab === 'system' ? systemStatus : core ? (core.tun_enabled ? 'TUN 已开启，当前由 Mihomo 虚拟网卡接管流量。' : 'TUN 未开启，可接管不遵循系统代理设置的应用流量。') : '内核未连接，暂时无法读取虚拟网卡状态。' }}</p>
        <div class="mh-switch-row">
          <div>
            <strong>{{ activeTab === 'system' ? '系统代理' : '虚拟网卡模式' }}</strong>
            <span>{{ activeTab === 'system' ? (network?.supported ? network.target : '支持 KDE Plasma / GNOME') : '当前管理页暂未提供 TUN 启停功能' }}</span>
          </div>
          <el-switch v-if="activeTab === 'system'" :model-value="networkEnabled" :loading="networkSaving"
                     :disabled="!network?.supported || networkLoading || networkSaving || (!running && !networkEnabled)"
                     aria-label="系统代理开关" @change="(value) => toggleSystemProxy(value === true)" />
          <el-switch v-else :model-value="core?.tun_enabled ?? false" disabled aria-label="虚拟网卡状态（只读）" />
        </div>
      </div>
      <div v-if="activeTab === 'system' && networkEnabled && !network?.matches_target" class="mh-inline-warning">
        <span>系统代理目标与 Mihomo 不一致</span>
        <el-button link type="primary" :loading="networkSaving" :disabled="!running || networkLoading" @click="toggleSystemProxy(true)">修复地址</el-button>
      </div>
      <p v-if="activeTab === 'system' && !running" class="mh-control-note">开启前请先启动 Mihomo；已开启时仍可关闭。</p>
      <p v-if="networkError" class="mh-control-error" role="alert">{{ networkError }}</p>
    </section>

    <section class="mh-control-card" aria-labelledby="proxy-mode-title">
      <div class="mh-control-heading">
        <h3 id="proxy-mode-title"><el-icon><Guide /></el-icon>代理模式</h3>
        <el-button text circle :icon="Refresh" :loading="coreLoading" :disabled="!!modeSaving || globalSaving"
                   aria-label="刷新代理模式" @click="loadCore" />
      </div>
      <div class="mh-choice-row mh-choice-row--modes" role="group" aria-label="代理模式">
        <button v-for="mode in modes" :key="mode.value" type="button"
                class="mh-choice" :class="{ 'is-active': core?.mode === mode.value, 'is-pending': modeSaving === mode.value }"
                :aria-pressed="core?.mode === mode.value" :aria-busy="modeSaving === mode.value"
                :disabled="!running || !core || coreLoading || !!modeSaving || globalSaving" @click="changeMode(mode.value)">
          <el-icon><component :is="mode.icon" /></el-icon><span>{{ mode.label }}</span>
        </button>
      </div>
      <p class="mh-mode-description" aria-live="polite">{{ modeDescription }}</p>
      <div v-if="core?.mode === 'global'" class="mh-global-row">
        <label for="global-outbound">全局出口</label>
        <el-select v-if="core.global_options.length" id="global-outbound" :model-value="core.global_current"
                   filterable :loading="globalSaving" :disabled="globalSaving || !!modeSaving || coreLoading || !running"
                   placeholder="选择 GLOBAL 出口" @change="changeGlobalNode">
          <el-option v-for="name in core.global_options" :key="name" :label="name" :value="name" />
        </el-select>
        <span v-else>{{ core.global_current || 'GLOBAL 策略组不可用' }}</span>
        <p v-if="core.global_current === 'DIRECT'" class="mh-control-note">当前 GLOBAL 出口为 DIRECT，流量将直连。请选择代理节点或策略组。</p>
      </div>
      <div v-else class="mh-mode-status">
        <el-icon><CircleCheck /></el-icon><span>{{ core ? '当前模式已从 Mihomo 内核读取' : '等待内核连接' }}</span>
      </div>
      <p v-if="coreError" class="mh-control-error" role="alert">{{ coreError }}</p>
      <p class="mh-control-note">代理模式决定进入 Mihomo 的流量如何转发，系统代理开关单独生效。</p>
    </section>
  </div>

  <el-dialog v-model="detailsOpen" title="系统代理设置" width="min(560px, 94vw)">
    <p class="mh-dialog-intro">查看运行此管理页的电脑上，当前用户的实际系统代理设置。</p>
    <el-descriptions :column="1" border>
      <el-descriptions-item label="桌面">{{ network?.desktop || '未识别' }}</el-descriptions-item>
      <el-descriptions-item label="代理状态">{{ !network?.supported ? '不可用' : networkEnabled ? '已开启' : network.mode === 'other' ? '其他模式' : '已关闭' }}</el-descriptions-item>
      <el-descriptions-item label="Mihomo 地址">{{ network?.target || '—' }}</el-descriptions-item>
      <el-descriptions-item v-for="protocol in ['http', 'https', 'socks']" :key="protocol" :label="protocol.toUpperCase()">
        {{ network?.addresses[protocol] || '未设置' }}
      </el-descriptions-item>
    </el-descriptions>
    <p class="mh-control-note">系统代理只影响遵循该设置的应用。关闭不会停止 Mihomo，也不会清除终端的代理环境变量。Chrome 未生效时，请完全退出后重新打开。</p>
    <template #footer><el-button @click="detailsOpen = false">关闭</el-button></template>
  </el-dialog>
</template>

<style scoped>
.mh-control-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.mh-control-card { min-width: 0; padding: 18px 20px; border: 1px solid #e4e7ed; border-radius: 12px; background: #fff; }
.mh-control-heading { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 18px; min-height: 28px; }
.mh-control-heading h3 { display: flex; align-items: center; gap: 8px; margin: 0; color: #334155; font-size: 15px; font-weight: 600; }
.mh-control-heading h3 .el-icon { color: #64748b; font-size: 18px; }
.mh-control-tools { display: flex; align-items: center; gap: 4px; }
.mh-control-tools .el-tag { max-width: 108px; overflow: hidden; text-overflow: ellipsis; }
.mh-choice-row { display: flex; justify-content: center; gap: 10px; padding: 0 6px; }
.mh-choice { appearance: none; position: relative; display: flex; flex: 1; justify-content: center; align-items: center; gap: 8px; max-width: 180px; padding: 12px 10px; border: 1px solid #e4e7ed; border-radius: 8px; background: #fff; color: #475569; font: inherit; font-size: 14px; cursor: pointer; transition: background .15s, box-shadow .15s, transform .15s; }
.mh-choice .el-icon { font-size: 18px; flex-shrink: 0; }
.mh-choice:not(:disabled):hover { transform: translateY(-1px); box-shadow: 0 3px 8px rgb(15 23 42 / 8%); }
.mh-choice.is-active { color: #fff; background: #3b82f6; border-color: #3b82f6; box-shadow: 0 3px 7px rgb(59 130 246 / 18%); font-weight: 600; }
.mh-choice.is-active::after { content: ''; position: absolute; width: 2px; height: 14px; bottom: -15px; left: calc(50% - 1px); background: #3b82f6; }
.mh-choice:disabled { opacity: .5; cursor: not-allowed; }
.mh-choice:focus-visible { outline: 3px solid #93c5fd; outline-offset: 3px; }
.mh-choice.is-pending { opacity: .6; }
.mh-status-dot { position: absolute; top: 7px; right: 7px; width: 6px; height: 6px; border-radius: 50%; background: #22c55e; }
.mh-choice.is-active .mh-status-dot { background: #fff; }
.mh-mode-description { display: flex; align-items: center; justify-content: center; min-height: 52px; box-sizing: border-box; margin: 14px 0 12px; padding: 10px 14px; border: 1px solid #bfdbfe; border-radius: 7px; color: #64748b; font-size: 12px; line-height: 1.65; text-align: center; background: #fafdff; }
.mh-switch-row { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 12px 14px; border-radius: 9px; background: #f3f7fe; }
.mh-switch-row strong, .mh-switch-row span { display: block; }
.mh-switch-row strong { font-size: 14px; font-weight: 500; color: #334155; }
.mh-switch-row span { margin-top: 4px; font-size: 12px; color: #94a3b8; overflow-wrap: anywhere; }
.mh-inline-warning { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-top: 10px; color: #b45309; font-size: 12px; }
.mh-control-note { margin: 12px 0 0; font-size: 12px; line-height: 1.7; color: #94a3b8; }
.mh-control-error { margin: 12px 0 0; font-size: 12px; line-height: 1.6; color: #dc2626; }
.mh-mode-status { display: flex; align-items: center; gap: 7px; min-height: 58px; padding: 0 14px; border-radius: 9px; background: #f8fafc; color: #94a3b8; font-size: 12px; }
.mh-mode-status .el-icon { color: #64748b; }
.mh-global-row { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; padding: 12px 14px; border-radius: 9px; background: #f8fafc; }
.mh-global-row label { font-size: 12px; color: #64748b; }
.mh-global-row .el-select { flex: 1; min-width: 120px; }
.mh-global-row > span { font-size: 12px; color: #64748b; }
.mh-global-row .mh-control-note { width: 100%; margin: 0; color: #b45309; }
.mh-dialog-intro { margin-top: 0; color: #64748b; font-size: 13px; line-height: 1.7; }
@media (max-width: 760px) { .mh-control-grid { grid-template-columns: 1fr; } }
@media (max-width: 400px) { .mh-control-card { padding: 16px 12px; } .mh-choice { gap: 5px; padding-inline: 8px; } }
</style>
