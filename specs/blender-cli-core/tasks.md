# bli — タスク台帳 (tasks.md)

スコープ: **M0 → M2 の縦切り（walking skeleton）**。完了条件 = CLI→HELLO→ping(echo) のE2E疎通。
出典: `plan.md` §4。チケットは `tasks/`。`[x]`=完了 / `[ ]`=未完。

## M0: プロジェクト基盤 ✅
- [x] [01](tasks/01-git-and-workspace.md) git init + uv workspace ルート（依存: なし）
- [x] [02](tasks/02-package-scaffolding.md) packages/{bli-core,bli-cli,bli-addon} 雛形（依存: 01）
- [x] [03](tasks/03-tooling.md) ruff/pyright/pre-commit（依存: 02）
- [x] [04](tasks/04-ast-guard.md) 生bpy.ops禁止 AST lint + ユニット4件（依存: 02）
- [x] [05](tasks/05-ci-skeleton.md) CIスケルトン lint+L1枠（依存: 03,04）

## M0.5: Phase0 実機スパイク ✅
- [x] [06](tasks/06-capability-dump.md) bl_rna/operator ダンプ（5.0/4.4実機）→ research.md 付録A（print3dモジュールid特定はM8へ繰越）
- [x] [07](tasks/07-dispatch-poc.md) timer+queue+Event.wait 安定性PoC → 両版STABLE（付録C）
- [x] [08](tasks/08-operator-wrapper-spike.md) run_operator/temp_override/undo_push 実機確認 → 付録B

## M1: コア（bli-core / SSOT）✅
- [x] [09](tasks/09-command-ssot.md) Command/Param dataclass + @command 登録（依存: 02）
- [x] [10](tasks/10-schema-gen.md) to_json_schema/validate/schema_hash（依存: 09）
- [x] [11](tasks/11-errors.md) ErrorObject + エラー/終了コード（依存: 02）
- [x] [12](tasks/12-protocol-codec.md) framing + JSON-RPC codec + HELLO（依存: 11）

## M2: 通信層 ✅
- [x] [13](tasks/13-addon-server-skeleton.md) listen/recvスレッド/select（依存: 12）
- [x] [14](tasks/14-auth-handshake.md) HELLO + token認証（依存: 13）
- [x] [15](tasks/15-session-registry.md) session_lock + RequestRegistry冪等（依存: 13）
- [x] [16](tasks/16-shutdown.md) shutdown手順 + フック（依存: 13）
- [x] [17](tasks/17-cli-client.md) CLIクライアント + connection.json + init/ping（依存: 12）
- [x] [18](tasks/18-e2e-ping.md) echoハンドラ + E2E ping疎通（依存: 14,15,16,17）

## M3: アドオン実行基盤 ✅
- [x] Dispatcher（submit/pump/install_timer/remove_timer・TimeoutPending）。bpy 依存は install_timer のみ。
- [x] CapabilityRegistry / operator_real（get_rna_type 判定）/ RESOLVERS（M0.5 確定値）。
- [x] BpyGateway: run_operator（temp_override/poll/FINISHED/undo_push）/ resolve_targets / require_single / object_summary / scene_summary / origin_set / set_origin_world（直接行列）/ make_single_user_mesh / current_mode / object_fingerprint。**bpy.ops は gateway.py のみ**（AST guard 強制）。
- [x] ops.py: ドメインハンドラ（scene-info / object-info / set-origin）+ dispatch ルータ（bpy系→ハンドラ / その他→handlers.dispatch）。param検証(INVALID_PARAMS)・required_mode検証(E_MODE_MISMATCH)・共有mesh(E_PRECONDITION)。
- [x] definitions.py に object-info 追加。
- [x] __init__.py register() 結線: Dispatcher→install_timer→server.start(handler=submit(ops.dispatch))。unregister で stop+remove_timer。
- [x] CLI サブコマンド scene-info / object-info / set-origin（終了コード写像: USER_INPUT→4 / business→1）。
- [x] ops ユニットテスト 7件（ルーティング + param検証、bpy不要）。
- [x] 実機スモーク smoke_ops.py（メインスレッド手動pump + 別スレッドclient）。set-origin world→geometry の golden 検証。

## 進捗メモ
- 着手日: 2026-06-13 / ブランチ: feature/m0-bootstrap
- M0→M2 完了（2026-06-13）。L3 E2E 38件 pass + Blender 5.0.1 実機スモーク OK。
- walking skeleton 達成: CLI→HELLO→ping/echo 疎通（dev 3.10 / Blender 3.11 両対応実証）。
- M3 完了（2026-06-13）。pytest **45件** pass + ruff/format/AST guard 緑。Blender **5.0.1 / 4.4.3** 両実機で smoke_ops OK（fingerprint 一致 = 決定性確認）。
