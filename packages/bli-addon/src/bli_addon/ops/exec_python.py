"""exec-python ハンドラ（逃げ道・既定 off・サンドボックスなし・ops/ 分割 P2-4）。

元 ops.py の該当セクションをそのまま移設（挙動変更なし）。
"""

from __future__ import annotations

from typing import Any

from bli_core.errors import RPC_BUSINESS_ERROR, ErrorCategory, ErrorCode, make_error
from bli_core.protocol import JsonRpcError

from ..handlers import ServerInfo
from ._shared import _check_mode, _command, _ok_offload, _require_input, _validate


def _exec_error(message: str, *, phase: str, cause: str = "") -> JsonRpcError:
    """ユーザコードの例外を EXEC_ERROR へ写像する（INTERNAL 化しない・研究 §E14）。

    compile フェーズ（SyntaxError 等）はユーザコードの不備＝USER_INPUT、runtime は ENVIRONMENT。
    """
    category = ErrorCategory.USER_INPUT if phase == "compile" else ErrorCategory.ENVIRONMENT
    return JsonRpcError(
        RPC_BUSINESS_ERROR,
        ErrorCode.EXEC_ERROR,
        make_error(
            ErrorCode.EXEC_ERROR,
            category=category,
            retryable=False,
            symptom=message,
            remediation="コードを修正して再実行してください",
            cause=cause,
        ),
    )


def _exec_disabled(symptom: str, remediation: str) -> JsonRpcError:
    """exec が無効（off / audited で許可リスト外）のときの EXEC_DISABLED（PRECONDITION・retryable=False）。"""
    return JsonRpcError(
        RPC_BUSINESS_ERROR,
        ErrorCode.EXEC_DISABLED,
        make_error(
            ErrorCode.EXEC_DISABLED,
            category=ErrorCategory.PRECONDITION,
            retryable=False,
            symptom=symptom,
            remediation=remediation,
        ),
    )


def _exec_blocked_restricted(blocked: list[str], remediation: str) -> JsonRpcError:
    """restricted のブロックリスト検出（EXEC_BLOCKED_RESTRICTED・PRECONDITION・retryable=False）。

    「何がブロックされたか」を症状文へ列挙する（scan_blocked の理由は `import:subprocess` 等の
    自己記述形式）。修正して再実行すれば通るコードはコード修正が本筋なので、trusted 昇格の案内は
    remediation 側に置く（P1-1・設計レビュー G0）。
    """
    return JsonRpcError(
        RPC_BUSINESS_ERROR,
        ErrorCode.EXEC_BLOCKED_RESTRICTED,
        make_error(
            ErrorCode.EXEC_BLOCKED_RESTRICTED,
            category=ErrorCategory.PRECONDITION,
            retryable=False,
            symptom=f"exec mode=restricted: ブロック対象を検出しました: {', '.join(blocked)}",
            remediation=remediation,
        ),
    )


def _audit_exec(entry: Any) -> bool:
    """exec 監査を記録し、失敗（best-effort False）なら stderr に警告する（§280 の検知漏れを観測可能に）。

    executed 経路は戻り値を `audit_ok` で応答に載せるが、rejected 経路（off / audited-unlisted /
    restricted-blocked）は raise で終わり応答に載らないため、ここで stderr 警告して証跡欠落を必ず
    観測可能にする。
    """
    from .. import audit

    ok = audit.record(entry)
    if not ok:
        import sys

        print(
            "[bli] warning: exec 監査ログの書き込みに失敗しました（証跡が残りません・§280）",
            file=sys.stderr,
        )
    return ok


def _exec_python(params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    """構造化で表現できない操作の逃げ道（spec D3）。**サンドボックスなし**＝防止でなく検知（§459）。"""
    cmd = _command("exec-python")
    _validate(cmd, params)
    code = params.get("code")
    file = params.get("file")
    has_code = isinstance(code, str) and code.strip() != ""
    has_file = isinstance(file, str) and file.strip() != ""
    # code/file は排他（どちらか一方が必須）。bpy 到達前に弾く（§6e）。
    _require_input(
        has_code != has_file,
        symptom="--code か --file のどちらか一方を指定してください（両方/どちらも無しは不可）",
        remediation='exec-python --code "<python>" または --file <path> のどちらかを使ってください',
    )

    # **mode の真実源はサーバが読む policy.toml（R-A）**。params の mode は一切読まない＝CLI 単体では
    # 昇格できない（spec §276・§459）。読取は実行ごとに最新化（trusted→off の切替を即反映＝安全側）。
    from .. import audit, policy

    mode = policy.read_exec_mode()

    # off: file を読む前に拒否（試行は監査に残す＝防止でなく検知・§280）。
    if mode == "off":
        sha = audit.code_sha256(str(code)) if has_code else None
        ref = "code" if has_code else f"file:{file}"
        _audit_exec(
            audit.make_entry(
                mode="off",
                decision="rejected:off",
                source=ref,
                code_sha256=sha,
                code_len=len(str(code)) if has_code else None,
            )
        )
        raise _exec_disabled(
            "exec-python は無効です（既定 off・サンドボックスは提供しません）",
            # 有効化は**人間に依頼する**文型にする（エージェントへの自動昇格の指示にしない・R1-1）。
            f"有効化するには、**ユーザ（人間）に** policy.toml（{policy.policy_path()}）の "
            "[exec] mode を restricted（推奨: Blender API は自走・プロセス起動/ネットワーク/削除系は"
            "拒否）へ変更してもらってください（例: `bli policy --action set --mode restricted` を"
            "ユーザが実行・対話確認つき。リポジトリ内の config.toml では昇格できません）",
        )

    # restricted/audited/trusted: source を解決する（--file は直接 RPC 用にサーバ側でも読む。CLI は
    # --file を CLI 側で読んで code として送る）。path はサーバ（Blender プロセス）の CWD 基準で解決される。
    if has_file:
        import os

        abspath = os.path.abspath(str(file))
        _require_input(
            os.path.isfile(abspath),
            symptom=f"スクリプトファイルが見つかりません: {abspath}",
            remediation="存在するファイルのパスを指定してください（パスは Blender プロセスの CWD 基準）",
        )
        try:
            with open(abspath, encoding="utf-8") as fh:
                source = fh.read()
        except OSError as e:
            raise _exec_error(
                f"スクリプトファイルの読み取りに失敗しました: {e}", phase="compile"
            ) from e
        ref = f"file:{abspath}"
    else:
        source = str(code)
        ref = "code"

    from .. import ast_heuristics

    sha = audit.code_sha256(source)
    flags = ast_heuristics.scan(source)

    # restricted（P1-1・設計レビュー G0）: AST ブロックリスト検査で自走可否を決める。Blender API
    # （bpy/bmesh/mathutils 等）は全面許可・プロセス起動/ネットワーク/削除系/動的実行/書込 open を
    # 検出したら拒否（監査に blocked を残す）。**静的検査は完全ではない**（getattr 迂回等）＝安全保証
    # ではなく事故防止（spec §459・security_guarantee:false は不変）。
    # blocked は「restricted の検査を通ったか」の監査証跡: None=検査対象外の経路（trusted/audited）
    # / []=検査して通過 / 非空=拒否理由（audit.AuditEntry.blocked の契約と対）。
    blocked: list[str] | None = None
    if mode == "restricted":
        from .. import exec_restricted

        blocked = exec_restricted.scan_blocked(source)
        if blocked:
            _audit_exec(
                audit.make_entry(
                    mode="restricted",
                    decision="rejected:restricted-blocked",
                    source=ref,
                    code_sha256=sha,
                    code_len=len(source),
                    heuristic_flags=flags,
                    blocked=blocked,
                )
            )
            raise _exec_blocked_restricted(
                blocked,
                "コードからブロック対象（プロセス起動/ネットワーク/削除系/動的実行/書込 open）を"
                "除いて再実行してください。ファイル書き出しは export/save コマンドを使ってください。"
                f"どうしても必要な場合は**ユーザ（人間）の判断で** policy.toml"
                f"（{policy.policy_path()}）の [exec] mode を trusted（無制限）へ変更して"
                "もらってください（エージェントが自ら昇格しないこと）",
            )

    # audited（R-B）: 許可ハッシュ集合に一致するコードだけ自走実行する。不一致は監査に残して拒否し、
    # 追加すべき sha を提示する（ユーザがその sha を policy.toml の allow_hashes に足せば次回から自走）。
    if mode == "audited" and sha not in policy.read_allow_hashes():
        _audit_exec(
            audit.make_entry(
                mode="audited",
                decision="rejected:audited-unlisted",
                source=ref,
                code_sha256=sha,
                code_len=len(source),
                heuristic_flags=flags,
            )
        )
        raise _exec_disabled(
            f"exec mode=audited: このコードは許可リストにありません（sha256={sha}）",
            f"承認するなら、**ユーザ（人間）に** policy.toml の [exec] allow_hashes へこの sha256 を"
            f"追加してもらってください: {sha}",
        )

    # 実行が確定（trusted / restricted で検査通過 / audited で許可済み）。**実行前に**監査へ記録する
    # （証跡を先に残す）。restricted の通過は blocked=[]（検査済みの証跡）として残る。
    audit_ok = _audit_exec(
        audit.make_entry(
            mode=mode,
            decision="executed",
            source=ref,
            code_sha256=sha,
            code_len=len(source),
            heuristic_flags=flags,
            blocked=blocked,
        )
    )

    from .. import gateway  # lazy: bpy 依存

    _check_mode(cmd, gateway.current_mode())
    outcome, fingerprint = gateway.exec_user_code(source)
    if outcome.error is not None:
        # 例外直前までにキャプチャした stdout/stderr を cause に載せ、観測性を失わない。
        captured = []
        if outcome.stdout:
            captured.append(f"stdout: {outcome.stdout.strip()}")
        if outcome.stderr:
            captured.append(f"stderr: {outcome.stderr.strip()}")
        raise _exec_error(
            f"{outcome.error.type}: {outcome.error.message}",
            phase=outcome.error.phase,
            cause=" | ".join(captured),
        )
    data = {
        "mode": mode,
        "stdout": outcome.stdout,
        "stderr": outcome.stderr,
        "result_repr": outcome.result_repr,
        # **サンドボックスはしない**＝この出力を信頼の根拠にしないこと（spec §459・常に false）。
        "security_guarantee": False,
        # AST ヒューリスティック（T11.2・R-D）。注意喚起のみでブロックしない（mode ゲートとは独立）。
        "heuristic_flags": flags,
        # 許可リスト追加用（audited 昇格に使える）。
        "code_sha256": sha,
        # 監査記録に成功したか（false なら証跡欠落＝可用性優先で実行はしたが観測可能にする・§280）。
        "audit_ok": audit_ok,
    }
    return _ok_offload("exec-python", data, "exec-python/v1", fingerprint=fingerprint)
