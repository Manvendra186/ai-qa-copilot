/**
 * Faithful subset of the @playwright/test ^1.48.0 type surface (S2.3 gate stub).
 *
 * The workspace is offline and the sample repositories ship stub lockfiles by
 * design, so the S2.3 lint/type gate (qa_copilot_ai.automation.checker) type-
 * checks AI-generated Playwright specs against this stand-in instead of the
 * real package. It mirrors the public API of @playwright/test for the surface
 * the automation golden set exercises — keep both in sync.
 */

/** Stand-in for Node's Buffer (the real type comes from @types/node). */
export type Buffer = Uint8Array;

/** A single HTTP response (subset). */
export interface Response {
  url(): string;
  status(): number;
  statusText(): string;
  ok(): boolean;
  headers(): Record<string, string>;
  headerValue(name: string): string | null;
  allHeaders(): Record<string, string>;
  body(): Promise<Buffer>;
  text(): Promise<string>;
  json(): Promise<unknown>;
  request(): { url(): string; method(): string; postData(): string | null };
}

/** Aria roles accepted by getByRole (real-Playwright union). */
export type AriaRole =
  | "alert"
  | "alertdialog"
  | "application"
  | "article"
  | "banner"
  | "button"
  | "cell"
  | "checkbox"
  | "columnheader"
  | "combobox"
  | "complementary"
  | "contentinfo"
  | "definition"
  | "dialog"
  | "directory"
  | "document"
  | "feed"
  | "figure"
  | "form"
  | "generic"
  | "grid"
  | "gridcell"
  | "group"
  | "heading"
  | "img"
  | "link"
  | "list"
  | "listbox"
  | "listitem"
  | "log"
  | "main"
  | "marquee"
  | "math"
  | "menu"
  | "menubar"
  | "menuitem"
  | "menuitemcheckbox"
  | "menuitemradio"
  | "navigation"
  | "none"
  | "note"
  | "option"
  | "presentation"
  | "progressbar"
  | "radio"
  | "radiogroup"
  | "region"
  | "row"
  | "rowgroup"
  | "scrollbar"
  | "search"
  | "searchbox"
  | "separator"
  | "slider"
  | "spinbutton"
  | "status"
  | "switch"
  | "tab"
  | "table"
  | "tablist"
  | "tabpanel"
  | "term"
  | "textbox"
  | "timer"
  | "toolbar"
  | "tooltip"
  | "tree"
  | "treegrid"
  | "treeitem";

export interface FrameLocator {
  frameLocator(pageSelector: string): FrameLocator;
  getByAltText(text: string | RegExp, options?: { exact?: boolean }): Locator;
  getByLabel(text: string | RegExp, options?: { exact?: boolean; includeHidden?: boolean }): Locator;
  getByPlaceholder(text: string | RegExp, options?: { exact?: boolean }): Locator;
  getByRole(
    role: AriaRole,
    options?: {
      checked?: boolean;
      disabled?: boolean;
      expanded?: boolean;
      includeHidden?: boolean;
      level?: number;
      name?: string | RegExp;
      pressed?: boolean;
      selected?: boolean;
    },
  ): Locator;
  getByTestId(testId: string): Locator;
  getByText(text: string | RegExp, options?: { exact?: boolean; includeHidden?: boolean }): Locator;
  getByTitle(text: string | RegExp, options?: { exact?: boolean }): Locator;
  locator(selector: string, options?: { has?: Locator; hasNot?: Locator; hasText?: string | RegExp }): Locator;
  nth(index: number): FrameLocator;
  first(): FrameLocator;
  last(): FrameLocator;
}

/** A web-first Locator (subset of the real API). */
export interface Locator {
  click(options?: {
    button?: "left" | "right" | "middle";
    clickCount?: number;
    delay?: number;
    force?: boolean;
    position?: { x: number; y: number };
    timeout?: number;
  }): Promise<void>;
  dblclick(options?: { delay?: number; force?: boolean; position?: { x: number; y: number }; timeout?: number }): Promise<void>;
  fill(value: string, options?: { delay?: number; force?: boolean; timeout?: number }): Promise<void>;
  type(text: string, options?: { delay?: number; timeout?: number }): Promise<void>;
  press(key: string, options?: { delay?: number; timeout?: number }): Promise<void>;
  focus(): Promise<void>;
  check(options?: { force?: boolean; timeout?: number }): Promise<void>;
  uncheck(options?: { force?: boolean; timeout?: number }): Promise<void>;
  hover(options?: { force?: boolean; position?: { x: number; y: number }; timeout?: number }): Promise<void>;
  selectOption(
    values:
      | string
      | string[]
      | { value?: string; label?: string; index?: number }
      | Array<{ value?: string; label?: string; index?: number }>,
    options?: { timeout?: number },
  ): Promise<void>;
  setInputFiles(files: string | string[] | { name: string; mimeType?: string; buffer: Buffer }): Promise<void>;
  getTextContent(options?: { timeout?: number }): Promise<string | null>;
  innerText(options?: { timeout?: number }): Promise<string>;
  innerHTML(options?: { timeout?: number }): Promise<string>;
  inputValue(options?: { timeout?: number }): Promise<string>;
  getAttribute(name: string, options?: { timeout?: number }): Promise<string | null>;
  count(): Promise<number>;
  all(): Promise<Locator[]>;
  nth(index: number): Locator;
  first(): Locator;
  last(): Locator;
  getByAltText(text: string | RegExp, options?: { exact?: boolean }): Locator;
  getByLabel(text: string | RegExp, options?: { exact?: boolean; includeHidden?: boolean }): Locator;
  getByPlaceholder(text: string | RegExp, options?: { exact?: boolean }): Locator;
  getByRole(
    role: AriaRole,
    options?: {
      checked?: boolean;
      disabled?: boolean;
      expanded?: boolean;
      includeHidden?: boolean;
      level?: number;
      name?: string | RegExp;
      pressed?: boolean;
      selected?: boolean;
    },
  ): Locator;
  getByTestId(testId: string): Locator;
  getByText(text: string | RegExp, options?: { exact?: boolean; includeHidden?: boolean }): Locator;
  getByTitle(text: string | RegExp, options?: { exact?: boolean }): Locator;
  locator(selector: string, options?: { has?: Locator; hasNot?: Locator; hasText?: string | RegExp }): Locator;
  frameLocator(frameSelector: string): FrameLocator;
  waitFor(options?: { state?: "attached" | "detached" | "visible" | "hidden"; timeout?: number }): Promise<void>;
  isVisible(options?: { timeout?: number }): Promise<boolean>;
  isEnabled(options?: { timeout?: number }): Promise<boolean>;
  isDisabled(options?: { timeout?: number }): Promise<boolean>;
  isChecked(options?: { timeout?: number }): Promise<boolean>;
  boundingBox(options?: { timeout?: number }): Promise<{ x: number; y: number; width: number; height: number } | null>;
}

/** A BrowserContext page (subset of the real API). */
export interface Page {
  url(): string;
  title(): Promise<string>;
  content(): Promise<string>;
  goto(
    url: string,
    options?: {
      referer?: string;
      timeout?: number;
      waitUntil?: "load" | "domcontentloaded" | "networkidle" | "commit";
    },
  ): Promise<null | Response>;
  setDefaultTimeout(timeout: number): void;
  waitForLoadState(
    state?: "load" | "domcontentloaded" | "networkidle" | "commit",
    options?: { timeout?: number },
  ): Promise<void>;
  waitForURL(
    url: string | RegExp | ((url: URL) => boolean),
    options?: { timeout?: number; waitUntil?: "load" | "domcontentloaded" | "networkidle" | "commit" },
  ): Promise<void>;
  waitForTimeout(timeout: number): Promise<void>;
  waitForFunction(
    pageFunction: string | ((...args: unknown[]) => unknown),
    arg?: unknown,
    options?: { polling?: "raf" | "mutation" | number; timeout?: number },
  ): Promise<unknown>;
  waitForSelector(
    selector: string,
    options?: { state?: "attached" | "detached" | "visible" | "hidden"; timeout?: number },
  ): Promise<Locator>;
  evaluate<R>(pageFunction: string | ((...args: unknown[]) => R | Promise<R>), arg?: unknown): Promise<R>;
  on(event: string, listener: (...args: unknown[]) => void): this;
  once(event: string, listener: (...args: unknown[]) => void): this;
  locator(selector: string, options?: { has?: Locator; hasNot?: Locator; hasText?: string | RegExp }): Locator;
  frameLocator(frameSelector: string): FrameLocator;
  getByAltText(text: string | RegExp, options?: { exact?: boolean }): Locator;
  getByLabel(text: string | RegExp, options?: { exact?: boolean; includeHidden?: boolean }): Locator;
  getByPlaceholder(text: string | RegExp, options?: { exact?: boolean }): Locator;
  getByRole(
    role: AriaRole,
    options?: {
      checked?: boolean;
      disabled?: boolean;
      expanded?: boolean;
      includeHidden?: boolean;
      level?: number;
      name?: string | RegExp;
      pressed?: boolean;
      selected?: boolean;
    },
  ): Locator;
  getByTestId(testId: string): Locator;
  getByText(text: string | RegExp, options?: { exact?: boolean; includeHidden?: boolean }): Locator;
  getByTitle(text: string | RegExp, options?: { exact?: boolean }): Locator;
  frames(): Locator[];
  screenshot(options?: {
    animations?: "allow" | "disabled";
    caret?: "initial" | "hide";
    fullPage?: boolean;
    path?: string;
    quality?: number;
    timeout?: number;
    type?: "png" | "jpeg";
  }): Promise<Buffer>;
  keyboard: {
    insertText(text: string): Promise<void>;
    press(key: string, options?: { delay?: number }): Promise<void>;
    type(text: string, options?: { delay?: number }): Promise<void>;
    down(key: string): Promise<void>;
    up(key: string): Promise<void>;
    sendCharacter(char: string): Promise<void>;
  };
  mouse: {
    click(x: number, y: number, options?: { button?: "left" | "right" | "middle"; clickCount?: number; delay?: number }): Promise<void>;
    dblclick(x: number, y: number, options?: { button?: "left" | "right" | "middle"; delay?: number }): Promise<void>;
    down(options?: { button?: "left" | "right" | "middle" }): Promise<void>;
    up(options?: { button?: "left" | "right" | "middle" }): Promise<void>;
    move(x: number, y: number, options?: { steps?: number }): Promise<void>;
    wheel(deltaX: number, deltaY: number): Promise<void>;
  };
  request: {
    fetch(url: string, options?: Record<string, unknown>): Promise<Response>;
    get(url: string, options?: Record<string, unknown>): Promise<Response>;
    post(url: string, options?: Record<string, unknown>): Promise<Response>;
  };
}

/** Test-scoped data (subset). */
export interface TestInfo {
  title: string;
  titlePath: string[];
  testId: string;
  workerIndex: number;
  testIndex: number;
  retry: number;
  parallelIndex: number;
  projectName: string;
  repeatEachIndex: number;
  expectedStatus: "passed" | "failed" | "timedOut" | "skipped" | "flaky";
  status: "passed" | "failed" | "timedOut" | "skipped" | "flaky";
  timeout: number;
  annotation: { type: string; description?: string }[];
  attach(name: string, options: { path?: string; body?: Buffer; contentType?: string; size?: number }): Promise<void>;
  attachments: { name: string; path?: string; body?: Buffer; contentType: string }[];
  failures(): { error?: unknown }[];
  skip(condition?: boolean): void;
  fail(condition?: boolean): void;
  fixme(condition?: boolean): void;
  slow(): void;
  log: (string | unknown)[];
  logger: { info: (...args: unknown[]) => void; error: (...args: unknown[]) => void };
  timeout(timeout: number): void;
  outputPath(...file: string[]): string;
  snapshotPath(...file: string[]): string;
}

/** The fixtures a test body receives (subset — the real API also has
 *  browser/context/browserName etc.; `page` is what generated specs use). */
export interface TestFixture {
  page: Page;
}

type TestFunction = (arg1: TestFixture, arg2: TestInfo) => void | Promise<void>;

export interface Test {
  (title: string, body: TestFunction): void;
  (title: string, body: TestFunction & { annotations?: { type: string; description?: string }[] }): void;
  (title: string, body: unknown): void;
  step(title: string, body: () => void | Promise<void>): void;
  slow(): void;
  configure(options: Record<string, unknown>): void;
  describe(title: string, body: () => void): void;
  skip(condition?: boolean): void;
  fail(condition?: boolean): void;
  fixme(condition?: boolean): void;
  each(table: unknown[]): { (title: string, body: (row: unknown) => void | Promise<void>): void };
}

export interface ExpectMatcher {
  (expected?: unknown): void;
  toBe(expected: unknown): void;
  toBeCloseTo(expected: number, numDigits?: number): void;
  toBeDefined(): void;
  toBeFalsy(): void;
  toBeGreaterThan(expected: number): void;
  toBeGreaterThanOrEqual(expected: number): void;
  toBeInstanceOf(expected: unknown): void;
  toBeNull(): void;
  toBeNaN(): void;
  toBeNegative(): void;
  toBeTruthy(): void;
  toBeUndefined(): void;
  toBeLessThan(expected: number): void;
  toBeLessThanOrEqual(expected: number): void;
  toBeChecked(options?: { timeout?: number }): void;
  toBeDisabled(options?: { timeout?: number }): void;
  toBeEnabled(options?: { timeout?: number }): void;
  toBeEmpty(options?: { timeout?: number }): void;
  toBeHidden(options?: { timeout?: number }): void;
  toBeVisible(options?: { timeout?: number }): void;
  toContain(expected: unknown): void;
  toContainText(expected: string | RegExp): void;
  toHaveCount(expected: number, options?: { timeout?: number }): void;
  // NOTE: `toHaveTextContent` is intentionally NOT declared here — it is a
  // Cypress assertion, not a Playwright one (real @playwright/test types
  // reject it too), so the S2.3 type gate must catch it as well. Use
  // toHaveText / toContainText instead. See the negative probe in
  // tests/unit/test_automation_agent.py.
  toHaveText(expected: string | RegExp | Array<string | RegExp>, options?: { timeout?: number }): void;
  toHaveAttribute(name: string, value?: string | RegExp): void;
  toHaveClass(expected: string | RegExp | Array<string | RegExp>): void;
  toHaveId(expected: string | RegExp): void;
  toHaveValue(expected: string | RegExp | Array<string | RegExp>): void;
  toHaveLength(expected: number): void;
  toHaveTitle(expected: string | RegExp): void;
  toHaveURL(expected: string | RegExp): void;
  toMatch(expected: string | RegExp): void;
  toMatchObject(expected: Record<string, unknown> | Record<string, unknown>[]): void;
  toMatchSnapshot(options?: { timeout?: number }): void;
  not: ExpectMatcher;
  soft: ExpectMatcher;
  toHaveScreenshot(options?: { path?: string; timeout?: number }): void;
}

export interface Expect {
  (actual: unknown, message?: string): ExpectMatcher;
  soft(actual: unknown, message?: string): void;
  toBe: (expected: unknown) => void;
  not: ExpectMatcher;
  toMatch: (expected: string | RegExp) => void;
  toMatchObject: (expected: Record<string, unknown> | Record<string, unknown>[]) => void;
  toContain: (expected: unknown) => void;
  toHaveLength: (expected: number) => void;
  toBeCloseTo: (expected: number, numDigits?: number) => void;
  toBeDefined: () => void;
  toBeFalsy: () => void;
  toBeTruthy: () => void;
  toBeNull: () => void;
  toBeNaN: () => void;
  toBeUndefined: () => void;
  toBeGreaterThan: (expected: number) => void;
  toBeGreaterThanOrEqual: (expected: number) => void;
  toBeInstanceOf: (expected: unknown) => void;
  toBeNegative: () => void;
  toBeLessThan: (expected: number) => void;
  toBeLessThanOrEqual: (expected: number) => void;
  toBeChecked: (options?: { timeout?: number }) => void;
  toBeDisabled: (options?: { timeout?: number }) => void;
  toBeEnabled: (options?: { timeout?: number }) => void;
  toBeEmpty: (options?: { timeout?: number }) => void;
  toBeHidden: (options?: { timeout?: number }) => void;
  toBeVisible: (options?: { timeout?: number }) => void;
  toContainText: (expected: string | RegExp) => void;
  toHaveCount: (expected: number, options?: { timeout?: number }) => void;
  toHaveText: (expected: string | RegExp | Array<string | RegExp>, options?: { timeout?: number }) => void;
  toHaveAttribute: (name: string, value?: string | RegExp) => void;
  toHaveClass: (expected: string | RegExp | Array<string | RegExp>) => void;
  toHaveId: (expected: string | RegExp) => void;
  toHaveValue: (expected: string | RegExp | Array<string | RegExp>) => void;
  toHaveTitle: (expected: string | RegExp) => void;
  toHaveURL: (expected: string | RegExp) => void;
  toMatchSnapshot: (options?: { timeout?: number }) => void;
}

/** The test runner entry point (subset of the real API). */
export const test: Test;
export const expect: Expect;

/** The default test export — mirrors the real package's `test` re-export. */
export const it: Test;