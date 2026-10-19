export interface SSEFrame { type: string; data: string; id: string }

/** Incremental SSE framing; TextDecoder handles split UTF-8 at the transport. */
export class SSEParser {
  private buffer = '';
  private event = 'message';
  private data: string[] = [];
  private id: string | null = null;
  constructor(private onEvent: (event: SSEFrame) => void, public lastEventId = '0') {}
  feed(text: string, final = false) {
    this.buffer += text;
    let i = 0;
    while (i < this.buffer.length) {
      const ch = this.buffer[i];
      if (ch !== '\r' && ch !== '\n') { i++; continue; }
      if (ch === '\r' && i === this.buffer.length - 1 && !final) break;
      const width = ch === '\r' && this.buffer[i + 1] === '\n' ? 2 : 1;
      const line = this.buffer.slice(0, i);
      this.buffer = this.buffer.slice(i + width);
      i = 0;
      this.line(line);
    }
  }
  private line(line: string) {
    if (!line) {
      if (this.id !== null) this.lastEventId = this.id;
      if (this.data.length) this.onEvent({ type: this.event, data: this.data.join('\n'), id: this.lastEventId });
      this.event = 'message'; this.data = []; this.id = null;
      return;
    }
    if (line[0] === ':') return;
    const pos = line.indexOf(':');
    const name = pos < 0 ? line : line.slice(0, pos);
    let value = pos < 0 ? '' : line.slice(pos + 1);
    if (value.startsWith(' ')) value = value.slice(1);
    if (name === 'event') this.event = value;
    if (name === 'data') this.data.push(value);
    if (name === 'id' && !value.includes('\0')) this.id = value;
  }
}
