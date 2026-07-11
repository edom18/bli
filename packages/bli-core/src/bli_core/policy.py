"""exec-python のポリシー読取/書込（M11 T11.1・R-A / P1-1 で bli-core へ移設）。spec §276 / §284 / §459。

exec の mode（off|restricted|audited|trusted）の **真実源はユーザローカル policy.toml**
（`BLI_STATE_DIR/policy.toml`・OS 所有者限定・git 非管理）。サーバ（アドオン）だけがこれを読んで
実行可否を決める。CLI の `bli policy` ヘルパも表示/編集に同じロジックを使うため、
読取に加えて **ファイル形式の知識（許容スキーマ・レンダラ・原子的書込）もこのモジュールに集約**する
（読取と書込が別パッケージに分裂すると、policy.toml へキーを足すたび 2 箇所の手動同期が要る。
レビュー R1-3）。`bli_addon.policy` は互換再エクスポート。書込を使うのは CLI だけ（サーバは読むのみ）。

**ここが M11 の肝**: CLI が送る mode は無視する＝CLI フラグ単体では昇格できない。リポジトリ内の
`.bli/config.toml` に `mode = "trusted"` を commit しても昇格しない（サーバは config.toml を読まない）。
昇格はユーザが自分の OS アカウントの policy.toml を編集したときだけ成立する（spec §276）。

fail-closed: ファイル不在・パース失敗・不正な mode 値はすべて "off"（無効）へ丸める。

bpy 非依存。`tomllib` は 3.11+ 標準のため **bli-core の 3.10 互換維持（plan.md「core で 3.11+
機能を使わない」）とは条件付き import で両立**する: 3.10 では読取が fail-closed（off）へ、
書込ヘルパが UnsafePolicyError の明示拒否へ縮退する（レビュー R2-1。実運用の読取側は
Blender 3.11+ / uv 3.12 なので縮退は発火しない）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10（requires-python >=3.10 の宣言範囲・R2-1）
    tomllib = None  # type: ignore[assignment]

from . import runtime

POLICY_FILENAME = "policy.toml"
# restricted は P1-1（設計レビュー 2026-07-11 G0）で追加: AST ブロックリスト検査つき自走
# （Blender API は全面許可・プロセス/ネットワーク/削除系等を検出したら EXEC_BLOCKED_RESTRICTED）。
VALID_MODES = ("off", "restricted", "audited", "trusted")
DEFAULT_MODE = "off"

# 自動書き換え（`bli policy --action set`）を許す既存 policy.toml の形。read_* が消費するキーの
# 鏡写し＝ここに無いキーを持つファイルは「他の設定を静かに失わない」ため書込前に拒否する。
# 新キーを足すときは read_* とこの集合と render_policy_toml を **このモジュール内で** 揃える。
ALLOWED_TOP_KEYS = frozenset({"exec"})
ALLOWED_EXEC_KEYS = frozenset({"mode", "allow_hashes"})


def policy_path() -> Path:
    """ユーザローカル policy.toml のパス（`BLI_STATE_DIR/policy.toml`）。"""
    return runtime.user_state_dir() / POLICY_FILENAME


def _load_policy() -> dict[str, Any]:
    """policy.toml を辞書で返す。不在/パース失敗/tomllib 不在(3.10)は空 dict（fail-closed の起点）。"""
    if tomllib is None:  # Python 3.10: 解析不能＝昇格方向に倒さず off へ（R2-1）
        return {}
    path = policy_path()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, UnicodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def read_exec_mode() -> str:
    """policy.toml の `[exec] mode` を返す。不在/不正は "off"（fail-closed・R-A）。

    CLI が送るどんな値とも独立＝この関数だけが exec の可否を決める真実源。
    """
    exec_section = _load_policy().get("exec")
    if isinstance(exec_section, dict):
        mode = exec_section.get("mode")
        if mode in VALID_MODES:
            return str(mode)
    return DEFAULT_MODE


def read_allow_hashes() -> frozenset[str]:
    """policy.toml の `[exec] allow_hashes`（許可コードの sha256・小文字16進）を返す（M11 T11.3・R-B）。

    audited モードはここに一致する sha256 のコードだけ自走実行する。不在/不正は空集合（fail-closed）。
    要素は小文字に正規化し、文字列でないものは無視する。
    """
    exec_section = _load_policy().get("exec")
    if not isinstance(exec_section, dict):
        return frozenset()
    raw = exec_section.get("allow_hashes")
    if not isinstance(raw, list):
        return frozenset()
    # コピペ事故（前後空白・大文字）で沈黙して自走しない事態を減らすため strip + lower で正規化する。
    return frozenset(h.strip().lower() for h in raw if isinstance(h, str))


# ---- 書込側（`bli policy --action set` 用・サーバは使わない / P1-1・レビュー R1-3 で集約）----


class UnsafePolicyError(Exception):
    """既存 policy.toml が想定外の形（自動編集で他設定を失いかねない）。手動編集を促す。"""


def load_preserved_allow_hashes() -> list[str]:
    """set 時に保持すべき allow_hashes を返す（**順序・表記を保持**・正規化しない）。

    read_allow_hashes は照合用に小文字化・集合化するが、こちらは既存ファイルの見た目を
    変えずに書き戻すための生値。既存ファイルが「ALLOWED_* のキーしか無い」形でなければ
    UnsafePolicyError（policy.toml に手書きされた他の設定を静かに失わないため）。
    """
    if tomllib is None:  # Python 3.10: 既存内容を検査できない＝沈黙破壊を避け明示拒否（R2-1）
        raise UnsafePolicyError(
            "この Python には tomllib がありません（policy.toml の自動編集には Python 3.11+ が必要）"
        )
    path = policy_path()
    if not path.exists():
        return []
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as e:
        raise UnsafePolicyError(f"既存の policy.toml を解析できません: {e}") from e
    if not isinstance(data, dict):
        raise UnsafePolicyError("既存の policy.toml のトップレベルが table ではありません")
    extra_top = set(data) - ALLOWED_TOP_KEYS
    if extra_top:
        raise UnsafePolicyError(
            f"既存の policy.toml に [exec] 以外のセクションがあります: {sorted(extra_top)}"
        )
    exec_section = data.get("exec", {})
    if not isinstance(exec_section, dict):
        raise UnsafePolicyError("既存の policy.toml の [exec] が table ではありません")
    extra_exec = set(exec_section) - ALLOWED_EXEC_KEYS
    if extra_exec:
        raise UnsafePolicyError(
            f"既存の [exec] に mode/allow_hashes 以外のキーがあります: {sorted(extra_exec)}"
        )
    hashes = exec_section.get("allow_hashes", [])
    if not isinstance(hashes, list) or not all(isinstance(h, str) for h in hashes):
        raise UnsafePolicyError("既存の [exec] allow_hashes が文字列の配列ではありません")
    return list(hashes)


def render_policy_toml(mode: str, allow_hashes: list[str]) -> str:
    """policy.toml の内容を決定的に組み立てる（[exec] mode + 既存 allow_hashes を保持）。"""
    if mode not in VALID_MODES:  # 呼び出し側の検証漏れでも不正モードを書かない（fail-closed 対）
        raise ValueError(f"不正な mode: {mode!r}（{'|'.join(VALID_MODES)}）")
    lines = [
        "# bli exec 実行ポリシー（ユーザローカル・git 非管理）。",
        "# exec-python の mode はサーバ（Blender アドオン）がこのファイルだけを読む真実源。",
        "# .bli/config.toml の [exec] mode は表示ヒントに過ぎず、ここを書き換えないと昇格しない。",
        "# `bli policy --action set --mode <mode>` で書き換えられる（直接編集しても良い）。",
        "",
        "[exec]",
        f'mode = "{mode}"          # off | restricted | audited | trusted',
    ]
    if allow_hashes:
        items = ", ".join(json.dumps(h) for h in allow_hashes)
        lines.append(f"allow_hashes = [{items}]  # audited 用の許可 sha256（保持）")
    lines.append("")
    return "\n".join(lines)


def write_policy(mode: str, allow_hashes: list[str]) -> Path:
    """policy.toml を所有者限定権限で原子的に書き込む（token 書込と同じ流儀）。書込先を返す。"""
    path = policy_path()
    text = render_policy_toml(mode, allow_hashes)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)  # posix で所有者限定。Windows は限定的（既知・トークンと同じ）
    except OSError:
        pass
    os.replace(tmp, path)
    return path
