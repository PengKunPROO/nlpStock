// 全局后台任务管理：提交、轮询、跨页面状态追踪（不绑定任何页面 DOM）
// 选股/回测任务在后台线程跑，前端切 Tab 不中断；切回对应页面时恢复进度/结果。
import { api } from './api.js';
import state from './store.js';

let jobsChangeHandler = null;
export function setJobsChangeHandler(fn) { jobsChangeHandler = fn; }
function notify() { if (jobsChangeHandler) jobsChangeHandler(); }

const STATE_KEY = { screen: 'activeScreenJob', backtest: 'activeBacktestJob' };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function setJob(type, patch) {
  const key = STATE_KEY[type];
  state[key] = { ...(state[key] || {}), ...patch };
}

export function getActiveJob(type) {
  return state[STATE_KEY[type]];
}

// 提交任务并启动全局轮询（不 await，轮询在后台异步跑）
// submitFn 返回 { job_id }
export async function runJob(type, submitFn) {
  const cur = getActiveJob(type);
  if (cur && cur.status === 'running') return null; // 同类任务已在跑，防重复提交

  const { job_id } = await submitFn();
  setJob(type, { id: job_id, status: 'running', progress: { done: 0, total: 0, current: '' }, result: null, error: null, startedAt: Date.now() });
  notify();
  pollLoop(type, job_id); // 后台轮询，不阻塞提交
  return job_id;
}

async function pollLoop(type, jobId) {
  for (;;) {
    if (getActiveJob(type)?.id !== jobId) break; // 已被新任务替换
    let job;
    try {
      job = await api.getJob(jobId);
    } catch (e) {
      await sleep(1000); // 网络瞬时错误：重试
      continue;
    }
    setJob(type, { status: job.status, progress: job.progress, result: job.result, error: job.error });
    if (job.status === 'done') {
      // 结果落 state，供切回页面恢复
      if (type === 'screen') state.lastScreenResult = job.result;
      else state.lastBacktestResult = job.result;
    }
    notify();
    if (job.status === 'done' || job.status === 'error') break;
    await sleep(1000);
  }
}
