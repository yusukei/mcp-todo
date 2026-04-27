## 概要

<!-- 何を / なぜ。task ID を含める。task:69xxxxxx 形式 -->

## 変更内容

<!-- 具体的な変更点。ファイル単位ではなく機能単位で -->

-
-

## テスト方針

<!-- どう動作確認したか。E2E / unit / manual のいずれを実施したか -->

- [ ] Backend: `docker compose -f backend/docker-compose.test.yml run --rm test`
- [ ] Frontend: `cd frontend && npm test`
- [ ] E2E (該当時): `docker compose -f e2e/docker-compose.e2e.yml --env-file e2e/.env.e2e run --rm --build e2e-runner`
- [ ] Manual:

## "Correctly Working" 8 軸チェック (該当する場合)

CLAUDE.md §"Definition of Correctly Working" に基づく。

- [ ] axis 1 Specified — 仕様書 (`docs/`) に記載済み
- [ ] axis 2 Tested — 仕様の不変条件がテストでカバーされている
- [ ] axis 3 Implemented — 上記テストが pass
- [ ] axis 4 Shipped — staging deploy で本ブランチが配信される予定
- [ ] axis 5 Reachable — UI 入口 (button / route / hotkey) が live UI で見える / 触れる
- [ ] axis 6 Operable — 入口を triggers すると spec の結果が出る
- [ ] axis 7 Persistent — reload / 別デバイス / project 切替で state 維持
- [ ] axis 8 Recoverable — 失敗モードに UI 状態と回復導線がある

## Frontend: useEffect チェックリスト (frontend 変更を含む PR で必須)

新規 / 変更した `useEffect` がある場合、以下を確認してください。詳細はナレッジ `69eedf52aadadfddd2f0e27a` (React useEffect 使用判断ガイド) と CLAUDE.md "Frontend: useEffect は外部システム同期のみ" を参照。

新規 `useEffect` 追加件数: <!-- 0 / N --> 件

各 effect について:

- [ ] **「コンポーネントが画面に出たから」走るべきコードか?** (NO なら handler に移す)
- [ ] **外部システム** (DOM / network / library) と同期しているか? (NO なら計算 or `useMemo`)
- [ ] **cleanup** が必要なリソースを生成していないか? 必要なら return 関数で破棄
- [ ] **StrictMode** で 2 回走っても等価か? (`useRef` ガードで誤魔化していない)
- [ ] **deps array** は exhaustive で、closure stale でないか?
- [ ] **派生値** を state に書き写していないか? (`useMemo` に直す)
- [ ] **prop 変化リセット** を `key` で代替できないか?
- [ ] **effect の連鎖** になっていないか? (1 つの handler にまとめる)
- [ ] **外部 store subscribe** を `useSyncExternalStore` で書けないか?
- [ ] **データフェッチ** は React Query 等のライブラリで代替できないか?

🚫 **禁止パターンを使っていない**:
- [ ] `useEffect(() => { setX(derive(y)) }, [y])` (派生 state)
- [ ] `useEffect(() => { setSearchParams(...) }, [state])` (URL writeback の effect 後追い)
- [ ] `useEffect(() => { localStorage.setItem(...) }, [state])` (永続化の effect 後追い)
- [ ] `useEffect(() => { onChange(value) }, [value])` (親への通知)
- [ ] `useRef` で double-fire を抑止 (`if (!hasInit.current)`)
- [ ] `// eslint-disable-next-line react-hooks/exhaustive-deps`

## Spec / Docs 更新

<!-- docs/ 以下の仕様書を更新したか。task コメントに version を貼ったか -->

- [ ] 該当仕様書を更新済み (`docs/` 配下)
- [ ] update_document の change_summary を task コメントに記載
- [ ] CLAUDE.md / コーディング規約への影響なし、または更新済み

## ロールバック手順

<!-- 問題発生時にどう戻すか。git revert 1 commit で済む / DB migration を伴う 等 -->

## 関連 task / PR

<!-- task:69xxxxxx, PR #N, design doc id 等 -->
