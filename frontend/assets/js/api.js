// API layer — single fetch wrapper for the backend contract (docs/api-contract.md)
const BASE = (typeof window !== 'undefined' && window.API_BASE) || '';
let mockImpl = null;
export function installMock(impl) { mockImpl = impl; }

async function request(method, path, body) {
  const impl = mockImpl || window.__MOCK_IMPL__;
  if (impl) return impl(method, path, body);
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const resp = await fetch(BASE + path, opts);
  let data = null;
  try { data = await resp.json(); } catch { /* empty body */ }
  if (!resp.ok) {
    const err = data && data.error ? data.error : { code: 'http_' + resp.status, message: `HTTP ${resp.status}` };
    const e = new Error(err.message || '请求失败');
    e.code = err.code; e.raw = err.raw; e.status = resp.status;
    throw e;
  }
  return data;
}

export const api = {
  get: (p) => request('GET', p),
  post: (p, b) => request('POST', p, b ?? {}),
  put: (p, b) => request('PUT', p, b ?? {}),
  del: (p) => request('DELETE', p),

  health: () => request('GET', '/api/health'),
  getSettings: () => request('GET', '/api/settings'),
  updateSettings: (s) => request('PUT', '/api/settings', s),

  parseStrategy: (messages) => request('POST', '/api/parse-strategy', { messages }),
  listStrategies: () => request('GET', '/api/strategies'),
  createStrategy: (config) => request('POST', '/api/strategies', { config }),
  getStrategy: (id) => request('GET', `/api/strategies/${id}`),
  updateStrategy: (id, config) => request('PUT', `/api/strategies/${id}`, { config }),
  deleteStrategy: (id) => request('DELETE', `/api/strategies/${id}`),
  getStrategyVersion: (id, v) => request('GET', `/api/strategies/${id}/versions/${v}`),
  restoreStrategyVersion: (id, v) => request('POST', `/api/strategies/${id}/restore/${v}`),

  screen: (payload) => request('POST', '/api/screen', payload),
  backtest: (payload) => request('POST', '/api/backtest', payload),
  getJob: (id) => request('GET', `/api/jobs/${id}`),

  kline: (thscode, period, count) =>
    request('GET', `/api/kline?thscode=${encodeURIComponent(thscode)}&period=${period}&count=${count}`),
  search: (q, limit = 20) => request('GET', `/api/search?q=${encodeURIComponent(q)}&limit=${limit}`),
  universeOptions: () => request('GET', '/api/universe/options'),
};

export async function pollJob(jobId, onProgress, intervalMs = 1000, timeoutMs = 20 * 60 * 1000) {
  const start = Date.now();
  for (;;) {
    const job = await api.getJob(jobId);
    if (onProgress) onProgress(job);
    if (job.status === 'done') return job.result;
    if (job.status === 'error') { const e = new Error(job.error || '任务失败'); e.code = 'job_error'; throw e; }
    if (Date.now() - start > timeoutMs) { const e = new Error('任务超时'); e.code = 'job_timeout'; throw e; }
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}
