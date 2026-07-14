"""キャプチャ（viewport/screen/render）ハンドラ（ops/ 分割 P2-4）。

元 ops.py の該当セクションをそのまま移設（挙動変更なし）。
"""

from __future__ import annotations

from typing import Any

from bli_core.errors import RPC_BUSINESS_ERROR, ErrorCategory, ErrorCode, make_error
from bli_core.protocol import JsonRpcError

from ..handlers import ServerInfo
from ._shared import _check_mode, _command, _ok, _require_input, _validate


def _png_dimensions(path: str) -> tuple[int, int] | None:
    """PNG の IHDR から実出力解像度 (width, height) を読む。

    screen は area 全体≠実出力（WINDOW リージョン）で解像度がずれ得るため、報告値は
    保存済み PNG の実寸を採る（全 source 共通・敵対的レビュー P2-2）。
    """
    import struct

    with open(path, "rb") as f:
        head = f.read(24)
    if len(head) >= 24 and head[:8] == b"\x89PNG\r\n\x1a\n":
        w, h = struct.unpack(">II", head[16:24])
        return int(w), int(h)
    return None


def _capture(params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    cmd = _command("capture")
    _validate(cmd, params)
    source = str(params.get("source", "viewport"))
    # camera は render 専用 / width・height は screen 不可（領域サイズ固定）。silent ignore せず弾く（§6e）。
    if "camera" in params:
        _require_input(
            source == "render",
            symptom="--camera は render のときのみ有効です",
            remediation="render で使うか --camera を外してください",
        )
    if "width" in params or "height" in params:
        _require_input(
            source != "screen",
            symptom="--width/--height は screen では指定できません（領域サイズ固定）",
            remediation="viewport/render で使うか --width/--height を外してください",
        )

    from bli_core import runtime

    # 解像度は暴走防止のため範囲を bpy 到達前に弾く（範囲は ops が SSOT・CLI は型/ENUM のみ検証）。
    for key in ("width", "height"):
        if key in params:
            v = int(params[key])
            _require_input(
                runtime.CAPTURE_MIN_DIM <= v <= runtime.CAPTURE_MAX_DIM,
                symptom=f"--{key} は {runtime.CAPTURE_MIN_DIM}〜{runtime.CAPTURE_MAX_DIM} の範囲です",
                remediation="範囲内の値を指定してください",
            )

    import os

    from bli_core import output_ref as outref

    from .. import gateway  # lazy: bpy 依存

    _check_mode(cmd, gateway.current_mode())

    out_dir = runtime.outputs_dir()
    tmp_path = str(out_dir / f"capture_tmp{os.getpid()}.png")
    width = int(params.get("width", runtime.CAPTURE_DEFAULT_WIDTH))
    height = int(params.get("height", runtime.CAPTURE_DEFAULT_HEIGHT))
    try:
        if source == "viewport":
            meta = gateway.capture_viewport(tmp_path, width, height)
        elif source == "screen":
            meta = gateway.capture_screen(tmp_path)
        elif source == "render":
            camera = params.get("camera")
            meta = gateway.capture_render(
                tmp_path, width, height, str(camera) if camera is not None else None
            )
        else:  # source は ENUM 検証済みのため到達不能（新 source の分岐漏れ検出の防御）
            raise JsonRpcError(
                RPC_BUSINESS_ERROR,
                ErrorCode.E_PRECONDITION,
                make_error(ErrorCode.E_PRECONDITION, symptom=f"未対応の source: {source}"),
            )
        # 出力ファイルをコンテンツアドレスで退避（パス安全/アトミック/ストリーミング sha を output_ref と共有）。
        descriptor = outref.offload_file(tmp_path, "capture/v1", out_dir, suffix=".png")
    except OSError as e:
        # gateway 成功後のファイル I/O 失敗（書き出し失敗/容量/権限）は INTERNAL でなく業務エラーへ（敵対的 P1-1）。
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise JsonRpcError(
            RPC_BUSINESS_ERROR,
            ErrorCode.E_OPERATOR,
            make_error(
                ErrorCode.E_OPERATOR,
                category=ErrorCategory.ENVIRONMENT,
                retryable=False,
                symptom=f"キャプチャ出力の書き出しに失敗しました: {e}",
                remediation="ディスク容量/権限/outputs ディレクトリを確認してください",
            ),
        ) from e

    dims = _png_dimensions(descriptor["path"])  # 実出力解像度（screen の領域≠出力ずれを吸収）
    out_w, out_h = dims if dims is not None else (meta.get("width"), meta.get("height"))
    data: dict[str, Any] = {
        "source": source,
        "path": descriptor["path"],
        "size": descriptor["size"],
        "sha256": descriptor["sha256"],
        "width": out_w,
        "height": out_h,
    }
    if "camera" in meta:  # render の実描画カメラ（active 解決後の名前）
        data["camera"] = meta["camera"]
    return _ok("capture", data, fingerprint=descriptor["id"])
