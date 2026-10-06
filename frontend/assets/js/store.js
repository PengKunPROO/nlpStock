// App state store — cross-view shared state (light, no framework)
const state = {
  settings: null,
  draftConfig: null,      // 待审查的策略配置（对齐产出）
  lastScreenResult: null, // 最近一次选股结果
  lastBacktestResult: null,
  chartCode: null,        // 图表页当前标的
  chartSignalDate: null,  // 图表页信号点标注日期（来自选股结果点击）
  screenStrategy: null,   // 选股页预选策略
  strategyTab: 'screening', // 策略页当前 Tab：screening | trading
  screenPicks: [],        // 选股页勾选的池：[{thscode, name, signal_date}]
  activeScreenJob: null,  // 后台选股任务 { id, status, progress, result, startedAt }
  activeBacktestJob: null,// 后台回测任务 { id, status, progress, result, startedAt }
};
export default state;

export async function ensureSettings(api) {
  if (!state.settings) state.settings = await api.getSettings();
  return state.settings;
}
