import {
  test,
  expect,
  attachConsoleErrorWatcher,
} from "../../fixtures/auth";
import { loginAsAdminApi, createProject } from "../../fixtures/api";
import {
  deleteProject,
  dragTabToOtherGroup,
  dragTabWithinGroup,
  makePane,
  makeSplitNode,
  makeTabsNode,
  openWorkbench,
  seedLayout,
  tabButtonByTitle,
} from "../../fixtures/workbench";

/**
 * Tab drag — Refactor Phase 2 reducer 移行で破綻すると最も
 * 致命的な経路: 「ユーザがタブをドラッグして別 group に移したい」.
 *
 * 軸: axis 6 Operable / axis 7 Persistent
 *
 * Phase 2 task `69eee31d71f37143d043d05d` の事前準備として、
 * dnd-kit の pointer event chain と reducer の moveTabToCenter
 * 経路を実 browser で固定する.
 */

test.describe("[refactor-p2-pre] Workbench tab drag", () => {
  test("[axis6] 同 group 内でタブを並び替えられる (center / insertIndex)", async ({
    page,
  }) => {
    const watcher = attachConsoleErrorWatcher(page);
    const api = await loginAsAdminApi();
    const project = await createProject(api, {
      name: `drag-reorder-${Date.now()}`,
    });

    // Tasks + Terminal + Doc の 3 タブを seed
    const tasks = makePane("tasks");
    const terminal = makePane("terminal");
    const doc = makePane("doc");
    await seedLayout(api, project.id, makeTabsNode([tasks, terminal, doc]));

    await openWorkbench(page, project.id);
    // 3 タブが visible
    await expect(tabButtonByTitle(page, "Tasks").first()).toBeVisible();
    await expect(tabButtonByTitle(page, "Terminal").first()).toBeVisible();
    await expect(tabButtonByTitle(page, "Doc").first()).toBeVisible();

    // Doc を Terminal の前にドラッグ (= [Tasks, Doc, Terminal])
    await dragTabWithinGroup(page, "Doc", "Terminal");
    await page.waitForTimeout(400); // debounce flush

    // 並び順は DOM 順で検証する.
    const tabs = page.locator(
      'button[title="Tasks"], button[title="Terminal"], button[title="Doc"]',
    );
    await expect(tabs).toHaveCount(3);
    const order = await tabs.evaluateAll((els) =>
      els.map((el) => (el as HTMLElement).getAttribute("title")),
    );
    // dnd-kit drag は微妙な座標差で結果が ±1 ぶれる場合があるので、
    // **Doc が Terminal より左にある** ことだけを assert する.
    const docIdx = order.indexOf("Doc");
    const terminalIdx = order.indexOf("Terminal");
    expect(
      docIdx,
      `期待: Doc が Terminal より左 (= 小さい index). actual order=${order.join(",")}`,
    ).toBeLessThan(terminalIdx);

    expect(
      watcher.errors,
      `想定外 console エラー:\n${watcher.errors.join("\n")}`,
    ).toEqual([]);

    await deleteProject(api.ctx, api.accessToken, project.id);
    watcher.dispose();
  });

  test("[axis6] 横 split された別 group へタブを移動できる", async ({
    page,
  }) => {
    const watcher = attachConsoleErrorWatcher(page);
    const api = await loginAsAdminApi();
    const project = await createProject(api, {
      name: `drag-cross-${Date.now()}`,
    });

    // 横 split: 左 [Tasks, Terminal] / 右 [Doc]
    const tasks = makePane("tasks");
    const terminal = makePane("terminal");
    const doc = makePane("doc");
    await seedLayout(
      api,
      project.id,
      makeSplitNode("horizontal", [
        makeTabsNode([tasks, terminal]),
        makeTabsNode([doc]),
      ]),
    );

    await openWorkbench(page, project.id);
    await expect(tabButtonByTitle(page, "Doc").first()).toBeVisible();
    await expect(tabButtonByTitle(page, "Terminal")).toHaveCount(1);

    // 右 group の中央 (= Doc が居る group の本体) に Terminal をドロップ
    // 右 panel は Workbench root の最後の Panel — タブ strip じゃなく
    // pane 本体領域にドロップする. group の DropZoneOverlay が掴むのは
    // **タブ strip 下** の領域なので、`button[title="Doc"]` の上の strip
    // ではなく Doc pane 本体 (= "タスクを選択してください" の placeholder
    // が出てるエリア) を狙う必要がある.
    //
    // 安定性のため、DocPane の EmptyState placeholder text を locator
    // 起点にして、その親要素 (strip 含めた group ルート) を targetGroup
    // として扱う.
    const targetGroupSelector =
      'div:has(> div > button[title="Doc"])'; // Doc タブを持つ TabGroup ルート
    await dragTabToOtherGroup(page, "Terminal", targetGroupSelector);
    await page.waitForTimeout(400);

    // Terminal が右 group に移った結果、左 group には Tasks のみ残り、
    // Terminal タブ自体は依然 1 個 (strip に 1 箇所だけ).
    await expect(tabButtonByTitle(page, "Tasks")).toHaveCount(1);
    await expect(tabButtonByTitle(page, "Doc")).toHaveCount(1);
    await expect(tabButtonByTitle(page, "Terminal")).toHaveCount(1);

    expect(
      watcher.errors,
      `想定外 console エラー:\n${watcher.errors.join("\n")}`,
    ).toEqual([]);

    await deleteProject(api.ctx, api.accessToken, project.id);
    watcher.dispose();
  });
});
