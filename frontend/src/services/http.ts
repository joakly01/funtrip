export type RequestOptions = { timeoutMs?: number; signal?: AbortSignal };

// Keep the deadline active while reading the body as well as waiting for headers.
export async function fetchJson(url: string, init: RequestInit, options: RequestOptions = {}) {
  const controller = new AbortController();
  let expired = false;
  const cancel = () => controller.abort();
  options.signal?.addEventListener('abort', cancel, { once: true });
  if (options.signal?.aborted) cancel();
  const timer = setTimeout(() => { expired = true; cancel(); }, options.timeoutMs ?? 30000);
  try {
    const response = await fetch(url, { ...init, signal: controller.signal });
    const data = await response.json();
    return { status: response.status, data };
  } catch (error) {
    if (expired) throw new Error('请求超时，请重试。行情来源可能暂时响应较慢。');
    if (options.signal?.aborted) throw new Error('已取消载入');
    if (error instanceof SyntaxError) throw new Error('服务返回内容异常');
    throw new Error('暂时无法连接服务，请检查网络后重试。未完成的草稿仍留在这台设备。');
  } finally {
    clearTimeout(timer);
    options.signal?.removeEventListener('abort', cancel);
  }
}
