/**
 * Workbench 用 e2e helper.
 *
 * Refactor Phase 2 (`69eee31d71f37143d043d05d`) の reducer 移行を
 * 安全に進めるための回帰ネットを敷くため、既存 fixture (auth / api)
 * の上に Workbench 固有の操作を集約する.
 *
 * 設計方針:
 *   - **シードは API 経由のみ** (e2e-strategy.md §1.2): ログイン・
 *     project 作成は fixtures/api を使い、layout は backend に PUT.
 *     localStorage 直書きは「他タブからの cross-tab 通知」を再現する
 *     場合のみ許可 (writeLayoutLocalStorage).
 *   - **DnD は実 pointer event を多段階で送る**: dnd-kit の
 *     PointerSensor は activationConstraint.distance=5 なので最低 6px
 *     以上動かす. 1 ステップでは hover overlay が更新されないので
 *     ``steps`` を 10 以上にして DragMove イベントを通す.
 *   - **console.error は 0 件を assert** (auth 同様): 既存 spec の
 *     パターンを踏襲し各 spec で attachConsoleErrorWatcher を使う.
 */
import type { APIRequestContext, BrowserContext, Page } from "@playwright/test";
import { expect } from "@playwright/test";
import { type ApiClient } from "./api";
import { loginViaUi } from "./auth";

const API_TIMEOUT_MS = 30_000;

// ── Layout helpers (server seed) ──────────────────────────────

/** Workbench layout tree. backend `LayoutTree` の subset. */
export type LayoutTree =
  | { kind: "tabs"; id: string; activeTabId: string; tabs: Pane[] }
  | {
      kind: "split";
      id: string;
      orientation: "horizontal" | "vertical";
      sizes: number[];
      children: LayoutTree[];
    };

export interface Pane {
  id: string;
  paneType:
    | "tasks"
    | "task-detail"
    | "terminal"
    | "doc"
    | "documents"
    | "file-browser"
    | "error-tracker";
  paneConfig: Record<string, unknown>;
}

/** Backend に layout を PUT して固定する. workbench-add-tab-race.spec
 *  の seed パターンと同じく、reload 後の hydrate で確実にこの tree
 *  になるようにする. */
export async function seedLayout(
  api: ApiClient,
  projectId: string,
  tree: LayoutTree,
  clientId = "e2e-seed",
): Promise<void> {
  const res = await api.ctx.put(`/api/v1/workbench/layouts/${projectId}`, {
    data: { tree, schema_version: 1, client_id: clientId },
    headers: {
      Authorization: `Bearer ${api.accessToken}`,
      "Content-Type": "application/json",
    },
    timeout: API_TIMEOUT_MS,
  });
  if (!res.ok()) {
    throw new Error(
      `[fixture] seed layout PUT failed: ${res.status()} ${await res.text()}`,
    );
  }
}

/** Layout 構築ヘルパ (id は test 専用 prefix で衝突回避). */
let _idCounter = 0;
function nextId(prefix: string): string {
  _idCounter += 1;
  return `${prefix}-${Date.now().toString(36)}-${_idCounter}`;
}

export function makePane(
  paneType: Pane["paneType"],
  paneConfig: Record<string, unknown> = {},
): Pane {
  return { id: nextId("p"), paneType, paneConfig };
}

export function makeTabsNode(tabs: Pane[]): LayoutTree {
  if (tabs.length === 0) {
    throw new Error("makeTabsNode requires at least one tab");
  }
  return {
    kind: "tabs",
    id: nextId("g"),
    activeTabId: tabs[0].id,
    tabs,
  };
}

export function makeSplitNode(
  orientation: "horizontal" | "vertical",
  children: LayoutTree[],
): LayoutTree {
  if (children.length < 2) {
    throw new Error("makeSplitNode requires at least 2 children");
  }
  const eq = 100 / children.length;
  return {
    kind: "split",
    id: nextId("s"),
    orientation,
    sizes: children.map(() => eq),
    children,
  };
}

// ── Document seed (?doc= deeplink test) ───────────────────────

/** 1 件の Markdown document を作成. ?doc= deeplink テスト用. */
export async function createDocument(
  api: ApiClient,
  projectId: string,
  body: { title: string; content: string; category?: string; tags?: string[] },
): Promise<{ id: string; title: string }> {
  const res = await api.ctx.post(
    `/api/v1/projects/${projectId}/documents/`,
    {
      data: {
        title: body.title,
        content: body.content,
        category: body.category ?? "design",
        tags: body.tags ?? [],
      },
      headers: {
        Authorization: `Bearer ${api.accessToken}`,
        "Content-Type": "application/json",
      },
      timeout: API_TIMEOUT_MS,
    },
  );
  if (!res.ok()) {
    throw new Error(
      `[fixture] create document failed: ${res.status()} ${await res.text()}`,
    );
  }
  const json = (await res.json()) as { id: string; title: string };
  return json;
}

// ── Page navigation ────────────────────────────────────────────

/** UI ログイン → /projects/<id> を開いて hydrate 完了まで待つ.
 *  hydrate 完了は **Tasks タブ (= 既定 layout の最初のタブ)** が
 *  `aria-label="Add tab"` の隣に並ぶことで保証する.  */
export async function openWorkbench(
  page: Page,
  projectId: string,
  options: { skipLogin?: boolean; query?: string } = {},
): Promise<void> {
  if (!options.skipLogin) {
    await loginViaUi(page);
  }
  const target = `/projects/${projectId}${options.query ? `?${options.query}` : ""}`;
  await page.goto(target);
  await page.waitForURL(
    new RegExp(`/projects/${projectId}(?:[/?].*)?$`),
    { timeout: 10_000 },
  );
  // Workbench mount 完了 = + (Add tab) ボタンが見える
  await expect(
    page.getByRole("button", { name: "Add tab", exact: true }).first(),
  ).toBeVisible({ timeout: 10_000 });
}

// ── Tab strip helpers ──────────────────────────────────────────

/** title 属性で tab 本体ボタンを取得. ``getByRole('button', { name })``
 *  だと close affordance (×) も拾うので title 経由で限定する. */
export function tabButtonByTitle(page: Page, title: string) {
  return page.locator(`button[title="${title}"]`);
}

/** + (Add tab) → メニューから paneType を選択して新規タブを追加. */
export async function pickPaneType(page: Page, label: string): Promise<void> {
  const addButton = page
    .getByRole("button", { name: "Add tab", exact: true })
    .first();
  await expect(addButton).toBeVisible({ timeout: 5_000 });
  await addButton.click();
  const picker = page.getByRole("menu", { name: "Add tab type" });
  await expect(picker).toBeVisible({ timeout: 5_000 });
  await picker.getByRole("menuitem", { name: label, exact: true }).click();
}

// ── DnD helpers ────────────────────────────────────────────────

export type EdgeZone = "top" | "right" | "bottom" | "left";

/** タブを **同じ group 内の特定 index** にドラッグして並び替える.
 *  PointerSensor の activation distance=5px を超えるよう 6px 以上を
 *  最初に動かしてから本来の目的地へ多段階移動する. */
export async function dragTabWithinGroup(
  page: Page,
  fromTabTitle: string,
  toTabTitle: string,
): Promise<void> {
  const from = tabButtonByTitle(page, fromTabTitle).first();
  const to = tabButtonByTitle(page, toTabTitle).first();
  await expect(from).toBeVisible();
  await expect(to).toBeVisible();
  const fromBox = await from.boundingBox();
  const toBox = await to.boundingBox();
  if (!fromBox || !toBox) {
    throw new Error("dragTabWithinGroup: bounding box unavailable");
  }
  const fromX = fromBox.x + fromBox.width / 2;
  const fromY = fromBox.y + fromBox.height / 2;
  // toTab の左半分 (mid より手前) を狙うと「toTab の前に挿入」.
  const toX = toBox.x + toBox.width * 0.25;
  const toY = toBox.y + toBox.height / 2;
  await page.mouse.move(fromX, fromY);
  await page.mouse.down();
  // activation distance を確実に超えるため一旦 +20px 動かしてから目的地へ
  await page.mouse.move(fromX + 20, fromY, { steps: 5 });
  await page.mouse.move(toX, toY, { steps: 15 });
  await page.mouse.up();
}

/** タブを **別 tab group** にドラッグして tabify する.
 *  ``targetGroupCenter`` は対象 group の本体 (タブ strip 下のペイン
 *  領域) の中央. */
export async function dragTabToOtherGroup(
  page: Page,
  fromTabTitle: string,
  targetGroupSelector: string,
): Promise<void> {
  const from = tabButtonByTitle(page, fromTabTitle).first();
  const target = page.locator(targetGroupSelector).first();
  await expect(from).toBeVisible();
  await expect(target).toBeVisible();
  const fromBox = await from.boundingBox();
  const targetBox = await target.boundingBox();
  if (!fromBox || !targetBox) {
    throw new Error("dragTabToOtherGroup: bounding box unavailable");
  }
  const fromX = fromBox.x + fromBox.width / 2;
  const fromY = fromBox.y + fromBox.height / 2;
  const toX = targetBox.x + targetBox.width / 2;
  const toY = targetBox.y + targetBox.height / 2;
  await page.mouse.move(fromX, fromY);
  await page.mouse.down();
  await page.mouse.move(fromX + 20, fromY, { steps: 5 });
  await page.mouse.move(toX, toY, { steps: 20 });
  await page.mouse.up();
}

/** タブを **同じ group の edge band** にドロップして split を作る.
 *  edge band は dndZones.EDGE_FRACTION = 0.2 (= rect の 20%).
 *  ここでは余裕を持って 10% (= 端から rect.width * 0.10) を狙う. */
export async function dragTabToEdge(
  page: Page,
  fromTabTitle: string,
  groupSelector: string,
  edge: EdgeZone,
): Promise<void> {
  const from = tabButtonByTitle(page, fromTabTitle).first();
  const group = page.locator(groupSelector).first();
  await expect(from).toBeVisible();
  await expect(group).toBeVisible();
  const fromBox = await from.boundingBox();
  const gb = await group.boundingBox();
  if (!fromBox || !gb) {
    throw new Error("dragTabToEdge: bounding box unavailable");
  }
  const fromX = fromBox.x + fromBox.width / 2;
  const fromY = fromBox.y + fromBox.height / 2;
  let toX = gb.x + gb.width / 2;
  let toY = gb.y + gb.height / 2;
  switch (edge) {
    case "right":
      toX = gb.x + gb.width * 0.92;
      break;
    case "left":
      toX = gb.x + gb.width * 0.08;
      break;
    case "bottom":
      toY = gb.y + gb.height * 0.92;
      break;
    case "top":
      toY = gb.y + gb.height * 0.08;
      break;
  }
  await page.mouse.move(fromX, fromY);
  await page.mouse.down();
  await page.mouse.move(fromX + 20, fromY, { steps: 5 });
  await page.mouse.move(toX, toY, { steps: 25 });
  // edge zone overlay が確実に highlight されるまで一拍待つ
  await page.waitForTimeout(100);
  await page.mouse.up();
}

// ── Cross-tab sync helpers ─────────────────────────────────────

/** 別タブからの localStorage 書き込みを再現する.
 *
 *  ``WorkbenchPage`` は ``subscribeCrossTab`` (= window 'storage' event)
 *  で他タブの保存を adopt する. Playwright で 2 page を open している
 *  とき、片方の page.evaluate 内で localStorage.setItem しても **その
 *  page 自身には storage event は飛ばない** (browser 仕様). 対側 page
 *  にだけ event が届くので、この関数は writer 側に呼び出す.
 *
 *  ``stamp`` は ``Date.now()`` ベースの ms 値. WorkbenchPage の
 *  ``localStampRef`` と比較して **より新しい** 場合だけ adopt される
 *  ので、reader が初期 hydrate 時点で記録する 0 を超えていれば良い. */
export async function writeLayoutToLocalStorage(
  page: Page,
  projectId: string,
  tree: LayoutTree,
  stamp: number,
): Promise<void> {
  await page.evaluate(
    ({ projectId, tree, stamp }) => {
      const payload = {
        version: 1,
        savedAt: stamp,
        tree,
      };
      window.localStorage.setItem(
        `workbench:layout:${projectId}`,
        JSON.stringify(payload),
      );
    },
    { projectId, tree, stamp },
  );
}

/** 同一 BrowserContext で 2 つ目の page を開き、同じ projectId の
 *  Workbench を hydrate 完了状態にする. cross-tab spec 専用. */
export async function openSecondWorkbenchTab(
  context: BrowserContext,
  projectId: string,
): Promise<Page> {
  const page2 = await context.newPage();
  // 既に 1 ページ目で UI login 済 → cookie 共有なので skipLogin
  await openWorkbench(page2, projectId, { skipLogin: true });
  return page2;
}

// ── Cleanup ───────────────────────────────────────────────────

/** Project を後始末. spec の終端で呼ぶ. */
export async function deleteProject(
  ctx: APIRequestContext,
  accessToken: string,
  projectId: string,
): Promise<void> {
  await ctx.delete(`/api/v1/projects/${projectId}`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
}
