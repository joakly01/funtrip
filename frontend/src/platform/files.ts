import { authorizePrivacy } from "./privacy";
import type { SelectedFile } from "../domain/types";
export async function chooseFiles(
  kind: "image" | "file",
  count: number,
): Promise<SelectedFile[]> {
  if (count < 1) throw new Error("每次最多添加 6 个附件");
  // #ifdef H5
  return new Promise((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.multiple = true;
    input.accept =
      kind === "image"
        ? "image/jpeg,image/png,image/webp,image/gif"
        : ".txt,.md,.csv,.json,.pdf,.docx,.xlsx,.mp3,.wav,.m4a,.webm,.ogg,.flac,.aac";
    input.onchange = () =>
      resolve(
        Array.from(input.files || [])
          .slice(0, count)
          .map((file) => ({ name: file.name, size: file.size, file })),
      );
    input.oncancel = () => resolve([]);
    input.click();
  });
  // #endif
  // #ifdef MP-WEIXIN
  await authorizePrivacy();
  return new Promise((resolve, reject) => {
    const options = {
      count,
      success: (r: any) =>
        resolve(
          r.tempFiles.map((file: any) => ({
            path: file.tempFilePath || file.path,
            name: file.name || "图片.jpg",
            size: file.size,
          })),
        ),
      fail: (e: any) =>
        /cancel/.test(e.errMsg)
          ? resolve([])
          : reject(new Error("附件选择失败")),
    };
    if (kind === "image") wx.chooseMedia({ ...options, mediaType: ["image"] });
    else wx.chooseMessageFile({ ...options, type: "file" });
  });
  // #endif
}
