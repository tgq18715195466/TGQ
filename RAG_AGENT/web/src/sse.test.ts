import { describe, expect, it } from 'vitest';
import { SSEParser, type SSEFrame } from './sse';

describe('SSE framing', () => {
  it('preserves Chinese UTF-8, multiline data and CRLF at every byte boundary', () => {
    const events: SSEFrame[] = [];
    const parser = new SSEParser(e => events.push(e));
    const decoder = new TextDecoder();
    const bytes = new TextEncoder().encode(': heartbeat\r\nid: 7\r\nevent: answer.delta\r\ndata: {"text":\r\ndata: "中文"}\r\n\r\n');
    for (const byte of bytes) parser.feed(decoder.decode(new Uint8Array([byte]), { stream: true }));
    parser.feed(decoder.decode(), true);
    expect(events).toEqual([{ type: 'answer.delta', id: '7', data: '{"text":\n"中文"}' }]);
  });
  it('inherits the resume cursor, ignores null IDs and never dispatches incomplete frames', () => {
    const events: SSEFrame[] = [];
    const parser = new SSEParser(e => events.push(e), '9');
    parser.feed('id: bad\0id\ndata: first\n\ndata: partial');
    expect(events).toEqual([{ type: 'message', id: '9', data: 'first' }]);
    parser.feed('', true);
    expect(events).toHaveLength(1);
  });
});
