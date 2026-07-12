"""CLI スナップショットの抽出・実行ロジック（テストと再生成スクリプトで共有）。

P2-2（Typer コマンドの SSOT 自動生成）の受け入れ基準は「既存コマンドの CLI 挙動が
回帰しない（ヘルプ文言・終了コード・JSON 出力のスナップショット比較）」（report §4）。
移行前の手書き実装でベースラインを固定し、ファクトリ移行後に byte-identical を検証する。

2 層のスナップショットを持つ:
- surface: click コマンド構造（オプション名/別名/required/default/型/help 文言）。
  rich が描画する --help テキストは端末幅依存のため、描画前の構造を真実源にする。
- behavior: fake client で RPC を差し替えた実行結果（exit code / stdout / stderr /
  送信した (method, params)）。human フォーマッタと presence-sensitive な params 組み立て
  の回帰を検出する。

再生成: `uv run python packages/bli-cli/tests/regen_snapshots.py`
"""

from __future__ import annotations

import itertools
import json
import uuid
from pathlib import Path
from typing import Any

from typer.main import get_command
from typer.testing import CliRunner

import bli.main as main_mod
from bli import client as client_mod

SNAPSHOT_DIR = Path(__file__).parent / "snapshots"
SURFACE_PATH = SNAPSHOT_DIR / "cli_surface.json"
BEHAVIOR_PATH = SNAPSHOT_DIR / "cli_behavior.json"

REGEN_HINT = (
    "意図した変更なら `uv run python packages/bli-cli/tests/regen_snapshots.py` で再生成し、"
    "diff をレビューしてからコミットする"
)

# hello は ping/doctor 以外では出力に影響しない。ping 用に決定的な値を固定する。
DEFAULT_HELLO: dict[str, Any] = {
    "protocol_version": "1.0",
    "blender_version": "5.0.1",
    "schema_hash": "cafebabe" * 8,
    "capabilities": ["print3d"],
}


# ---- surface スナップショット ----


def surface_document(app: Any | None = None) -> dict[str, Any]:
    """全 Typer コマンドの click 構造（描画前のヘルプサーフェス）を抽出する。

    app 省略時は本体の bli.main.app。移行作業では factory 生成のみの app を渡して
    ベースラインとの差分照合にも使う。
    """
    root = get_command(app if app is not None else main_mod.app)
    commands: dict[str, Any] = {}
    for name in sorted(root.commands):  # type: ignore[attr-defined]
        cmd = root.commands[name]  # type: ignore[attr-defined]
        commands[name] = {
            "help": cmd.help,
            "params": [
                {
                    "name": p.name,
                    "opts": list(p.opts),
                    "secondary_opts": list(p.secondary_opts),
                    "required": p.required,
                    # default は False/None/1.0/"mm" 等の混在のため repr で一様化する
                    "default": repr(p.default),
                    "is_flag": bool(getattr(p, "is_flag", False)),
                    "type": repr(p.type),
                    "help": getattr(p, "help", None),
                }
                for p in cmd.params
            ],
        }
    return {"commands": commands}


# ---- behavior スナップショット ----


class Case:
    """behavior ケース 1 件（human/--json の両モードで実行される）。

    responses は fake client.call が FIFO で返す応答列。各要素:
    - {"result": {...}}        … 成功（domain result＝_ok エンベロープ相当）
    - {"error": {...}}         … RpcRemoteError（サーバ業務エラー）
    - {"connect_error": "msg"} … ConnectError（接続不能）
    local_only=True は RPC 到達前に決着するケース（送信前バリデーション等・calls 空を期待）。
    """

    def __init__(
        self,
        id: str,
        argv: list[str],
        responses: list[dict[str, Any]] | None = None,
        *,
        hello: dict[str, Any] | None = None,
        local_only: bool = False,
    ) -> None:
        self.id = id
        self.argv = argv
        self.responses = responses or []
        self.hello = hello or DEFAULT_HELLO
        self.local_only = local_only


_runner = CliRunner()


def run_case(case: Case, *, json_out: bool) -> dict[str, Any]:
    """fake client + 決定的 uuid でケースを 1 回実行し、観測結果を返す。"""
    argv = [*case.argv, "--json"] if json_out else list(case.argv)
    calls: list[dict[str, Any]] = []
    queue = [dict(r) for r in case.responses]

    def fake_call(
        method: str,
        params: dict[str, Any] | None = None,
        *,
        port: int | None = None,
        request_id: str | None = None,
        timeout: float | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        calls.append({"method": method, "params": params})
        if not queue:
            raise AssertionError(f"想定外の追加 RPC: {method}（responses が不足）")
        entry = queue.pop(0)
        if "connect_error" in entry:
            raise client_mod.ConnectError(entry["connect_error"])
        if "error" in entry:
            raise client_mod.RpcRemoteError(entry["error"])
        return entry["result"], case.hello

    # request_id は CLI 側で uuid4 生成され stdout/stderr に載る。決定的な連番 UUID に固定する。
    counter = itertools.count(1)

    def fake_uuid4() -> uuid.UUID:
        return uuid.UUID(int=next(counter), version=4)

    orig_call = client_mod.call
    orig_uuid4 = uuid.uuid4
    try:
        client_mod.call = fake_call  # type: ignore[assignment]
        uuid.uuid4 = fake_uuid4  # type: ignore[assignment]
        res = _runner.invoke(main_mod.app, argv)
    finally:
        client_mod.call = orig_call  # type: ignore[assignment]
        uuid.uuid4 = orig_uuid4  # type: ignore[assignment]

    if res.exception is not None and not isinstance(res.exception, SystemExit):
        raise res.exception

    if case.local_only and calls:
        raise AssertionError(f"local_only ケースが RPC を送信した: {case.id} -> {calls}")
    if not case.local_only and queue:
        raise AssertionError(f"未消費の responses が残った: {case.id}（{len(queue)} 件）")

    return {
        "exit_code": res.exit_code,
        "stdout": res.stdout,
        "stderr": res.stderr,
        "calls": calls,
    }


def behavior_document(cases: list[Case]) -> dict[str, Any]:
    """全ケース × human/json モードの観測結果を辞書化する。"""
    seen: set[str] = set()
    doc: dict[str, Any] = {}
    for case in cases:
        if case.id in seen:
            raise ValueError(f"ケース id が重複: {case.id}")
        seen.add(case.id)
        doc[f"{case.id}|human"] = run_case(case, json_out=False)
        doc[f"{case.id}|json"] = run_case(case, json_out=True)
    return doc


def load_snapshot(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def dump_snapshot(path: Path, doc: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
