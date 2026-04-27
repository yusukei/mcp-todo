import {
  test,
  expect,
  attachConsoleErrorWatcher,
  loginViaUi,
} from "../../fixtures/auth";
import { loginAsAdminApi, createProject } from "../../fixtures/api";

/**
 * Feature: + (Add tab) ボタンで pane type を選んでタブを追加する.
 *
 * 軸:
 *   - axis 5 Reachable:  + ボタンが見える、押すと type 選択メニューが出る
 *   - axis 6 Operable:   "Terminal" を選ぶと Terminal タブが新規追加される
 *   - axis 7 Persistent: 1.5s 後も新タブが残る (= 一瞬だけ生成→即閉じ ではない)
 *
 * 関連タスク: 69edb607
 *
 * 注: Terminal pane の中身 (TerminalSessionList) は agent process が無い E2E スタックで
 *      "Failed to load sessions" を出すが、それは axis 8 fallback として正しい。
 *      本 spec は **タブ追加そのもの** が動くことを保証する。
 */
test("[axis5][axis6] + メニューで Terminal タブを追加できる", async ({
  page,
}) => {
  const watcher = attachConsoleErrorWatcher(page);

  const api = await loginAsAdminApi();
  const unique = Date.now();
  const project = await createProject(api, { name: `addtab-${unique}` });

  await loginViaUi(page);
  await page
    .locator(`a[href="/projects/${project.id}"]`)
    .first()
    .click();
  await page.waitForURL(new RegExp(`/projects/${project.id}(?:[/?].*)?$`), {
    timeout: 10_000,
  });

  // 既定 layout は "Tasks" タブ 1 枚で開始
  await expect(
    page.getByRole("button", { name: "Tasks" }).first(),
  ).toBeVisible({ timeout: 10_000 });

  // axis 5: "+ (Add tab)" ボタン
  const addButton = page.getByRole("button", { name: "Add tab", exact: true });
  await expect(addButton).toBeVisible({ timeout: 5_000 });

  await addButton.click();
  const picker = page.getByRole("menu", { name: "Add tab type" });
  await expect(picker).toBeVisible({ timeout: 5_000 });

  for (const label of [
    "Tasks",
    "Task Detail",
    "Terminal",
    "Doc",
    "Documents",
    "Files",
    "Errors",
  ]) {
    await expect(
      picker.getByRole("menuitem", { name: label, exact: true }),
    ).toBeVisible({ timeout: 2_000 });
  }

  // axis 6: Terminal を選ぶ
  await picker.getByRole("menuitem", { name: "Terminal", exact: true }).click();

  // 直後: Terminal タブが strip に現れる
  const terminalTab = page.getByRole("button", { name: "Terminal" });
  await expect(terminalTab.first()).toBeVisible({ timeout: 5_000 });

  // axis 7: 1.5s 待ってもまだ存在する (即閉じていないこと)
  await page.waitForTimeout(1500);
  await expect(
    page.getByRole("button", { name: "Terminal" }).first(),
    "Terminal タブが 1.5s 後に消えている (一瞬だけ生成されて即閉じる現象)",
  ).toBeVisible({ timeout: 1_000 });

  // タブ数: Tasks + Terminal の 2 つ。タブ strip の `<button title="...">` を全部数える。
  // 各 DraggableTab は title=PANE_TYPE_LABELS[type] なので、その属性で絞る。
  const tabButtons = page.locator('[title="Tasks"], [title="Terminal"]');
  await expect(tabButtons).toHaveCount(2, { timeout: 2_000 });

  // axis 7: reload で残る (paneConfig 永続化)
  await page.reload();
  await expect(
    page.getByRole("button", { name: "Terminal" }).first(),
    "reload 後 Terminal タブが消えている",
  ).toBeVisible({ timeout: 10_000 });

  // メニューは閉じている
  await expect(
    page.getByRole("menu", { name: "Add tab type" }),
  ).not.toBeVisible();

  // (旧) "⋮ Pane menu に Change type が無いこと" を検証していたが、
  // P3-5 (TabGroup.tsx §237-245) で ⋮ Pane menu 自体が撤去された.
  // 今や タブ追加経路は + メニューに集約されているため、確認対象
  // (Pane menu) が DOM に存在しない = 上の "Add tab type メニュー
  // が閉じている" の検証で十分.

  expect(
    watcher.errors,
    `想定外の console エラー:\n${watcher.errors.join("\n")}`,
  ).toEqual([]);

  await api.ctx.delete(`/api/v1/projects/${project.id}`, {
    headers: { Authorization: `Bearer ${api.accessToken}` },
  });
  watcher.dispose();
});
