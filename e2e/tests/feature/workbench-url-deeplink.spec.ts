import {
  test,
  expect,
  attachConsoleErrorWatcher,
} from "../../fixtures/auth";
import {
  loginAsAdminApi,
  createProject,
  createTask,
} from "../../fixtures/api";
import {
  createDocument,
  deleteProject,
  makePane,
  makeTabsNode,
  openWorkbench,
  seedLayout,
  tabButtonByTitle,
} from "../../fixtures/workbench";

/**
 * URL deeplink (?task= / ?doc=) — Phase C2 D1/D3 仕様の最重要 path.
 *
 * Phase 2 reducer 移行で破綻すると：
 *   - URL → state hydrate が二重発火 → state ループ
 *   - findFirstPaneOfType の「first」順序が変わる
 *   - slide-over fallback が出っぱなしになる / 出ない
 * いずれも本 spec で固定する.
 *
 * 仕様書: docs/api/url-contract.md, urlContract.ts
 */

test("[refactor-p2-pre][axis5] ?layout=tasks-with-detail&task=<id> で TaskDetailPane に該当 task が表示される", async ({
  page,
}) => {
  const watcher = attachConsoleErrorWatcher(page);
  const api = await loginAsAdminApi();
  const project = await createProject(api, {
    name: `deep-task-${Date.now()}`,
  });
  const task = await createTask(api, project.id, {
    title: `URL deeplink target ${Date.now()}`,
    description: "ブラウザ URL から直接開いて表示できる task",
    priority: "high",
  });

  await openWorkbench(page, project.id, {
    query: `layout=tasks-with-detail&task=${task.id}`,
  });

  // TaskDetail の <h2> がタイトルを表示する (TaskDetail.tsx:513)
  await expect(
    page.getByRole("heading", { level: 2, name: task.title }),
  ).toBeVisible({ timeout: 10_000 });
  // EmptyState (タスクを選択してください) は出ていない
  await expect(page.getByText("タスクを選択してください")).not.toBeVisible();

  expect(
    watcher.errors,
    `想定外 console エラー:\n${watcher.errors.join("\n")}`,
  ).toEqual([]);

  await deleteProject(api.ctx, api.accessToken, project.id);
  watcher.dispose();
});

test("[refactor-p2-pre][axis8] ?task=<id> で task-detail pane が無い時は slide-over fallback が出る", async ({
  page,
}) => {
  const watcher = attachConsoleErrorWatcher(page);
  const api = await loginAsAdminApi();
  const project = await createProject(api, {
    name: `deep-task-fb-${Date.now()}`,
  });
  const task = await createTask(api, project.id, {
    title: `Slide-over fallback target ${Date.now()}`,
    description: "TaskDetailPane が無い layout 時の fallback 検証",
  });

  // 既定 layout = Tasks 1 枚 (= task-detail pane 無し).
  // → slide-over fallback path に入る (Decision D1).
  await openWorkbench(page, project.id, { query: `task=${task.id}` });

  // slide-over (TaskDetail displayMode='slideOver') は role="dialog"
  // + aria-label=task.title (TaskDetail.tsx:486)
  await expect(
    page.getByRole("dialog", { name: task.title }),
  ).toBeVisible({ timeout: 10_000 });

  expect(
    watcher.errors,
    `想定外 console エラー:\n${watcher.errors.join("\n")}`,
  ).toEqual([]);

  await deleteProject(api.ctx, api.accessToken, project.id);
  watcher.dispose();
});

test("[refactor-p2-pre][axis5] ?doc=<id> で DocPane に該当 doc が表示される", async ({
  page,
}) => {
  const watcher = attachConsoleErrorWatcher(page);
  const api = await loginAsAdminApi();
  const project = await createProject(api, {
    name: `deep-doc-${Date.now()}`,
  });
  const docTitle = `Deeplink Doc ${Date.now()}`;
  const doc = await createDocument(api, project.id, {
    title: docTitle,
    content: "# Deeplink Doc\n\n本文.\n",
    category: "design",
  });

  // 単独 Doc pane のみの layout を seed (FileBrowser は外す ─ E2E 環境
  // では remote agent が未バインドで `/filebrowser` が 409 を返し、
  // 本テストの趣旨と無関係な console.error を生むため).
  const docPane = makePane("doc");
  await seedLayout(api, project.id, makeTabsNode([docPane]));

  await openWorkbench(page, project.id, {
    query: `doc=${doc.id}`,
  });

  // Doc tab が 1 個
  await expect(tabButtonByTitle(page, "Doc")).toHaveCount(1);

  // DocPane の header に title が描画される (DocPane.tsx:157)
  await expect(page.getByText(docTitle).first()).toBeVisible({
    timeout: 10_000,
  });

  expect(
    watcher.errors,
    `想定外 console エラー:\n${watcher.errors.join("\n")}`,
  ).toEqual([]);

  await deleteProject(api.ctx, api.accessToken, project.id);
  watcher.dispose();
});
