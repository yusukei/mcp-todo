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
  openSecondWorkbenchTab,
  openWorkbench,
  seedLayout,
  tabButtonByTitle,
  writeLayoutToLocalStorage,
} from "../../fixtures/workbench";

/**
 * Cross-tab layout sync — `subscribeCrossTab` (storage event) 経由.
 *
 * Phase 2 reducer 移行で破綻すると：
 *   - 別タブで保存した layout を adopt しなくなる (= 仕様 INV-22)
 *   - 自タブが書いた layout を自分で adopt して echo loop に入る
 * いずれも本 spec で固定する.
 *
 * 仕様: storage.ts §subscribeCrossTab, WorkbenchPage §314-323.
 *
 * 実装メモ:
 *   - 同一 BrowserContext で 2 page を開く: localStorage は origin 共有、
 *     `storage` event は **書き込み元タブには飛ばず** 別タブにのみ届く
 *     (browser 仕様). これは production の "別 browser tab で開いて
 *     編集" シナリオを忠実に再現する.
 *   - cross-tab 通知は localStorage 書き込みベースなので、書き込み側
 *     page から `evaluate()` で setItem するだけで再現可能.
 */

test("[refactor-p2-pre][axis7] 別タブで保存された layout が現在タブに反映される", async ({
  page,
  context,
}) => {
  const watcher1 = attachConsoleErrorWatcher(page);
  const api = await loginAsAdminApi();
  const project = await createProject(api, {
    name: `crosstab-${Date.now()}`,
  });

  // 初期 layout = Tasks のみ
  const tasks = makePane("tasks");
  await seedLayout(api, project.id, makeTabsNode([tasks]));

  // page1 (このテスト) を開く
  await openWorkbench(page, project.id);
  await expect(tabButtonByTitle(page, "Tasks").first()).toBeVisible();
  await expect(tabButtonByTitle(page, "Terminal")).toHaveCount(0);

  // page2 を同 context に開く (= cookie 共有)
  const page2 = await openSecondWorkbenchTab(context, project.id);
  const watcher2 = attachConsoleErrorWatcher(page2);
  await expect(tabButtonByTitle(page2, "Tasks").first()).toBeVisible();

  // page2 から localStorage を「Tasks + Terminal」で上書き.
  // これにより storage event が page1 に届き、subscribeCrossTab が
  // dispatch('replace') する.
  await writeLayoutToLocalStorage(
    page2,
    project.id,
    {
      kind: "tabs",
      id: "g-cross",
      activeTabId: "p-tasks-cross",
      tabs: [
        { id: "p-tasks-cross", paneType: "tasks", paneConfig: {} },
        { id: "p-terminal-cross", paneType: "terminal", paneConfig: {} },
      ],
    },
    Date.now() + 5_000, // 確実に localStampRef より新しい
  );

  // page1: Terminal タブが現れる (cross-tab adopt 成功)
  await expect(tabButtonByTitle(page, "Terminal")).toHaveCount(1, {
    timeout: 5_000,
  });
  await expect(tabButtonByTitle(page, "Tasks")).toHaveCount(1);

  // page2 自身は writer なので storage event を受け取らない: page2 の
  // 表示は localStorage 書き込み**前**の Tasks のみのまま (mount 時
  // hydrate のスナップショットを保持).
  // ただし WorkbenchPage の hydrate は projectId 依存なので reload
  // しない限り page2 自身には反映されない.
  await expect(tabButtonByTitle(page2, "Terminal")).toHaveCount(0);

  expect(
    watcher1.errors,
    `想定外 console エラー (page1):\n${watcher1.errors.join("\n")}`,
  ).toEqual([]);
  expect(
    watcher2.errors,
    `想定外 console エラー (page2):\n${watcher2.errors.join("\n")}`,
  ).toEqual([]);

  await deleteProject(api.ctx, api.accessToken, project.id);
  watcher1.dispose();
  watcher2.dispose();
  await page2.close();
});

test("[refactor-p2-pre][axis7] 古い stamp の cross-tab 書き込みは ignored", async ({
  page,
  context,
}) => {
  const watcher1 = attachConsoleErrorWatcher(page);
  const api = await loginAsAdminApi();
  const project = await createProject(api, {
    name: `crosstab-stale-${Date.now()}`,
  });

  // 初期 layout = Tasks + Terminal
  await seedLayout(
    api,
    project.id,
    makeTabsNode([makePane("tasks"), makePane("terminal")]),
  );

  await openWorkbench(page, project.id);
  await expect(tabButtonByTitle(page, "Terminal")).toHaveCount(1);

  // page1 で localStorage に「Tasks のみ」 + 古い stamp を書き、
  // 続けて localStampRef を進めるためにユーザ操作 (close Terminal)
  // をしてから古い stamp で書き戻されても adopt されないことを確認.
  // — シンプル化のため、ここでは古い stamp で別タブが書いた場合に
  // 反映されない経路だけを確認する.

  const page2 = await openSecondWorkbenchTab(context, project.id);
  const watcher2 = attachConsoleErrorWatcher(page2);

  // ユーザが page1 で Terminal を閉じる (= localStamp が現時点に進む)
  const terminalTab = tabButtonByTitle(page, "Terminal").first();
  const closeBtn = terminalTab.getByRole("button", { name: "Close tab" });
  await closeBtn.click({ force: true });
  await page.waitForTimeout(400);
  await expect(tabButtonByTitle(page, "Terminal")).toHaveCount(0);

  // page2 から **古い** stamp で「Tasks + Terminal + Doc」を書き込み
  await writeLayoutToLocalStorage(
    page2,
    project.id,
    {
      kind: "tabs",
      id: "g-stale",
      activeTabId: "p-stale-tasks",
      tabs: [
        { id: "p-stale-tasks", paneType: "tasks", paneConfig: {} },
        { id: "p-stale-terminal", paneType: "terminal", paneConfig: {} },
        { id: "p-stale-doc", paneType: "doc", paneConfig: {} },
      ],
    },
    1, // 1ms — 確実に localStampRef より古い
  );

  // page1 は古い stamp を ignore する → Terminal も Doc も現れない
  await page.waitForTimeout(800);
  await expect(tabButtonByTitle(page, "Terminal")).toHaveCount(0);
  await expect(tabButtonByTitle(page, "Doc")).toHaveCount(0);
  await expect(tabButtonByTitle(page, "Tasks")).toHaveCount(1);

  expect(
    watcher1.errors,
    `想定外 console エラー (page1):\n${watcher1.errors.join("\n")}`,
  ).toEqual([]);
  expect(
    watcher2.errors,
    `想定外 console エラー (page2):\n${watcher2.errors.join("\n")}`,
  ).toEqual([]);

  await deleteProject(api.ctx, api.accessToken, project.id);
  watcher1.dispose();
  watcher2.dispose();
  await page2.close();
});
