import {
  test,
  expect,
  attachConsoleErrorWatcher,
} from "../../fixtures/auth";
import { loginAsAdminApi, createProject } from "../../fixtures/api";
import {
  deleteProject,
  makePane,
  makeTabsNode,
  openWorkbench,
  seedLayout,
  tabButtonByTitle,
} from "../../fixtures/workbench";

/**
 * Layout reset (Cmd/Ctrl+Shift+R hotkey) と preset 適用ダイアログ.
 *
 * UI menu からの load preset は P3-5 で撤去された (TabGroup §237-245).
 * 残っているのは:
 *   - Cmd/Ctrl+Shift+R hotkey (matchHotkey 'reset-layout')
 *   - WorkbenchPage.applyPreset (URL ?layout= とテストで叩ける)
 *
 * 本 spec は **hotkey → 確認モーダル → Replace layout → tasks-only**
 * の path を固定する.
 *
 * Phase 2 reducer 移行で破綻すると：
 *   - hotkey listener が新 store dispatcher を購読できない
 *   - applyPreset → updateTree → reducer.replace の経路が壊れる
 */

test("[refactor-p2-pre][axis6] Cmd+Shift+R で layout を tasks-only に reset できる", async ({
  page,
}) => {
  const watcher = attachConsoleErrorWatcher(page);
  const api = await loginAsAdminApi();
  const project = await createProject(api, {
    name: `reset-${Date.now()}`,
  });

  // 複雑な layout (Tasks + Terminal + Doc) を seed
  await seedLayout(
    api,
    project.id,
    makeTabsNode([makePane("tasks"), makePane("terminal"), makePane("doc")]),
  );

  await openWorkbench(page, project.id);
  await expect(tabButtonByTitle(page, "Tasks").first()).toBeVisible();
  await expect(tabButtonByTitle(page, "Terminal").first()).toBeVisible();
  await expect(tabButtonByTitle(page, "Doc").first()).toBeVisible();

  // hotkey 発火 (Linux/Win 上の Playwright なので Control を使う;
  // matchHotkey は metaKey || ctrlKey を受け入れる)
  await page.keyboard.press("Control+Shift+R");

  // 確認モーダルが出る
  const confirmHeading = page.getByRole("heading", {
    name: "Replace current layout?",
  });
  await expect(confirmHeading).toBeVisible({ timeout: 3_000 });
  await expect(page.getByText("Tasks only", { exact: false })).toBeVisible();

  // 「Replace layout」ボタンで確定
  await page.getByRole("button", { name: "Replace layout" }).click();
  await page.waitForTimeout(400);

  // 結果: Tasks 1 タブのみ
  await expect(tabButtonByTitle(page, "Tasks")).toHaveCount(1);
  await expect(tabButtonByTitle(page, "Terminal")).toHaveCount(0);
  await expect(tabButtonByTitle(page, "Doc")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Add tab", exact: true }),
  ).toHaveCount(1);

  expect(
    watcher.errors,
    `想定外 console エラー:\n${watcher.errors.join("\n")}`,
  ).toEqual([]);

  await deleteProject(api.ctx, api.accessToken, project.id);
  watcher.dispose();
});

test("[refactor-p2-pre][axis6] reset 確認モーダルは Cancel で閉じられ layout は変わらない", async ({
  page,
}) => {
  const watcher = attachConsoleErrorWatcher(page);
  const api = await loginAsAdminApi();
  const project = await createProject(api, {
    name: `reset-cancel-${Date.now()}`,
  });

  await seedLayout(
    api,
    project.id,
    makeTabsNode([makePane("tasks"), makePane("terminal")]),
  );

  await openWorkbench(page, project.id);
  await expect(tabButtonByTitle(page, "Terminal").first()).toBeVisible();

  await page.keyboard.press("Control+Shift+R");
  await expect(
    page.getByRole("heading", { name: "Replace current layout?" }),
  ).toBeVisible();

  await page.getByRole("button", { name: "Cancel" }).click();
  await expect(
    page.getByRole("heading", { name: "Replace current layout?" }),
  ).not.toBeVisible();

  // layout は変わっていない (Tasks + Terminal が残る)
  await expect(tabButtonByTitle(page, "Tasks")).toHaveCount(1);
  await expect(tabButtonByTitle(page, "Terminal")).toHaveCount(1);

  expect(
    watcher.errors,
    `想定外 console エラー:\n${watcher.errors.join("\n")}`,
  ).toEqual([]);

  await deleteProject(api.ctx, api.accessToken, project.id);
  watcher.dispose();
});

test("[refactor-p2-pre][axis5] ?layout=tasks-with-detail で preset がマウント時に適用される", async ({
  page,
}) => {
  const watcher = attachConsoleErrorWatcher(page);
  const api = await loginAsAdminApi();
  const project = await createProject(api, {
    name: `preset-url-${Date.now()}`,
  });

  // ?layout=tasks-with-detail = horizontal split: [Tasks] / [Task Detail]
  await openWorkbench(page, project.id, { query: "layout=tasks-with-detail" });

  // 2 group 構成
  await expect(
    page.getByRole("button", { name: "Add tab", exact: true }),
  ).toHaveCount(2, { timeout: 5_000 });
  await expect(tabButtonByTitle(page, "Tasks")).toHaveCount(1);
  await expect(tabButtonByTitle(page, "Task Detail")).toHaveCount(1);
  // 2 Panel (= horizontal split が成立)
  await expect(page.locator("[data-panel]")).toHaveCount(2);

  expect(
    watcher.errors,
    `想定外 console エラー:\n${watcher.errors.join("\n")}`,
  ).toEqual([]);

  await deleteProject(api.ctx, api.accessToken, project.id);
  watcher.dispose();
});
