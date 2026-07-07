/** テスト用の EventSource スタブ(InfoPanel.test.tsx / ViewerHeader.literal-style.test.tsx と同方針)。 */
export class MockEventSource {
  static instances: MockEventSource[] = [];
  url: string;
  closed = false;
  private listeners: Record<string, ((e: MessageEvent<string>) => void)[]> = {};

  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }

  addEventListener(type: string, cb: EventListener): void {
    (this.listeners[type] ??= []).push(cb as (e: MessageEvent<string>) => void);
  }

  removeEventListener(): void {
    // 未使用。
  }

  close(): void {
    this.closed = true;
  }

  dispatch(type: string, data?: unknown): void {
    const event = { data: data === undefined ? "" : JSON.stringify(data) } as MessageEvent<string>;
    for (const cb of this.listeners[type] ?? []) cb(event);
  }

  static reset(): void {
    MockEventSource.instances = [];
  }
}

/** `MockEventSource.instances[0]` を non-null 断定なしで取り出す(lint: no-non-null-assertion)。 */
export function firstEventSource(): MockEventSource {
  const source = MockEventSource.instances[0];
  if (!source) throw new Error("no MockEventSource instance was created");
  return source;
}
