"""文件上传管理 - 镜像tar / 代码zip / SSH密钥 / 对比CSV"""

import json
import re
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile

from ..core.compare_core import load_result_csv
from ..core.docker_manager import DockerManager
from ..state import AppState
from .deps import get_state

router = APIRouter(prefix="/upload", tags=["files"])

uploads_router = APIRouter(prefix="/uploads", tags=["files"])


_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._\-()\u4e00-\u9fff]+")


def _safe_filename(name: str) -> str:
    """清理文件名, 保留常见安全字符"""
    name = Path(name.replace("\\", "/")).name
    name = _SAFE_NAME_RE.sub("_", name).strip("._")
    return name or "upload.bin"


def _save_upload(state: AppState, file: UploadFile, prefix: str) -> Path:
    """将上传文件流式保存到 uploads 目录, 返回目标路径"""
    filename = _safe_filename(file.filename or "upload.bin")
    stamp = time.strftime("%Y%m%d_%H%M%S")
    dest = Path(state.uploads_dir) / f"{stamp}_{prefix}_{filename}"
    counter = 1
    while dest.exists():
        dest = Path(state.uploads_dir) / f"{stamp}_{prefix}_{counter}_{filename}"
        counter += 1
    try:
        with dest.open("wb") as out:
            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
    finally:
        file.file.close()
    return dest


def _write_meta(path: Path, meta: dict):
    meta_path = path.with_suffix(path.suffix + ".meta.json")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


def _read_meta(path: Path) -> Optional[dict]:
    meta_path = path.with_suffix(path.suffix + ".meta.json")
    if meta_path.exists():
        try:
            return json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None
    return None


def _infer_kind(name: str) -> str:
    lowered = name.lower()
    if lowered.endswith((".tar", ".tar.gz", ".tgz")):
        return "image"
    if lowered.endswith(".zip"):
        return "code-zip"
    if lowered.endswith(".csv"):
        return "compare-csv"
    if lowered.endswith((".pem", ".key")) or "rsa" in lowered or "ed25519" in lowered:
        return "ssh-key"
    return "other"


@router.post("/image")
def upload_image(file: UploadFile, state: AppState = Depends(get_state)):
    """上传镜像tar包, 自动预读 manifest.json 识别镜像名"""
    dest = _save_upload(state, file, "image")
    image_name = DockerManager.get_image_name_from_tar(str(dest))
    meta = {"kind": "image", "image_name": image_name}
    _write_meta(dest, meta)
    return {
        "ok": True,
        "id": dest.name,
        "filename": dest.name,
        "size": dest.stat().st_size,
        "image_name": image_name,
    }


@router.post("/code-zip")
def upload_code_zip(file: UploadFile, state: AppState = Depends(get_state)):
    """上传测试代码zip包"""
    dest = _save_upload(state, file, "code")
    if not dest.name.lower().endswith(".zip"):
        dest.unlink()
        raise HTTPException(400, "仅支持 .zip 格式的代码包")
    _write_meta(dest, {"kind": "code-zip"})
    return {
        "ok": True,
        "id": dest.name,
        "filename": dest.name,
        "size": dest.stat().st_size,
    }


@router.post("/ssh-key")
def upload_ssh_key(file: UploadFile, state: AppState = Depends(get_state)):
    """上传SSH密钥文件"""
    dest = _save_upload(state, file, "key")
    _write_meta(dest, {"kind": "ssh-key"})
    return {
        "ok": True,
        "id": dest.name,
        "filename": dest.name,
        "size": dest.stat().st_size,
    }


@router.post("/compare-csv")
def upload_compare_csv(file: UploadFile, state: AppState = Depends(get_state)):
    """上传结果对比CSV(立即校验格式)"""
    dest = _save_upload(state, file, "csv")
    if not dest.name.lower().endswith(".csv"):
        dest.unlink()
        raise HTTPException(400, "仅支持 .csv 格式")
    rows, err = load_result_csv(str(dest))
    meta = {"kind": "compare-csv", "rows": len(rows), "error": err}
    _write_meta(dest, meta)
    if err:
        return {
            "ok": False,
            "id": dest.name,
            "filename": dest.name,
            "size": dest.stat().st_size,
            "rows": 0,
            "error": err,
        }
    return {
        "ok": True,
        "id": dest.name,
        "filename": dest.name,
        "size": dest.stat().st_size,
        "rows": len(rows),
        "error": None,
    }


@uploads_router.get("")
def list_uploads(state: AppState = Depends(get_state)):
    """列出所有上传文件(含元数据)"""
    items = []
    uploads = Path(state.uploads_dir)
    for path in sorted(uploads.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not path.is_file() or path.name.endswith(".meta.json"):
            continue
        meta = _read_meta(path) or {}
        items.append({
            "id": path.name,
            "filename": path.name,
            "size": path.stat().st_size,
            "kind": meta.get("kind") or _infer_kind(path.name),
            "image_name": meta.get("image_name"),
            "rows": meta.get("rows"),
            "error": meta.get("error"),
            "uploaded_at": time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(path.stat().st_mtime)),
        })
    return {"items": items}


@uploads_router.delete("/{upload_id}")
def delete_upload(upload_id: str, state: AppState = Depends(get_state)):
    """删除上传文件"""
    uploads = Path(state.uploads_dir).resolve()
    path = (uploads / upload_id).resolve()
    if not str(path).startswith(str(uploads)) or not path.is_file():
        raise HTTPException(404, "文件不存在")
    path.unlink()
    meta = path.with_suffix(path.suffix + ".meta.json")
    if meta.exists():
        meta.unlink()
    return {"ok": True}
