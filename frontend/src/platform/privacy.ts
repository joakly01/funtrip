import { reactive } from "vue";
export const privacy = reactive({ visible: false, title: "隐私保护指引" });
let pending: { resolve: () => void; reject: (e: Error) => void } | null = null;
export async function authorizePrivacy() {
  // #ifdef MP-WEIXIN
  if (!wx.getPrivacySetting) return;
  const setting: any = await new Promise((resolve, reject) =>
    wx.getPrivacySetting({
      success: resolve,
      fail: () => reject(new Error("隐私指引暂时无法读取")),
    }),
  );
  if (!setting.needAuthorization) return;
  if (pending) throw new Error("请先完成当前授权");
  await new Promise<void>((resolve, reject) => {
    pending = { resolve, reject };
    privacy.title = setting.privacyContractName || privacy.title;
    privacy.visible = true;
  });
  // #endif
}
export function acceptPrivacy() {
  privacy.visible = false;
  pending?.resolve();
  pending = null;
}
export function rejectPrivacy() {
  privacy.visible = false;
  pending?.reject(new Error("已取消，可继续使用文字输入"));
  pending = null;
}
export function openPrivacy() {
  // #ifdef MP-WEIXIN
  wx.openPrivacyContract({
    fail: () =>
      uni.showToast({ title: "请在微信后台配置隐私指引", icon: "none" }),
  });
  // #endif
}
