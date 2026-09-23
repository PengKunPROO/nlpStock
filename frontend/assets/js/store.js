// App state store — cross-view shared state (light, no framework)
const state = {
  settings: null,
  draftConfig: null,      // 待审查的策略配置（对齐产出）
  lastScreenResult: null, // 最近一次选股结果
  lastBacktestResult: null,
  chartCode: null,        // 图表页当前标的
  screenStrategy: null,   // 选股页预选策略
};
export default state;

export async function ensureSettings(api) {
  if (!state.settings) state.settings = await api.getSettings();
  return state.settings;
}
