import { EventReader } from "../services/events";
import { fetchJson, type RequestOptions } from "../services/http";
import type { SelectedFile, Attachment } from "../domain/types";
export const BASE = "https://joakly.site";
const KEY = "trading-session-v2";
let unauthorized = () => {};
export function onUnauthorized(fn: () => void) {
  unauthorized = fn;
}
export function forget() {
  uni.removeStorageSync(KEY);
}
export function cookie() {
  return uni.getStorageSync(KEY) || "";
}
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}
function check(status: number, data: any) {
  if (status === 401) {
    forget();
    unauthorized();
  }
  if (status < 200 || status >= 300)
    throw new ApiError(
      status,
      typeof data?.detail === "string"
        ? data.detail
        : "服务暂时无法处理，请稍后重试",
    );
  return data;
}
export async function request(
  path: string,
  data?: any,
  method = "GET",
  options: RequestOptions = {},
): Promise<any> {
  // #ifdef H5
  let url = path;
  if (method === "GET" && data)
    url +=
      "?" +
      new URLSearchParams(
        Object.fromEntries(
          Object.entries(data).map(([k, v]) => [k, String(v)]),
        ),
      ).toString();
  const response = await fetchJson(url, {
      method,
      credentials: "same-origin",
      headers: method === "GET" ? {} : { "Content-Type": "application/json" },
      body: method === "GET" ? undefined : JSON.stringify(data),
    }, options);
  return check(response.status, response.data);
  // #endif
  // #ifdef MP-WEIXIN
  return new Promise((resolve, reject) =>
    wx.request({
      url: BASE + path,
      data,
      method,
      header: { Cookie: cookie() },
      timeout: options.timeoutMs ?? 30000,
      success: (r: any) => {
        try {
          resolve(check(r.statusCode, r.data));
        } catch (e) {
          reject(e);
        }
      },
      fail: () => reject(new Error("暂时无法连接，请检查网络后重试")),
    }),
  );
  // #endif
}
export async function login(password: string) {
  // #ifdef H5
  await request("/api/login", { password }, "POST");
  // #endif
  // #ifdef MP-WEIXIN
  const response: any = await new Promise((resolve, reject) =>
    wx.request({
      url: BASE + "/api/login",
      method: "POST",
      data: { password },
      timeout: 30000,
      success: resolve,
      fail: () => reject(new Error("暂时无法登录，请检查网络后重试")),
    }),
  );
  check(response.statusCode, response.data);
  const values = response.cookies?.length
    ? response.cookies
    : [
        response.header?.["Set-Cookie"] ||
          response.header?.["set-cookie"] ||
          "",
      ];
  const match = values
    .join(";")
    .match(/(?:^|[;,]\s*)trading_session=([^;\s,]+)/);
  if (!match) throw new Error("未收到登录凭证");
  uni.setStorageSync(KEY, "trading_session=" + match[1]);
  // #endif
}
export async function upload(file: SelectedFile): Promise<Attachment> {
  if (file.size > 20 * 1024 * 1024) throw new Error("单个附件不能超过 20 MB");
  // #ifdef H5
  const body = new FormData();
  body.append("file", file.file!, file.name);
  const res = await fetch("/api/attachments", {
    method: "POST",
    body,
    credentials: "same-origin",
  });
  return check(res.status, await res.json());
  // #endif
  // #ifdef MP-WEIXIN
  return new Promise((resolve, reject) =>
    wx.uploadFile({
      url: BASE + "/api/attachments",
      filePath: file.path!,
      name: "file",
      header: { Cookie: cookie() },
      timeout: 180000,
      success: (r: any) => {
        try {
          resolve(check(r.statusCode, JSON.parse(r.data)));
        } catch (e) {
          reject(e);
        }
      },
      fail: () => reject(new Error("上传或转写连接失败，请重试")),
    }),
  );
  // #endif
}
export async function stream(body: any, emit: (event: any) => void) {
  let completed = false;
  const reader = new EventReader((event) => {
    if (event.type === "done") completed = true;
    emit(event);
  });
  // #ifdef H5
  const response = await fetch("/api/router/ask", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify(body),
  });
  if (!response.ok) return check(response.status, await response.json());
  if (!response.body) throw new Error("浏览器不能读取流式回复");
  const pipe = response.body.getReader();
  try {
    while (true) {
      const part = await pipe.read();
      if (part.done) break;
      reader.push(
        part.value.buffer.slice(
          part.value.byteOffset,
          part.value.byteOffset + part.value.byteLength,
        ),
      );
    }
    reader.push(null, true);
  } finally {
    pipe.releaseLock();
  }
  // #endif
  // #ifdef MP-WEIXIN
  await new Promise<void>((resolve, reject) => {
    let received = false,
      error: any = null;
    const task = wx.request({
      url: BASE + "/api/router/ask",
      method: "POST",
      data: body,
      header: { Cookie: cookie() },
      enableChunked: true,
      timeout: 300000,
      success: (r: any) => {
        try {
          check(r.statusCode, r.data);
          if (error) throw error;
          if (!received && typeof r.data === "string") reader.feed(r.data);
          reader.push(null, true);
          resolve();
        } catch (e) {
          reject(e);
        }
      },
      fail: () =>
        reject(new Error("连接中断，请查看历史确认结果，避免重复记录")),
    });
    task.onChunkReceived?.((r: any) => {
      received = true;
      try {
        reader.push(r.data);
      } catch (e) {
        error = e;
      }
    });
  });
  // #endif
  if (!completed)
    throw new Error("回复连接中断，请查看历史确认结果；原稿可以找回");
}
export async function attachmentLocation(item: Attachment): Promise<string> {
  const path =
    "/api/attachments/" +
    item.id +
    (item.kind === "image" ? "/preview" : "/content");
  // #ifdef H5
  const res = await fetch(path, { credentials: "same-origin" });
  if (!res.ok) {
    check(res.status, await res.json());
  }
  return URL.createObjectURL(await res.blob());
  // #endif
  // #ifdef MP-WEIXIN
  return new Promise((resolve, reject) =>
    wx.downloadFile({
      url: BASE + path,
      header: { Cookie: cookie() },
      success: (r: any) => {
        try {
          check(r.statusCode, {});
          resolve(r.tempFilePath);
        } catch (e) {
          reject(e);
        }
      },
      fail: () => reject(new Error("附件下载失败")),
    }),
  );
  // #endif
}
export function releaseLocation(url: string) {
  // #ifdef H5
  if (url.startsWith("blob:")) URL.revokeObjectURL(url);
  // #endif
}
