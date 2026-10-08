export class EventReader {
  emit: (event: any) => void;
  bytes: number[];
  text: string;
  constructor(emit: (event: any) => void) {
    this.emit = emit;
    this.bytes = [];
    this.text = "";
  }
  push(buffer: ArrayBuffer | null, final = false) {
    this.bytes = this.bytes.concat(
      Array.from(new Uint8Array(buffer || new ArrayBuffer(0))),
    );
    let i = 0,
      out = "";
    while (i < this.bytes.length) {
      const b = this.bytes[i];
      let n =
        b < 128
          ? 1
          : b >= 194 && b < 224
            ? 2
            : b >= 224 && b < 240
              ? 3
              : b >= 240 && b < 245
                ? 4
                : 1;
      if (i + n > this.bytes.length && !final) break;
      let cp = n === 1 ? (b < 128 ? b : 65533) : b & ((1 << (7 - n)) - 1);
      let valid = i + n <= this.bytes.length;
      for (let j = 1; j < n && valid; j++) {
        const c = this.bytes[i + j];
        if ((c & 192) !== 128) valid = false;
        else cp = (cp << 6) | (c & 63);
      }
      if (
        !valid ||
        (n === 2 && cp < 128) ||
        (n === 3 && cp < 2048) ||
        (n === 4 && cp < 65536) ||
        cp > 1114111 ||
        (cp >= 55296 && cp <= 57343)
      ) {
        cp = 65533;
        n = 1;
      }
      out += String.fromCodePoint(cp);
      i += n;
    }
    this.bytes = this.bytes.slice(i);
    this.feed(out);
  }
  feed(text: string) {
    this.text += text.replace(/\r\n/g, "\n");
    let end;
    while ((end = this.text.indexOf("\n\n")) >= 0) {
      const block = this.text.slice(0, end);
      this.text = this.text.slice(end + 2);
      const data = block
        .split("\n")
        .filter((x) => x.startsWith("data:"))
        .map((x) => x.slice(5).trimStart())
        .join("\n");
      if (data) this.emit(JSON.parse(data));
    }
  }
}
