import {
  test,
  expect,
  attachConsoleErrorWatcher,
} from "../../fixtures/auth";
import { loginAsAdminApi, createProject } from "../../fixtures/api";
import {
  deleteProject,
  dragTabToEdge,
  makePane,
  makeTabsNode,
  openWorkbench,
  seedLayout,
  tabButtonByTitle,
} from "../../fixtures/workbench";

/**
 * Split (edge drop) と group 自然消滅 (= 最後のタブを × で閉じる).
 *
 * 現在の UI では「Split」「Close group」専用ボタンは無く、edge band
 * への DnD と × での tab close が代替手段. (TabGroup.tsx §237-245
 * のコメント参照: ⋮ menu は P3-5 で撤去された.)
 *
 * Phase 2 reducer 移行で破綻すると：
 *   - moveTabToEdge → splitTabGroup の path 全体が動かなくなる
 *   - closeTab で group が collapse する treeUtils 経路がエンバグする
 * いずれも本 spec で固定する.
 */

test.describe("[refactor-p2-pre] Workbench split & group collapse", () => {
  test("[axis6] タブを右 edge にドロップすると horizontal split される", async ({
    page,
  }) => {
    const watcher = attachConsoleErrorWatcher(page);
    const api = await loginAsAdminApi();
    const project = await createProject(api, {
      name: `split-h-${Date.now()}`,
    });

    // Tasks + Terminal の 2 タブを 1 group に seed
    await seedLayout(
      api,
      project.id,
      makeTabsNode([makePane("tasks"), makePane("terminal")]),
    );

    await openWorkbench(page, project.id);
    await expect(tabButtonByTitle(page, "Tasks").first()).toBeVisible();
    await expect(tabButtonByTitle(page, "Terminal").first()).toBeVisible();

    // 元 group の selector — Tasks タブを含む TabGroup ルート div
    const groupSelector = 'div:has(> div > button[title="Tasks"])';

    // Terminal を右 edge にドロップ → 右側に新 group が誕生
    await dragTabToEdge(page, "Terminal", groupSelector, "right");
    await page.waitForTimeout(400); // debounce

    // assertion: タブ strip ("Add tab" ボタン) が 2 個に増える
    const addButtons = page.getByRole("button", {
      name: "Add tab",
      exact: true,
    });
    await expect(addButtons).toHaveCount(2, { timeout: 5_000 });

    // Tasks と Terminal がそれぞれ別 group に
    await expect(tabButtonByTitle(page, "Tasks")).toHaveCount(1);
    await expect(tabButtonByTitle(page, "Terminal")).toHaveCount(1);

    // react-resizable-panels の Panel が 2 つ生成されている
    // (v4.10 では Panel のみ data-panel="true" を持つ; Group には専用
    // data-属性は無いため Panel 数で判定する).
    const panels = page.locator("[data-panel]");
    await expect(panels).toHaveCount(2);

    expect(
      watcher.errors,
      `想定外 console エラー:\n${watcher.errors.join("\n")}`,
    ).toEqual([]);

    await deleteProject(api.ctx, api.accessToken, project.id);
    watcher.dispose();
  });

  test("[axis7] split された片側 group の最後のタブを閉じると group が collapse する", async ({
    page,
  }) => {
    const watcher = attachConsoleErrorWatcher(page);
    const api = await loginAsAdminApi();
    const project = await createProject(api, {
      name: `close-collapse-${Date.now()}`,
    });

    // 横 split: 左 [Tasks] / 右 [Terminal] を seed
    await seedLayout(api, project.id, {
      kind: "split",
      id: "s-root",
      orientation: "horizontal",
      sizes: [50, 50],
      children: [
        {
          kind: "tabs",
          id: "g-left",
          activeTabId: "p-tasks",
          tabs: [{ id: "p-tasks", paneType: "tasks", paneConfig: {} }],
        },
        {
          kind: "tabs",
          id: "g-right",
          activeTabId: "p-terminal",
          tabs: [{ id: "p-terminal", paneType: "terminal", paneConfig: {} }],
        },
      ],
    });

    await openWorkbench(page, project.id);
    // 初期: 2 group
    await expect(
      page.getByRole("button", { name: "Add tab", exact: true }),
    ).toHaveCount(2);

    // 右 group の Terminal タブの × を押して閉じる
    const terminalTab = tabButtonByTitle(page, "Terminal").first();
    await expect(terminalTab).toBeVisible();
    // Close affordance: same row、aria-label="Close tab"
    const closeOnTerminal = terminalTab.getByRole("button", {
      name: "Close tab",
    });
    await closeOnTerminal.click({ force: true });
    await page.waitForTimeout(400);

    // group は 1 個だけ (右 group が消えて左がフルサイズ)
    await expect(
      page.getByRole("button", { name: "Add tab", exact: true }),
    ).toHaveCount(1);
    // Panel も無くなる (= split が flatten され、TabGroup 直下に戻る)
    await expect(page.locator("[data-panel]")).toHaveCount(0);
    // Tasks は残る
    await expect(tabButtonByTitle(page, "Tasks")).toHaveCount(1);
    await expect(tabButtonByTitle(page, "Terminal")).toHaveCount(0);

    expect(
      watcher.errors,
      `想定外 console エラー:\n${watcher.errors.join("\n")}`,
    ).toEqual([]);

    await deleteProject(api.ctx, api.accessToken, project.id);
    watcher.dispose();
  });
});
