#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GUI  v1.5  |  DICOM ▶ JPEG + tags.xlsx + IIIF manifest.json + 검증 리포트
────────────────────────────────────────────────────────────────────────────

DICOM to JPEG & IIIF Manifest Converter with Validation (v1.5)

Overview:
    GUI 기반의 DICOM → JPEG 변환, IIIF Presentation API v3 manifest 생성
    및 외부 도구(dciodvfy)를 이용한 DICOM 규격 검증을 자동화하는 스크립트입니다.

Usage:
    python dicom_iiif_converter.py \
        --dic-dir <DICOM_FOLDER>    # DICOM(.dcm) 파일이 모여 있는 폴더 경로
        --out-dir <OUTPUT_FOLDER>   # JPEG, manifest, 로그 등이 저장될 출력 폴더
        --base-url <IIIF_BASE_URL>  # IIIF manifest 내 이미지 참조용 Base URL
        --img-base <IMAGE_BASE_URL> # JPEG 이미지를 호스팅하는 Base URL
        --dict-file <TAG_DICT>      # Private Tag 사전 파일 (.xlsx 또는 .json)
        --dciodvfy <DCIODVFY_EXE>   # dciodvfy.exe 실행 파일 경로
        [--manifest <MANIFEST_FILE>]# 생성할 manifest.json 파일명 (기본: manifest.json)
        [--excel <TAGS_XLSX>]       # 생성할 태그 목록 Excel 파일명 (기본: tags.xlsx)

Options:
    --dic-dir      필수  DICOM 파일 폴더 지정
    --out-dir      필수  출력 폴더 지정
    --base-url     필수  IIIF manifest Base URL
    --img-base     필수  JPEG 이미지 Base URL
    --dict-file    필수  태그 사전 파일 경로
    --dciodvfy     필수  DICOM 검증 도구 경로
    --manifest     선택  manifest 출력 파일명 지정
    --excel        선택  태그 목록 Excel 파일명 지정

Description:
    - 16비트 DICOM 이미지를 8비트 JPEG로 변환하고,
    - IIIF manifest에 각 이미지 캔버스 정보를 포함하여 JSON으로 생성하며,
    - dciodvfy를 호출하여 DICOM 규격 오류/경고를 수집합니다.
    - 진행 상황은 변환 단계(CONVERT)와 검증 단계(VALIDATE)로 구분되어 상세 로그에 표시됩니다.
"""

from __future__ import annotations
import os, ctypes, json, pathlib, re, subprocess, threading, csv, urllib.parse
from collections import Counter, defaultdict
from pathlib import Path
from datetime import datetime
from time import perf_counter
from typing import Dict, Tuple, List

import numpy as np
import pandas as pd
import pydicom
from pydicom.pixel_data_handlers.util import apply_modality_lut
from PIL import Image
import chardet

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext, StringVar, END, NORMAL, DISABLED
import subprocess, shutil, tempfile # 기존 + datetime
import pandas as pd                            # 엑셀 저장용

# ───▶ PASTE: META‑ITER UTIL + Flags ───────────────────────────────
import tkinter as tk
from itertools import chain
from pathlib import Path
import json, re, html as _html
import shlex  # ← shlex 미정의 경고 해결

def ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p

def append_jsonl(path: Path, obj: dict) -> None:
    ensure_dir(path.parent)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")

# --- folder save helpers -----------------------------------

def save_json_in_folder(base_dir: Path, folder_name: str, filename: str, obj: dict):
    folder = base_dir / folder_name
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / filename
    p.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    return p

# -----------------------------------------------------------


# SOP Instance UID는 새로 생성하거나 기존과 일치시킵니다.


# ─────────────────────────────────────────────────────────────────────────────
# dciodvfy 로그 파서/집계 패치
# ─────────────────────────────────────────────────────────────────────────────


# 카테고리 패턴 (필요시 자유롭게 추가)
_CATEGORY_RULES = [
    (r"Unrecognized tag",                     "Unrecognized/Private tag"),
    (r"Value dubious .* PN",                  "Value dubious (PN)"),
    (r"Retired Person Name form",             "Value dubious (PN)"),
    (r"Information Object Not found",         "IOD not found"),
    (r"No Information Object definition found","IOD not found"),
    (r"INVALID",                              "Invalid value/structure"),
    (r"cannot find.*IOD|not.*IOD",            "IOD not found"),
    (r"Attribute.*not found|Tag not found",   "Missing attribute/tag"),
    (r"Error",                                "Error (other)"),
    (r"Warning",                              "Warning (other)"),
]

_TAG_PAT = re.compile(r"\{?0x([0-9A-Fa-f]{4})[,)]\s*0x([0-9A-Fa-f]{4})\}?")

root = tk.Tk()
def iter_with_meta(ds, include_meta: bool):
    """
    DICOM Dataset 을 순회할 때
    include_meta=True  → file_meta + 본문
    include_meta=False → 본문만
    """
    if include_meta and getattr(ds, "file_meta", None):
        yield from ds.file_meta          # 0002 그룹
    yield from ds.iterall()              # 나머지 그룹

# 전역 BooleanVar – 반드시 master 지정
_include_meta_var = tk.BooleanVar(master=root, value=True)
_export_iiif_var  = tk.BooleanVar(master=root, value=True)
# ──────────────────────────────────────────────────────────────────


# ─── Constants & Regex ─────────────────
WINDOW_DEFAULT = (0.0, 400.0)
IGNORE_VR = {"OB","OW","OF","OD","UN"}
TXT_RE = re.compile(r"\((?P<grp>[0-9A-Fa-f]{4}),(?P<elm>[0-9A-Fa-f]{4})\)\s+(?P<vr>[A-Z]{2})\s+\d+\s+(?P<keyword>.+)")
NUM_RE = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")
DEFAULT_DCIODVFY = r"C:/tools/dicom3tools/bin/dciodvfy.exe"

# ─────────────── 헬퍼 ────────────────
def window_level(arr: np.ndarray, level: float, width: float) -> np.ndarray:
    low, high = level - width / 2, level + width / 2
    arr = np.clip(arr, low, high)
    return ((arr - low) / width * 255).astype(np.uint8)

def lang_map(txt: str, lang: str = "none") -> Dict[str, List[str]]:
    return {lang: [str(txt)]}

def find_dciodvfy() -> Path | None:
    """
    ① GUI에서 지정한 경로(_dciodvfy) → ② 시스템 PATH 순으로
    dciodvfy.exe 를 검색해 첫 번째로 발견된 실행파일을 돌려준다.
    """
    # ① GUI 입력
    cand = Path(_dciodvfy.get()).expanduser()
    if cand.is_file():
        return cand
    # ② 기본 경로
    cand = Path(DEFAULT_DCIODVFY)
    if cand.is_file():
        return cand
    # ③ PATH 검색
    for p in os.environ["PATH"].split(os.pathsep):
        exe_test = Path(p) / "dciodvfy.exe"
        if exe_test.is_file():
            return exe_test
    return None

def short_path(p: str) -> str:
    """
    Windows 8.3(ASCII) 짧은 경로 변환.
    • 공백·한글·특수문자가 있는 긴 경로를 dciodvfy가 읽지 못할 때 우회.
    • 변환 실패 시 원본 경로 그대로 반환.
    """
    buf = ctypes.create_unicode_buffer(260)
    if ctypes.windll.kernel32.GetShortPathNameW(p, buf, 260):
        return buf.value
    return p

# ── NEW ────────────────────────────────────────────────────
def dciodvfy_supports_t(exe_test: Path | str) -> bool:
    """
    dciodvfy -version → 'Dicom3tools 2024…' 형식이면 -t 지원.
    과거 릴리스는 날짜(YYYYMMDD) 또는 없음.
    """
    try:
        out = subprocess.check_output([str(exe_test), "-version"],
                                      stderr=subprocess.STDOUT,
                                      text=True, encoding="utf-8", timeout=3)
    except Exception:
        return False
    # 예) "Dicom3tools version 2022.05.26 (DJDICM #…)"
    for tok in out.split():
        if tok.replace(".", "").isdigit() and len(tok) >= 8:
            # 20220526 이상이면 -t 지원
            try:
                dt = datetime.strptime(tok.replace(".", ""), "%Y%m%d")
                return dt >= datetime(2022, 5, 26)
            except ValueError:
                continue
    return False


# ─── dciodvfy 절대 경로(필요 시 수정) ─────────────────────────
#DCIODVFY = r"C:\\Users\\USER\\Desktop\\논문_ 코드20250702\\2.DICOM to JPEG _ tags_IIIF manifest converter\\2.DICOM to JPEG _ tags_IIIF manifest converter\\20250707\\dicom3tools\\dciodvfy.exe"
# 예시 경로이므로 .exe 가 정확히 존재하는 위치로 수정
# ------------------------------------------------------------------


def run_dciodvfy(dcm: Path, output_dir: Path | None = None) -> dict:
    exe_test = find_dciodvfy()
    if not exe_test:
        raise RuntimeError("dciodvfy.exe not found – 경로를 지정하거나 PATH에 추가하세요!")

    output_dir = ensure_dir((output_dir or dcm.parent))
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = dcm.stem

    # 실행
    cmd = [str(exe_test), "-v", short_path(str(dcm))]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=60)
    out = (proc.stdout or "") + (proc.stderr or "")

    # 결과 파일 (텍스트/요약 JSON/덤프만)
    txt_path  = output_dir / f"validate_{base}_{ts}.txt"
    dump_path = output_dir / f"{base}_dciodvfy_dump_{ts}.txt"
    txt_path.write_text(out,  encoding="utf-8")
    dump_path.write_text(out, encoding="utf-8")

    # 분류
    ERR_PAT  = re.compile(r'^(?:E:|Error\b)', re.I)
    WARN_PAT = re.compile(r'^(?:W:|Warning\b)|\((?:0x)?[0-9A-Fa-f]{4},(?:0x)?[0-9A-Fa-f]{4}\)\s+\?\s+-\s+Warning\s+-', re.I)
    lines = out.splitlines()
    errors     = [l for l in lines if ERR_PAT.search(l) or "Abort" in l]
    warns_all  = [l for l in lines if WARN_PAT.search(l)]
    priv_warns = [w for w in warns_all if "Unrecognized tag" in w and "(0x0013," in w]
    warns      = [w for w in warns_all if w not in priv_warns]

    # 요약 JSON (디버깅/후처리 용)
    json_path = output_dir / f"validate_{base}_{ts}_summary.json"
    json_path.write_text(json.dumps({
        "summary": {
            "exit": proc.returncode,
            "errors": len(errors),
            "warnings": len(warns),
            "priv_warnings": len(priv_warns),
            "all_warnings": len(warns_all),
        },
        "errors": errors,
        "warnings": warns,
        "priv_warnings": priv_warns,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "exit": proc.returncode,
        "errors": errors,
        "warnings": warns,
        "priv_warns": priv_warns,
        "files": {
            "text": str(txt_path),
            "json": str(json_path),
            "dump": str(dump_path),  # ✔ HTML 없음
        },
    }

def normalize_for_sc(ds):
    """SC IOD 경고 자동 보정:
       - UTF-8 표기 정규화 (ISO_IR 192)
       - ImageType 2번째 값 'SECONDAR' 오타 → 'SECONDARY'
       - Type 2 필드만 빈값으로 존재 보장 (ReferringPhysicianName, AccessionNumber, PositionReferenceIndicator)
       - Laterality/PatientOrientation(모두 2C)는 생성 금지, 비어 있거나 무의미하면 제거
       - ImagePositionPatient 기본값
    """
    # 1) SpecificCharacterSet 정규화 (아래 2)와 중복되지만 안전망으로 둡니다)
    # SpecificCharacterSet 정규화: 잘못된 표기/다값 → 단일 'ISO_IR 192'
    scs = getattr(ds, "SpecificCharacterSet", None)
    def _tokens(x):
        if scs != "ISO_IR 192":
            ds.SpecificCharacterSet = "ISO_IR 192"
        if isinstance(x, (list, tuple)):
            return [str(t).strip() for t in x if str(t).strip()]
        if isinstance(x, str):
            s = x.strip().strip("^$").replace("_"," ").upper()
            # 역슬래시 다값 들어온 경우 분리
            if "\\" in x:
                return [t.strip() for t in x.split("\\") if t.strip()]
            return [s]
        return []

    tok = _tokens(scs)
    if tok in (["ISO IR 192"], ["ISO IR","192"], ["ISO","IR","192"]) or any(t in ("UTF8","UTF-8","UTF 8") for t in tok):
        ds.SpecificCharacterSet = "ISO_IR 192"
    elif isinstance(scs, str) and scs.strip("^$") == "ISO_IR 192":
        ds.SpecificCharacterSet = "ISO_IR 192"


    # 2) ImageType[1]: SECONDAR → SECONDARY
    if hasattr(ds, "ImageType"):
        try:
            parts = list(ds.ImageType)
            if len(parts) > 1 and str(parts[1]).upper() == "SECONDAR":
                parts[1] = "SECONDARY"
                ds.ImageType = parts
        except Exception:
            pass

    # 3) Type 2 (빈값 허용, 반드시 존재)
    for kw in ("ReferringPhysicianName", "AccessionNumber", "PositionReferenceIndicator"):
        if not getattr(ds, kw, None):
            setattr(ds, kw, "")

    # 4) Type 2C (조건부) — 생성 금지, 비/무의미 값은 제거
    for kw in ("Laterality", "PatientOrientation"):
        if hasattr(ds, kw):
            val = getattr(ds, kw)
            empty = val in ("", None, []) or (isinstance(val, (list, tuple)) and not any(str(x).strip() for x in val))
            if empty:
                try:
                    delattr(ds, kw)
                except Exception:
                    pass

    # 5) ImagePositionPatient 기본값
    if not getattr(ds, "ImagePositionPatient", None):
        ds.ImagePositionPatient = [0.0, 0.0, 0.0]





# ───── run_dciodvfy_cli() REWRITE ─────────────────────────────
def run_dciodvfy_cli(
    dcm_path: str | Path,
    out_dir: str | Path,
    jsonl_path: str | Path | None = None,
    cb_log=lambda msg, lvl="info": print(msg),
):
    """
    한 개 DICOM에 대해 dciodvfy 실행 → 로그 스트리밍 → JSONL/HTML/CSV/텍스트 저장
    GUI에서 on_validate()가 호출하는 진짜 실행 엔진.
    """
    # --- 경로 정리 ---
    dcm = Path(dcm_path)
    out_dir = Path(out_dir.get()) if hasattr(out_dir, "get") else Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # --- dciodvfy 실행 준비 ---
    exe = find_dciodvfy()
    if not exe:
        raise RuntimeError("dciodvfy.exe not found – 경로를 지정하거나 PATH에 추가하세요!")

    cmd = [str(exe), "-v", short_path(str(dcm))]
    if _dump_flag.get() == "on":
        cmd.insert(1, "-dump")

    env = os.environ.copy()
    if _privtag_path.get():
        env["DCMDICTPATH"] = _privtag_path.get()

    cb_log(f"[info] run: {' '.join(shlex.quote(c) for c in cmd)}  (with DCMDICTPATH)", "info")

    # --- 실행 & 스트리밍 캡처 ---
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    all_lines: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip("\r\n")
        all_lines.append(line)
        cb_log(line, "info")
    proc.wait()

    out_text = "\n".join(all_lines)

    # --- 파싱 (에러/경고/프라이빗 경고 분리) ---
    ERR_PAT  = re.compile(r'^(?:E:|Error\b)', re.I)
    WARN_PAT = re.compile(r'^(?:W:|Warning\b|\(0x[0-9A-Fa-f]{4},0x[0-9A-Fa-f]{4}\)\s+\?\s+-\s+Warning\s+-)', re.I)

    errors     = [l for l in all_lines if ERR_PAT.search(l) or "Abort" in l]
    warns_all  = [l for l in all_lines if WARN_PAT.search(l)]
    priv_warns = [w for w in warns_all if "Unrecognized tag" in w and "(0x0013," in w]
    warns      = [w for w in warns_all if w not in priv_warns]

    # 교체:
    msgs = ([{"severity": "ERR",  "text": m} for m in errors] +
            [{"severity": "WARN", "text": m} for m in warns])

    # --- 개별 텍스트 로그 저장 ---
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = dcm.stem
    txt_path  = out_dir / f"validate_{base}_{ts}.txt"
    txt_path.write_text(out_text, encoding="utf-8")

    # --- JSONL 누적 저장 (Dump 포함) ---
    jsonl_path = Path(jsonl_path) if jsonl_path else out_dir / "dciodvfy.jsonl"
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)


    # --- 경고 요약 CSV + HTML 리포트 생성 ---
    ts_snip   = ts  # 파일명에 동일 타임스탬프 사용
    html_path = out_dir / f"dciodvfy_report_{base}_{ts_snip}.html"
    write_html_report_from_jsonl(jsonl_path, html_path)

    # --- 로그 마무리 ---
    if errors:
        cb_log(f"[warn] {dcm.name} • 규격 오류 {len(errors)}건 · 경고 {len(warns)}건(프라이빗 {len(priv_warns)}건 제외)", "warn")
    elif warns:
        cb_log(f"[ok] {dcm.name} • 규격 경고 {len(warns)}건(프라이빗 {len(priv_warns)}건 제외)", "warn")
    else:
        cb_log(f"[ok] {dcm.name} Converter", "ok")

    return {
        "jsonl": str(jsonl_path),
        "text":  str(txt_path),
        "html":  str(html_path),
        "exit":  proc.returncode,
        "errors": errors,
        "warnings": warns,
        "priv_warns": priv_warns,
    }





# ── dciodvfy 메시지 유형 분류 ──────────────────────────────────────────────
_WARN_CATS = [
    (r"Unrecognized tag",                  "Unrecognized/Private tag"),
    (r"Value dubious .* PN",               "Value dubious (PN)"),
    (r"Information Object Not found",      "IOD not found"),
]

def _categorize_vfy_msg(msg:str)->str:
    import re
    for pat, name in _WARN_CATS:
        if re.search(pat, msg):
            return name
    return "Other"

def summarize_vfy(parsed_rows:list[dict]):
    """카테고리/심각도별 집계 + 샘플 메시지 3건씩 추출"""
    by_cat = Counter()
    by_sev = Counter()
    samples = defaultdict(list)

    for r in parsed_rows:
        by_cat[r["category"]] += 1
        by_sev[r["severity"]] += 1
        if len(samples[r["category"]]) < 3:
            # 샘플에는 태그가 있으면 붙여주면 분석 편함
            tag = f" [{r['tag']}]" if r.get("tag") else ""
            samples[r["category"]].append((r["msg"][:300] + "...") + tag)

    return {"by_cat": by_cat, "by_sev": by_sev, "samples": samples}

# [PATCH: helper] ─────────────────────────────────────────────
def _dedup_rows(rows_in):
    seen = set()
    out = []
    for r in rows_in:
        key = (
            r.get("file",""),
            r.get("severity",""),
            r.get("tag",""),
            r.get("msg","") or r.get("text",""),
            r.get("category",""),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out
# ────────────────────────────────────────────────────────────
# [PATCH: inside write_html_report_from_jsonl(...)]
# 1) rows를 모두 수집한 "직후"에 중복 제거


def render_summary_html(summary:dict)->list[str]:
    """집계 결과를 리포트 상단에 넣을 수 있는 HTML 라인 리스트로 반환"""
    by_cat  = summary["by_cat"]
    by_sev  = summary["by_sev"]
    samples = summary["samples"]

    lines = []
    # 헤더: 전체 건수/심각도
    total = sum(by_cat.values())
    lines.append("<h3>Validation summary</h3>")
    lines.append("<p style='margin:4px 0'>"
                 f"Total messages: <b>{total}</b> · "
                 f"Severity — ERR: <b>{by_sev['ERR']}</b>, "
                 f"PRIV: <b>{by_sev['PRIV']}</b>, "
                 f"WARN: <b>{by_sev['WARN']}</b></p>")

    # 카테고리 표
    lines.append("<table border='1' cellpadding='4' cellspacing='0' style='border-collapse:collapse'>")
    lines.append("<tr><th style='text-align:left'>Category</th>"
                 "<th>Count</th><th style='text-align:left'>Samples (up to 3)</th></tr>")
    for cat, n in sorted(by_cat.items(), key=lambda kv: -kv[1]):
        smp = "<br/>".join(samples.get(cat, []))
        lines.append(f"<tr><td>{cat}</td><td style='text-align:right'>{n}</td><td>{smp}</td></tr>")
    lines.append("</table>")
    return lines

def save_summary_csv(summary:dict, out_root:Path, prefix:str="validation_warning_summary"):
    """카테고리 집계를 CSV로 저장 (엑셀 열기 용이)"""
    ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
    rows = [{"category": k, "count": v} for k, v in summary["by_cat"].items()]
    df = pd.DataFrame(rows)
    csv_path = out_root / f"{prefix}_{ts}.csv"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    return csv_path




# ─── FAQ 패턴 → 설명 맵 ───
# ─── FAQ pattern → explanation map (EN-only) ───
FAQ = {
    # ① Private tag not recognized (0013,xxxx)
    r"Unrecognized tag.*(?:\(|\{)0x0013[,)]":
        "DICOCH private tag — not loaded in external dictionary or invalid Private Creator (≤16 chars, single value). Register tags and align Creator mapping.",

    # ② SOP/File Meta UID mismatches or missing
    r"MediaStorageSOPInstanceUID .* missing SOPInstanceUID":
        "Dataset (0008,0018) SOP Instance UID is missing — copy from file_meta or generate a new UID.",
    r"MediaStorageSOPClassUID .* missing SOPClassUID":
        "Dataset (0008,0016) SOP Class UID is missing — set a valid standard SOP Class UID (e.g., CT/SC).",

    # ③ Missing attributes
    r"Missing attribute .* Patient.?ID":
        "Patient ID (0010,0020) is missing — provide a minimal identifier.",
    r"(Tag not found|Attribute.*not found)":
        "Required/referenced attribute is missing — define the tag with proper VR and value.",

    # ④ PN formatting issues
    r"Value dubious .* (?:Person|Patient'?s) Name|Retired Person Name form":
        "Patient's Name (0010,0020) PN is for human names — leave blank or use 'Object^Artifact'. Put object name in Description/ID fields instead.",

    # ⑤ IOD match failure
    r"Information Object Not found|No Information Object definition found":
        "IOD match failed — align (0008,0016)/(0002,0002) to a standard SOP Class (e.g., CT Image Storage or Secondary Capture).",

    # ⑥ Typos / formatting
    r"Image Type.*SECONDAR":
        "Typo in Image Type — use 'DERIVED\\SECONDARY'.",

    # ⑦ Invalid SOP Classes in Study
    r"SOP Classes in Study .* (?:invalid|not a) UID":
        "List actual SOP Class UIDs for the study — not Instance UIDs or non-standard UIDs.",
        
    # --- 추가 매핑: 이번 리포트 이슈들 ---
    r"Missing attribute .*ReferringPhysicianName": "Referring Physician (0008,0090) Type 2 — 모르면 빈값이라도 존재해야 함",
    r"Missing attribute .*AccessionNumber": "Accession Number (0008,0050) Type 2 — 빈값 허용, 누락 금지",
    r"Missing attribute .*Laterality": "Laterality (0020,0060) Type 2C — 조건 불충족 시 빈값으로 존재",
    r"Missing attribute .*PositionReferenceIndicator": "Position Reference Indicator (0020,1040) Type 2 — 빈값 허용",
    r"Missing attribute .*PatientOrientation": "Patient Orientation (0020,0020) Type 2C — 조건 불충족 시 빈값",
    r"Missing attribute .*ImagePositionPatient": "Image Position (Patient) (0020,0032) Type 1 — 기본값 0\\0\\0 등으로 채움",
    r"Unrecognized enumerated value .*SECONDAR": "Image Type의 두 번째 값은 'SECONDARY'가 정식 철자",
    r"Unrecognized defined term .*ISO_IR_192": "SpecificCharacterSet은 'ISO_IR 192'(공백) 권장. 구버전 dciodvfy는 UTF-8을 인식 못할 수 있음",
    r"Attribute is not present in standard DICOM IOD": "SC IOD에 없는 속성 — 표준 Extended SOP Class로 허용되나 엄격 모드에선 제거 고려",
}

def _categorize_msg(msg:str)->str:
    for pat, cat in _CATEGORY_RULES:
        if re.search(pat, msg):
            return cat
    return "Other"

def _extract_tag_hex(msg:str)->str|None:
    m = _TAG_PAT.search(msg)
    if m:
        return f"{m.group(1).upper()},{m.group(2).upper()}"
    return None

# ⬇️ 기존 함수를 확장 (severity 로직은 그대로, category/tag/explain 추가)
def parse_vfy_messages(msg: str) -> dict:
    """dciodvfy 한 줄을 {'severity','msg','category','tag','explain'}로 정규화"""
    sev = ("ERR"  if re.search(r'^(E:|Error\b)', msg, re.I) else
           "WARN" if re.search(r'^(W:|Warning\b)', msg, re.I) else
           "PRIV" if ("Unrecognized tag" in msg and re.search(r'\(0x?0013,', msg)) else
           "INFO")

    m = re.search(r'\((?:0x)?([0-9A-Fa-f]{4}),(?:0x)?([0-9A-Fa-f]{4})\)', msg)
    tag = f"{m.group(1).upper()},{m.group(2).upper()}" if m else None

    rules = [
        ("PrivateTag",           re.compile(r"Unrecognized tag.*\(0x?0013,", re.I)),
        ("ImageType",            re.compile(r"\bImage Type\b|\bSECONDAR\b", re.I)),
        ("SpecificCharacterSet", re.compile(r"Specific Character Set|ISO_IR_192", re.I)),
        ("Laterality",           re.compile(r"\bLaterality\b", re.I)),
        ("NotInIOD",             re.compile(r"not present in standard DICOM IOD|Standard Extended SOP Class", re.I)),
        ("VRDubious",            re.compile(r"Value dubious for this VR", re.I)),
        ("RetiredPN",            re.compile(r"Retired Person Name", re.I)),
    ]
    category = "General"
    for name, pat in rules:
        if pat.search(msg):
            category = name
            break

    explain_map = {
        "ImageType": "Image Type[1]=DERIVED, [2]=SECONDARY 가 표준. 'SECONDAR' → 'SECONDARY'.",
        "SpecificCharacterSet": "UTF-8 표기는 'ISO_IR 192' (공백 포함).",
        "Laterality": "쌍이 아닌 부위면 빈 값 대신 요소 자체를 생략.",
        "PrivateTag": "사설 태그(0x0013,xxxx). 확장 사용.",
    }
    return {"severity": sev, "msg": msg, "category": category, "tag": tag,
            "explain": explain_map.get(category, "")}



    
def read_text_autodetect(path: pathlib.Path, sel_enc: str | None):
    """
    sel_enc: GUI에서 사용자가 선택한 인코딩(utf-8, cp949 …)  
    None이면 chardet로 자동 추정 후 디코딩.
    """
    if sel_enc:
        return path.read_text(encoding=sel_enc, errors="replace")
    raw = path.read_bytes()
    enc = chardet.detect(raw)["encoding"] or "utf-8"
    return raw.decode(enc, errors="replace")

# ───── load_dict() REWRITE ───────────────────────────────────────────
def load_dict(path: str) -> Dict[Tuple[int, int], Dict[str, str]]:
    """
    다양한 형식(xlsx·csv·json·txt) 태그 사전을
    {(group, element): {'keyword', 'vr', 'desc'}} 형태로 변환하고
    pydicom datadict 에 즉시 등록한다.
    """
    if not path:
        return {}

    p            = Path(path)
    records: Dict[Tuple[int, int], Dict[str, str]] = {}
    df: pd.DataFrame | None = None                    # 기본값

    try:
        suf = p.suffix.lower()

        # ── 1) 파일 형식별 로드 ───────────────────────────────
        if suf in (".csv", ".tsv"):
            df = pd.read_csv(p, sep="," if suf == ".csv" else "\t",
                             engine="python", on_bad_lines="skip")
        elif suf in (".xlsx", ".xls"):
            df = pd.read_excel(p,                  # group·element을 ‘문자열’ 고정
                            dtype={'group': str, 'element': str})
            df[['group','element']] = (df[['group','element']]
                                    .apply(lambda s: s.str.strip()
                                                        .str.upper()
                                                        .str.zfill(4)))
        elif suf == ".json":
            data = json.loads(read_text_autodetect(p, _dict_enc.get() or None))
            for k, v in data.items():
                g, e = int(k[1:5], 16), int(k[6:10], 16)
                records[(g, e)] = {
                    "keyword": v["keyword"],
                    "vr":      v["vr"].upper(),
                    "desc":    v.get("description", "")
                }
        else:  # txt( dciodvfy dump 등 )
            text  = read_text_autodetect(p, _dict_enc.get() or None)
            lines = text.splitlines()
            df = pd.DataFrame([
                {"group": m["grp"], "element": m["elm"],
                 "vr": m["vr"], "keyword": m["keyword"]}
                for line in lines if (m := TXT_RE.match(line.strip()))
            ])

        # ── 2) DataFrame → dict ─────────────────────────────
        if df is not None and not df.empty:
            # 열 이름 표준화(대소문자·공백 무시)
            df.columns = [c.lower().strip().replace(" ", "") for c in df.columns]

            for r in df.itertuples(index=False):
                g_hex, e_hex = str(r.group).strip(), str(r.element).strip()
                if not g_hex or not e_hex:
                    continue
                try:
                    g, e = int(g_hex, 16), int(e_hex, 16)
                except ValueError:
                    continue

                records[(g, e)] = {
                    "keyword": str(getattr(r, "keyword", "")).strip(),
                    "vr":      str(getattr(r, "vr", "")).strip().upper(),
                    "desc":    str(getattr(r, "description", "")).strip()
                }

    except Exception as ex:
        messagebox.showerror("사전 로드 오류", str(ex))
        return {}

    # ── 3) pydicom datadict 확장 ────────────────────────────
    for (g, e), meta in records.items():
        tag = (g << 16) | e
        try:
            (pydicom.datadict.add_private_dict_entry if g % 2 else
             pydicom.datadict.add_dict_entry)(tag, meta["vr"], meta["keyword"], meta["desc"])
        except Exception:
            pass

    return records
# ────────────────────────────────────────────────────────────────────
# ── ▶ NEW: privtag 사전 → txt  ────────────────────────────
def write_privtag_txt(records: dict) -> str:
    """
    (group,element) dict → privtags_dicoch.txt 임시파일 경로 반환
    형식: (0013,EEEE) VR "Keyword"
    """
    tmp   = tempfile.NamedTemporaryFile("w+", delete=False,
                                        encoding="utf-8", suffix=".txt")
    for (g, e), meta in sorted(records.items()):
        if g == 0x0013:
            line = f"({g:04X},{e:04X}) {meta['vr']} \"{meta['keyword']}\"\n"
            tmp.write(line)
    tmp.close()
    return tmp.name

# ── ▶ NEW: 대소문자·공백 무시 컬럼 매핑 ─────────────────
# ───▶ PASTE: NEW build_canvas_metadata ────────────────────────────
IGNORE_VR = {"OB","OW","OF","OD","UN"}     # PixelData 등 대용량 VR 제외

def build_canvas_metadata(ds, dmap, *, include_meta=True):
    """
    반환값 목록에 group, elem, vr, level, vtype 추가
    """
    md = []
    def walk(dset, depth=0):
        for elem in iter_with_meta(dset, include_meta):
            if elem.tag == (0x7FE0,0x0010) or elem.VR in IGNORE_VR:
                continue
            g,e = elem.tag.group, elem.tag.element
            kw  = (dmap.get((g,e),{}).get("keyword") or elem.keyword
                   or f"({g:04X},{e:04X})")
            md.append({
                "group": g, "element": e, "vr": elem.VR,
                "level": depth,                      # ← Level
                "type": type(elem.value).__name__,   # ← Value Type
                "label": {"none":[kw]},
                "value": {"none":[str(elem.value)[:1024]]}
            })
            if elem.VR == "SQ":                      # 시퀀스 재귀
                for item in elem:
                    walk(item, depth+1)
    walk(ds)
    return md

# ──────────────────────────────────────────────────────────────────


def summary_stats(rows):
    total = len(rows)
    private = sum(1 for r in rows if r["keyword"].startswith("(0013"))
    hu_vals = [float(m.group())
               for r in rows
               if (m := NUM_RE.search(str(r["value"])))
               and r["keyword"].lower().startswith("mean hu")]
    stats = [
        {"label": lang_map("Total Tags"),   "value": lang_map(total)},
        {"label": lang_map("Private Tags"), "value": lang_map(private)}
    ]
    if hu_vals:
        stats += [
            {"label": lang_map("HU Min"), "value": lang_map(min(hu_vals))},
            {"label": lang_map("HU Max"), "value": lang_map(max(hu_vals))}
        ]
    return stats
# ─────────────── GUI ────────────────



root.title("DICOM ▶ JPEG · Manifest · Validation (v1.5)")
# ─── Notebook(탭 컨트롤) 만들기 ───
 # Notebook & 탭 추가
notebook = ttk.Notebook(root)
tab_config = ttk.Frame(notebook)
notebook.add(tab_config, text="Settings")

# 예) 로그 탭
tab_log = ttk.Frame(notebook)
notebook.add(tab_log, text="Data Status")
# ─── 로그 텍스트 그리드 배치 ─────────────────
log_text = scrolledtext.ScrolledText(tab_log, height=15)
log_text.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
# 탭 내부 그리드 확장 설정
tab_log.grid_rowconfigure(0, weight=1)
tab_log.grid_columnconfigure(0, weight=1)

# ─── GUI Messages 탭 정의 & 배치 ─────────────────────────
tree_tab = ttk.Frame(notebook)
tree = ttk.Treeview(tree_tab, columns=("sev","msg","exp"), show="headings")
tree.heading("sev", text="Severity")
tree.heading("msg", text="Message")
tree.heading("exp", text="Explanation")
tree.column("sev", width=80, anchor="center")
tree.column("msg", width=600)
tree.column("exp", width=600)
tree.pack(fill="both", expand=True)
notebook.add(tree_tab, text="Messages")

# 1) StringVar 선언 ― 각 변수는 **한 번만**
_dic_dir       = StringVar()
out_root       = StringVar()
_base_url      = StringVar(value="https://song-jung-il.github.io/Public_image")
_img_base      = StringVar(value="https://raw.githubusercontent.com/SONG-JUNG-IL/Public_image/main")
_dict_file     = StringVar()
_dciodvfy      = StringVar(value=DEFAULT_DCIODVFY) # dciodvfy.exe 절대경로
_dict_enc      = StringVar(value="utf-8")   # 사전 파일 인코딩
_dump_flag     = StringVar(value="off")    # ★ 새 변수: -dump on/off
_privtag_path = StringVar(value="")  # path to temp privtag txt for dciodvfy
_manifest_path = StringVar()
_excel_path    = StringVar()

_pad = {"padx": 4, "pady": 4}

# somewhere after notebook tabs created
frm = ttk.Frame(tab_log); frm.grid(row=1, column=0, sticky="we", padx=6, pady=4)
tv  = ttk.Treeview(frm, columns=("cat","n"), show="headings", height=5)
tv.heading("cat", text="Category"); tv.heading("n", text="Count")
tv.column("cat", width=260); tv.column("n", anchor="e", width=80)
tv.pack(side="left", fill="x", expand=True)
sb = ttk.Scrollbar(frm, orient="vertical", command=tv.yview); sb.pack(side="right", fill="y")
tv.configure(yscrollcommand=sb.set)
tab_log.grid_columnconfigure(0, weight=1)



def update_category_counts_tv(summary=None):
    tv.delete(*tv.get_children())
    if not summary:
        return
    for cat, n in sorted(summary["by_cat"].items(), key=lambda kv: -kv[1]):
        tv.insert("", "end", values=(cat, n))
# ───▶ PASTE: OUTPUT‑PATH UTIL ─────────────────────────────────────────
def _ensure_output_subfolder(base: str | pathlib.Path,
                             manifest_var: tk.StringVar,
                             excel_var: tk.StringVar,
                             prefix: str = "output",
                             ts_fmt: str = "%Y%m%d_%H%M") -> pathlib.Path:
    """
    1) <base>/<prefix>_YYYYMMDD_HHMM 하위폴더를 항상 생성
    2) manifest / excel StringVar 를 해당 폴더 내부 경로로 자동 갱신
    3) 반환값: 최종 하위폴더 Path
    """
    ts      = datetime.now().strftime(ts_fmt)
    sub     = pathlib.Path(base).expanduser() / f"{prefix}_{ts}"
    sub.mkdir(parents=True, exist_ok=True)

    manifest_var.set(str(sub / f"manifest_{ts}.json"))
    excel_var.set(str(sub / f"tags_{ts}.xlsx"))
    return sub
# ───────────────────────────────────────────────────────────────────────



# 2) labels · vars_ · btn_specs  → 인덱스 0-7 동일
labels = ["DICOM Folder", "Output Folder", "Base URL", "Image Base URL",
                  "Tag Dictionary", "dciodvfy.exe", "Manifest File", "Tags Excel"]

vars_  = [
    _dic_dir, out_root, _base_url, _img_base,
    _dict_file, _dciodvfy, _manifest_path, _excel_path
]

btn_specs = [
    ("Browse", lambda: browse(_dic_dir,  "dir",  "DICOM Folder")),   # 0
    ("Browse", lambda: browse(out_root,  "dir",  "Output Folder")),   # 1
    (None,   None),                                             # 2
    (None,   None),                                             # 3
    ("Browse", lambda: browse(_dict_file,"file", "Tag Dictionary")),   # 4
    ("Browse", lambda: browse(_dciodvfy,"file", "DICOM Validation (via dciodvfy)")), # 5 ★
    ("Save", lambda: save_as(_manifest_path,"Manifest File Save",".json")),# 6
    ("Save", lambda: save_as(_excel_path,  "Tags Excel Save",  ".xlsx"))   # 7
]
dump_cb = ttk.Checkbutton(
    root,
    text="Include dciodvfy ‑dump Output",
    variable=_dump_flag,
    onvalue="on", offvalue="off"
)
dump_cb.grid(row=len(labels)+3, column=0, columnspan=3,
             sticky="w", **_pad)

# ─── dump_cb 아래 행 번호 계산 ────────────────────────────────
opts_row = len(labels) + 1

# ─── 옵션 프레임(그리드) ─────────────────────────────────────
frame_opts = ttk.LabelFrame(root, text="Options")
frame_opts.grid(row=opts_row, column=0, columnspan=3,
                sticky="we", pady=6)

# ─── 체크박스 두 개를 같은 줄(왼→오)로 패킹 ────────────────
ttk.Checkbutton(frame_opts,
                text="Include File‑Meta (0002)",
                variable=_include_meta_var
               ).pack(side="left", padx=(4, 12))   # 왼쪽 여백·간격

ttk.Checkbutton(frame_opts,
                text="Export IIIF metadata JSON",
                variable=_export_iiif_var
               ).pack(side="left", padx=4)



# ──────────────────────────────────────────────────────────────────




# 3) 공통 루프 ― 그대로 두면 됨
for i, (lb, v, (btxt, bcmd)) in enumerate(zip(labels, vars_, btn_specs)):
    ttk.Label(tab_config, text=lb).grid(row=i, column=0, sticky="w", **_pad)
    ttk.Entry(tab_config, textvariable=v, width=60).grid(row=i, column=1, **_pad)
    if btxt:
        ttk.Button(tab_config, text=btxt, command=bcmd).grid(row=i, column=2, **_pad)
        # Description tabs

def _ensure_output_subfolder(base: str | pathlib.Path,
                             manifest_var: tk.StringVar,
                             excel_var:    tk.StringVar,
                             prefix: str = "output",
                             ts_fmt: str = "%Y%m%d_%H%M") -> pathlib.Path:
    """
    1) <base>/<prefix>_YYYYMMDD_HHMM 하위 폴더를 항상 생성
    2) 'manifest_var', 'excel_var' 를 해당 폴더 내부 경로로 동기화
    3) 반환값: 최종 하위 폴더 Path
    """
    ts   = datetime.now().strftime(ts_fmt)
    sub  = pathlib.Path(base).expanduser() / f"{prefix}_{ts}"
    sub.mkdir(parents=True, exist_ok=True)       # mkdir -p  :contentReference[oaicite:0]{index=0}

    manifest_var.set(str(sub / f"manifest_{ts}.json"))
    excel_var.set   (str(sub / f"tags_{ts}.xlsx"))
    return sub

def browse(var, mode, title):
    path = (filedialog.askdirectory(title=title)
            if mode == "dir"
            else filedialog.askopenfilename(title=title))
    if not path:
        return

    var.set(path)

    # ▸ Output Folder 선택 시 — 하위 타임스탬프 폴더 고정
    if var is out_root:
        sub = _ensure_output_subfolder(
            base=path,
            manifest_var=_manifest_path,
            excel_var=_excel_path
        )
        out_root.set(str(sub)) 

def save_as(var, title, ext):
    f = filedialog.asksaveasfilename(defaultextension=ext,
                                     filetypes=[(ext.upper(), f"*{ext}")])
    if f:
        var.set(f)

progress = ttk.Progressbar(root, mode="determinate")
progress.grid(row=len(labels), column=0, columnspan=3, sticky="we", **_pad)   # 8
# ── Logbox     (row 10)
logbox = scrolledtext.ScrolledText(root, height=15, width=180, state=DISABLED)
logbox.grid(row=len(labels)+2, column=0, columnspan=3, **_pad)                # 10


# ─── 설명 탭용 Notebook 생성 (여기에 삽입) ─────────────────


#notebook.grid(row=0, column=3, rowspan=len(labels), sticky="nsew", padx=4, pady=4)
notebook.grid(row=0, column=2, rowspan=len(labels)+2, sticky="nsew", padx=10, pady=10)  # config 탭(0,1,2열)에 걸쳐 표시      # config 탭(0,1,2열)에 걸쳐 표시
root.grid_columnconfigure(2, weight=1)
root.grid_rowconfigure(0,    weight=1)

# 한국어 설명 탭
ko_tab = scrolledtext.ScrolledText(notebook, wrap="word", state=DISABLED)
ko_text = (
    "■ 프로그램 개요\n"
    "  • 16-bit DICOM → 8-bit JPEG 변환\n"
    "  • IIIF Presentation-3 manifest.json 자동 생성\n"
    "  • dciodvfy(⁠dicom3tools⁠)로 DICOM 표준 위반·경고 검증\n"
    "  • 변환(CONVERT)·검증(VALIDATE) 단계별 실시간 로그 제공\n"
    "\n"
    "■ dciodvfy 검증 방식\n"
    "  ① GUI에서 dciodvfy.exe 절대경로를 지정하거나, 미지정 시 PATH에서 검색합니다.\n"
    "  ② 각 DICOM 파일을 ▶ dciodvfy <file> 명령으로 호출합니다.\n"
    "     · 오류(E:) 줄 → \"규격 오류\" \n"
    "     · 경고(W:) 줄 → \"규격 경고\"\n"
    "  ③ 결과를 validation_날짜/dciodvfy.jsonl 에 JSON Lines 형식으로 기록합니다.\n"
    "     { \"file\": \"aaa.dcm\", \"exit\": 0, \"errors\":[], \"warnings\":[...]} \n"
    "  ④ GUI 로그 색상\n"
    "       • 초록 OK : 오류·경고 0건\n"
    "       • 주황 WARN : 경고만 존재\n"
    "       • 빨강 ERROR : 오류 1건 이상 또는 dciodvfy 실행 실패\n"
    "  ⑤ dciodvfy.exe 설치: dicom3tools 패키지 압축 해제 후 exe 경로 지정\n"
    
    "\n"
    "■ 사용 순서\n"
    "  1) 필수 경로 : DICOM 폴더 · 출력 폴더 지정\n"
    "  2) (선택) Base URL / Image Base URL 입력\n"
    "  3) (선택) Tag Dictionary + 인코딩 지정(UTF-8/CP949/…)\n"
    "  4) (선택) dciodvfy.exe 선택 → 규격 검증 활성화\n"
    "  5) [실행] 클릭 → 변환 → 검증 → 완료 로그 확인\n"
    "\n"
    
    "■ 출력 구조\n"
    "  <출력 폴더>\n"
    "   ├ images/                 : 8-bit JPEG 파일\n"
    "   ├ manifest_*.json         : IIIF v3 manifest\n"
    "   ├ tags_*.xlsx             : 전체 태그 목록(Excel)\n"
    "   └ validation_*/           : 검증 결과 폴더\n"
    "        ├ dciodvfy.jsonl     : 파일별 오류·경고·덤프 로그 (JSONL)\n"
    "        ├ perf_metrics.xlsx  : 변환 시간·원본·JPEG 크기 통계\n"
    "        └ dciodvfy_report.html : HTML 요약 보고서\n"
    "\n"
    "      ▸ dciodvfy_report.html 내용\n"
    "        · 파일별 Error / Warning 개수 컬러 테이블\n"
    "        · FAQ - 자주 발생하는 메시지 패턴과 해결 가이드\n"
    "        · -dump 옵션을 켜면 태그 덤프 전체도 JSONL에 포함\n"
)

ko_tab.configure(state=tk.NORMAL)
ko_tab.insert("1.0", ko_text)
ko_tab.configure(state=DISABLED)
notebook.add(ko_tab, text="Description (KOR)")

# English 설명 탭
en_tab = scrolledtext.ScrolledText(notebook, wrap="word", state=DISABLED)
en_text = (
    "■ Overview\n"
    "  • Converts 16-bit DICOM images to 8-bit grayscale JPEG\n"
    "  • Generates an IIIF Presentation-3 manifest.json\n"
    "  • Validates each DICOM via dciodvfy (dicom3tools)\n"
    "  • Real-time logs for CONVERT and VALIDATE phases\n"
    "\n"
    "■ dciodvfy validation workflow\n"
    "  1) Specify an absolute path to dciodvfy.exe in the GUI or leave blank ➜ fallback to system PATH.\n"
    "  2) For every file the app runs:  dciodvfy <file>\n"
    "     • lines starting with E:  → counted as *errors*\n"
    "     • lines starting with W:  → counted as *warnings*\n"
    "  3) Validation output is saved to  validation_<date>/dciodvfy.jsonl  (one JSON object per line).\n"
    "  4) GUI color codes:\n"
    "       ✔ green  = no errors / warnings\n"
    "       ⚠ orange = warnings only\n"
    "       ✘ red    = ≥1 error or execution failure\n"
    "  5) Install dciodvfy:\n"
    "       • Windows  :  choco install dicom3tools\n"
    
    "\n"
    "■ Steps\n"
    "  1) Select DICOM folder and Output folder (required)\n"
    "  2) (Opt) Enter Base URL / Image Base URL for IIIF IDs\n"
    "  3) (Opt) Choose Tag Dictionary + file encoding (UTF-8, CP949, …)\n"
    "  4) (Opt) Browse for dciodvfy.exe to enable validation\n"
    "  5) Click [Run]  ➜  Conversion ➜ Validation ➜ Done\n"
    "\n"
    "■ Output layout\n"
    "  <output folder>\n"
    "   ├ images/                 : 8-bit JPEG files\n"
    "   ├ manifest_*.json         : IIIF v3 manifest\n"
    "   ├ tags_*.xlsx             : full tag list (Excel)\n"
    "   └ validation_*/           : validation results\n"
    "        ├ dciodvfy.jsonl     : per-file errors / warnings / dump (JSONL)\n"
    "        ├ perf_metrics.xlsx  : conversion time & original / JPEG size stats\n"
    "        └ dciodvfy_report.html : human-readable HTML summary report\n"
    "\n"
    "      ▸ What’s inside dciodvfy_report.html\n"
    "        · Color-coded table of Error / Warning counts for each file\n"
    "        · FAQ section – common message patterns and troubleshooting tips\n"
    "        · When the –dump option is enabled, full tag dumps are included in the JSONL file\n"
)

en_tab.configure(state=tk.NORMAL)
en_tab.insert("1.0", en_text)
en_tab.configure(state=DISABLED)
notebook.add(en_tab, text="Description (ENG)")




def _append(msg:str, tag:str="info"):
    logbox.config(state=NORMAL)
    if tag not in logbox.tag_names():
        colors = {"warn":"#f0ad4e", "error":"#d9534f", "ok":"#5cb85c"}
        logbox.tag_config(tag, foreground=colors.get(tag, "#5bc0de"))
    logbox.insert(END, msg+"\n", tag)
    logbox.see(END)
    logbox.config(state=DISABLED)

log = lambda m,l="info": root.after(0,_append,m,l)

# ── ▶ NEW: 버튼 이벤트 ───────────────────────────────────
def on_validate():
    dcm_path = filedialog.askopenfilename(
        title="검증할 DICOM 선택", filetypes=[("DICOM", "*.dcm")]
    )
    if not dcm_path:
        return

    # 태그 사전 로드/텍스트화
    global TAG_RECORDS
    if not TAG_RECORDS:
        TAG_RECORDS = load_dict(_dict_file.get())
    tag_txt = write_privtag_txt(TAG_RECORDS)

    _privtag_path.set(tag_txt)
    # 출력 위치 결정: 지정된 Output 폴더가 있으면 그 아래, 없으면 DICOM 상위
    out_base = Path(out_root.get()) if out_root.get() else Path(dcm_path).parent
    run_ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    val_dir  = out_base / f"validation_{run_ts}"
    val_dir.mkdir(parents=True, exist_ok=True)
    (jsonl_path := val_dir / "dciodvfy.jsonl").touch(exist_ok=True)


    def _log(msg):
        logbox.config(state=NORMAL)
        logbox.insert(END, msg + "\n")
        logbox.see(END)
        logbox.config(state=DISABLED)

    # 스트리밍 실행 + jsonl 기록
    res = run_dciodvfy_cli(
        dcm_path=str(dcm_path),
        out_dir=val_dir,
        jsonl_path=jsonl_path,
        cb_log=log,
    )
    try:
        os.remove(tag_txt)
    except Exception:
        pass

    # JSONL → HTML 리포트 생성
    html_path = val_dir / "dciodvfy_report.html"
    write_html_report_from_jsonl(jsonl_path, html_path)
    log(f"[ok] HTML report → {html_path}", "ok")


def _resolveout_root(out_root: Path | str | None, seed: Path | None = None) -> Path:
    """
    out_root None이면 seed(첫 DICOM)의 상위 폴더 아래에 output_<ts>를 만든다.
    seed도 없으면 CWD를 기준으로 생성.
    """
    if out_root:
        return Path(out_root)
    base = seed.parent if isinstance(seed, Path) else Path.cwd()
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    return base / f"output_{ts}"

def write_html_report_from_jsonl(jsonl_path: Path, html_path: Path):
    html_path.parent.mkdir(parents=True, exist_ok=True)

    rows, dumps = [], []                     # 상세/덤프
    by_file = defaultdict(lambda: {"ERR":0, "WARN":0, "PRIV":0})
    by_cat  = Counter()
    by_sev  = Counter()

    if jsonl_path.exists():
        with jsonl_path.open("r", encoding="utf-8") as jf:
            for line in jf:
                try:
                    obj = json.loads(line)
                except Exception:
                    continue

                # ① 덤프 레코드
                if obj.get("dump"):
                    dumps.append(obj)
                    continue

                # ② “messages”: [...] 형태
                if isinstance(obj.get("messages"), list):
                    fname = obj.get("file", "(unknown)")
                    for m in obj["messages"]:
                        # m 예: {"severity":"ERR|WARN|PRIV", "text": "..."}
                        sev = m.get("severity", "")
                        txt = m.get("text", "")
                        norm = parse_vfy_messages(txt)
                        norm["file"] = fname
                        rows.append(norm)

                        s = norm["severity"]
                        by_sev[s] += 1
                        by_cat[norm.get("category","Other")] += 1
                        if s in by_file[fname]:
                            by_file[fname][s] += 1
                    continue

                # ③ 개별 파싱 레코드(예: {"severity","msg",...})
                if obj.get("severity") and obj.get("msg"):
                    norm = {
                        "file": obj.get("file","(unknown)"),
                        **parse_vfy_messages(obj["msg"])
                    }
                    rows.append(norm)
                    s = norm["severity"]
                    by_sev[s] += 1
                    by_cat[norm.get("category","Other")] += 1
                    if s in by_file[norm["file"]]:
                        by_file[norm["file"]][s] += 1

    # --- DEDUP & RE-AGGREGATE (INSERT HERE, just before totals) ---
    rows = _dedup_rows(rows)

    # 집계 초기화 후, 중복 제거된 rows 기준으로 재계산
    by_file.clear(); by_cat.clear(); by_sev.clear()
    for r in rows:
        s  = r.get("severity")
        fn = r.get("file", "(unknown)")
        cat= r.get("category","Other")
        by_sev[s] += 1
        by_cat[cat] += 1
        if fn not in by_file:
            by_file[fn] = {"ERR":0, "WARN":0, "PRIV":0}
        if s in by_file[fn]:
            by_file[fn][s] += 1
    # --------------------------------------------------------------


    # 파일 합계 행(모든 파일 합산)
    totals = {"ERR": sum(v["ERR"] for v in by_file.values()),
              "WARN":sum(v["WARN"]for v in by_file.values()),
              "PRIV":sum(v["PRIV"]for v in by_file.values())}
    totals["TOTAL"] = totals["ERR"] + totals["WARN"] + totals["PRIV"]

    # ── HTML 작성 ───────────────────────────────────────────
    esc = lambda s: (s or "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

    parts = ["""
<!doctype html><html lang="ko"><head><meta charset="utf-8">
<title>DICOM Validation Report</title>
<style>
  :root{--fg:#222;--muted:#666;--err:#d9534f;--warn:#b06c00;--priv:#1e88e5}
  body{font-family:Segoe UI,Arial,sans-serif;color:var(--fg);margin:24px;max-width:1100px}
  h1{margin:.2rem 0 1rem}
  h2{margin:1.2rem 0 .5rem}
  table{border-collapse:collapse;width:100%;margin:.4rem 0 1rem}
  th,td{border:1px solid #ddd;padding:6px 8px}
  thead th{position:sticky;top:0;background:#fafafa}
  tbody tr:nth-child(even){background:#fafafa}
  .num{text-align:right}
  .err{color:var(--err)} .warn{color:var(--warn)} .priv{color:var(--priv)}
  details{border:1px solid #ddd;padding:.4rem .6rem;margin:.6rem 0}
  pre{white-space:pre-wrap;font-family:Consolas,monospace;max-height:420px;overflow:auto}
  .muted{color:var(--muted)}
</style></head><body>
"""]
    parts.append(f"<h1>DICOM Validation Report <small class='muted' style='font-weight:normal'>– {datetime.now():%Y-%m-%d %H:%M}</small></h1>")

    # 1) File Summary
    parts.append("<h2>File Summary</h2>")
    parts.append("<table><thead><tr><th>File</th><th class='num err'>Errors</th><th class='num warn'>Warnings</th><th class='num priv'>Private</th><th class='num'>Total</th></tr></thead><tbody>")
    parts.append(f"<tr><td><b>All files</b></td><td class='num err'><b>{totals['ERR']}</b></td>"
                 f"<td class='num warn'><b>{totals['WARN']}</b></td>"
                 f"<td class='num priv'><b>{totals['PRIV']}</b></td>"
                 f"<td class='num'><b>{totals['TOTAL']}</b></td></tr>")
    for fname, cnt in sorted(by_file.items()):
        t = cnt["ERR"] + cnt["WARN"] + cnt["PRIV"]
        parts.append(f"<tr><td>{esc(fname)}</td>"
                     f"<td class='num err'>{cnt['ERR']}</td>"
                     f"<td class='num warn'>{cnt['WARN']}</td>"
                     f"<td class='num priv'>{cnt['PRIV']}</td>"
                     f"<td class='num'>{t}</td></tr>")
    parts.append("</tbody></table>")

    # 2) Validation summary (grouped)
    parts.append("<h2>Validation summary (grouped)</h2>")
    parts.append("<table><thead><tr><th>Category</th><th class='num'>Count</th></tr></thead><tbody>")
    for cat, n in sorted(by_cat.items(), key=lambda kv: -kv[1]):
        parts.append(f"<tr><td>{esc(cat)}</td><td class='num'>{n}</td></tr>")
    parts.append("</tbody></table>")
    parts.append("<p class='muted'>Severity — "
                 f"ERR: <b>{by_sev.get('ERR',0)}</b>, "
                 f"PRIV: <b>{by_sev.get('PRIV',0)}</b>, "
                 f"WARN: <b>{by_sev.get('WARN',0)}</b></p>")

    # 3) Detailed Messages (FAQ 위로 이동)
    parts.append("<h2>Detailed Messages</h2>")
    parts.append("<table><thead><tr><th>File</th><th>Severity</th><th>Tag</th><th>Message</th><th>Explain</th></tr></thead><tbody>")
    for r in rows:
        sev = r.get("severity","")
        col = {"ERR":"err","WARN":"warn","PRIV":"priv"}.get(sev,"")
        parts.append(
            f"<tr><td>{esc(r.get('file',''))}</td>"
            f"<td class='{col}'>{esc(sev)}</td>"
            f"<td>{esc(r.get('tag') or '')}</td>"
            f"<td>{esc(r.get('msg',''))}</td>"
            f"<td>{esc(r.get('explain',''))}</td></tr>"
        )
    parts.append("</tbody></table>")

    # 4) Full DCIODVFY Dumps (단일 섹션, 중복 제거)
    parts.append("<h2>Full DCIODVFY Dumps (click filename)</h2>")
    if dumps:
        for i, d in enumerate(dumps, 1):
            title = f"{esc(d.get('file','(unknown)'))} — {d.get('ts','')}"
            dump  = esc(d.get("dump",""))
            parts.append(f"<details><summary>{title}</summary><pre>{dump}</pre></details>")
    else:
        parts.append("<p class='muted'><i>No dump captured.</i></p>")

    # 5) FAQ 맨 아래
    parts.append("<h2>FAQ – Error / Warning patterns</h2>")
    parts.append("<table><thead><tr><th>Pattern</th><th>Explanation</th></tr></thead><tbody>")
    for pat, desc in FAQ.items():
        parts.append(f"<tr><td>{esc(pat)}</td><td>{esc(desc)}</td></tr>")
    parts.append("</tbody></table>")

    parts.append("</body></html>")
    html_path.write_text("".join(parts), encoding="utf-8")




# ─────────────── 변환 + 검증 ───────────────
def convert_validate():
    exe_test = find_dciodvfy()
    if not exe_test:
        messagebox.showerror("dciodvfy 확인 실패",
            "dciodvfy.exe 를 찾지 못했습니다.\n경로를 선택하거나 PATH에 추가해 주세요.")
        return

    try:
        # ── 0. 입력 폴더 · URL 검사 ───────────────────────────────
        dcm_root = pathlib.Path(_dic_dir.get())
        if not dcm_root.is_dir():
            raise ValueError("DICOM 폴더를 지정하세요.")
        if urllib.parse.urlparse(_base_url.get()).scheme not in ("http", "https"):
            raise ValueError("Base URL 은 http/https 로 시작해야 합니다.")

        # ── 1. 출력 루트 · 하위 타임스탬프 폴더 보장 ───────────────
        if not out_root.get():                          # CLI 에서 비어있을 수 있음
            out_root.set(str(dcm_root.parent))          # DICOM 폴더 상위로 기본 설정

        # 교체 (덮어쓰기 금지)
# 실행 시작 시 1회
        # --- prevent nested output_<ts>/output_<ts> ---
        base_path = Path(out_root.get() or dcm_root.parent)
        if re.search(r"^output_\d{8}_\d{4,6}$", base_path.name):
            out_dir = base_path
        else:
            out_dir = _ensure_output_subfolder(
                base=base_path,
                manifest_var=_manifest_path,
                excel_var=_excel_path
            )
        out_root.set(str(out_dir))

        # ── 2. 하위 디렉터리 생성 ────────────────────────────────
        # ── 2. 하위 디렉터리 생성 ────────────────────────────────
        # ── 2. 하위 디렉터리 생성 ────────────────────────────────
        # ── 2. 하위 디렉터리 생성 ────────────────────────────────
        ts_all  = datetime.now().strftime("%Y%m%d_%H%M%S")
        img_dir = out_dir / "images"; img_dir.mkdir(parents=True, exist_ok=True)

        # ✅ out_dir → out_root 로 수정 (변수 충돌 제거)
        base_out = Path(out_root.get()) 
        run_ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
        val_dir  = base_out / f"validation_{run_ts}"
        val_dir.mkdir(parents=True, exist_ok=True)

        # ✅ JSONL 미리 생성해 ‘빈 파일’ 문제 예방
        jsonl_path = val_dir / "dciodvfy.jsonl"
        if not jsonl_path.exists():
            jsonl_path.write_text("", encoding="utf-8")



        tag_txt_path = Path(_dict_file.get()) if _dict_file.get() else Path()
        tag_exists   = tag_txt_path.is_file()
        dmap = load_dict(str(tag_txt_path)) if tag_exists else {}
        log(f"[info] Tag Dictionary {len(dmap)} Count" if dmap else "[info] 사전 미사용")

        files = sorted(dcm_root.rglob("*.dcm"))
        if not files:
            raise ValueError(".dcm 파일이 없습니다.")

        # ─── 3. progress 최대값 맞추기 (함수 초반) ──────────────────
        root.after(0, lambda: progress.config(maximum=len(files), value=0))
        root.config(cursor="watch")

        tag_rows, perf_rows, canvases = [], [], []
         # 1) ❶ 설명을 쌓을 버퍼 ────────────────  # ← NEW
        explained_messages: list[dict] = []         # ← NEW
        
        for idx, dcm_path in enumerate(files, 1):
            try:
                t0 = perf_counter()
                ds = pydicom.dcmread(dcm_path, force=True)

                # ── ▶ FIX: file_meta / Transfer Syntax UID 보완 ──
                if not getattr(ds, "file_meta", None):
                    ds.file_meta = pydicom.Dataset()          # 빈 file_meta 생성
                if not getattr(ds.file_meta, "TransferSyntaxUID", None):
                    ds.file_meta.TransferSyntaxUID = pydicom.uid.ExplicitVRLittleEndian
                    # 필요한 경우: ds.is_implicit_VR, ds.is_little_endian 조정
                # -------------------------------------------------------------
                # ── ▶ NEW: SC IOD 경고 자동 보정(ISO_IR 192, SECONDAR, Type2/2C, IPP 등)
                try:
                    normalize_for_sc(ds)
                except Exception:
                    pass
                arr = ds.pixel_array if "PixelData" in ds else None
                jpg_name, h, w = "", 0, 0

                # ─── 이미지 저장 ─────────────────
                if arr is not None:
                    if ds.get("Modality")=="CT":
                        hu = apply_modality_lut(arr, ds)
                        level = float(ds.get("WindowCenter", WINDOW_DEFAULT[0]))
                        width = float(ds.get("WindowWidth",  WINDOW_DEFAULT[1]))
                        img8 = window_level(hu, level, width)
                    else:
                        denom = np.ptp(arr) or 1
                        img8 = ((arr-arr.min())/denom*255).astype(np.uint8)

                    jpg_name = f"{dcm_path.stem}.jpg"
                    Image.fromarray(img8).convert("L").save(img_dir/jpg_name, quality=90)
                    h, w = img8.shape

                # ─── Canvas 메타데이터 ───────────────
                md = build_canvas_metadata(ds, dmap,
                           include_meta=_include_meta_var.get())

                tag_rows.extend([
                    {
                        "file":    dcm_path.name,
                        "group":   f"{m['group']:04X}",      # ← 수정: 0002, 0010 …
                        "element": f"{m['element']:04X}",    # 기존 유지
                        "vr":      m["vr"],
                        "level":   m["level"],
                        "type":    m["type"],
                        "keyword": m["label"]["none"][0],
                        "value":   m["value"]["none"][0],
                    }
                    for m in md
                ])

                base = _base_url.get().rstrip("/")
                canvas_id = f"{base}/canvas/{idx}"
                page_id   = f"{base}/page/{idx}"
                anno_id   = f"{base}/anno/{idx}"
                body_id   = (f"{_img_base.get().rstrip('/')}/{jpg_name}" if _img_base.get()
                             else f"{base}/images/{jpg_name}") if jpg_name else ""

                canvases.append({
                    "id": canvas_id, "type": "Canvas", "height": h, "width": w,
                    "items": [{
                        "id": page_id, "type": "AnnotationPage",
                        "items": [{
                            "id": anno_id, "type": "Annotation",
                            "motivation": "painting",
                            "body": {"id": body_id, "type": "Image",
                                     "format": "image/jpeg",
                                     "height": h, "width": w},
                            "target": canvas_id
                        }]
                    }],
                    "metadata": md
                })

                t_img = perf_counter() - t0

                # ─── 규격 검증 ──────────────────────

                # ── ▶ NEW: 보정본으로 검증 (원본 보존). 실패 시 원본으로 폴백
                norm_path = val_dir / f"{Path(dcm_path).stem}_norm.dcm"

                try:
                    # --- 저장 직전 안전망: SpecificCharacterSet을 단일값 'ISO_IR 192'로 강제 ---
                    scs = getattr(ds, "SpecificCharacterSet", None)
                    if isinstance(scs, (list, tuple)):
                        tokens = [str(x).strip() for x in scs if str(x).strip()]
                        if len(tokens) == 2 and tokens[0].upper() == "ISO_IR" and tokens[1] == "192":
                            ds.SpecificCharacterSet = "ISO_IR 192"
                    elif isinstance(scs, str):
                        s = scs.replace("_"," ").strip()
                        if s in ("ISO IR 192", "ISO IR192", "ISOIR 192"):
                            ds.SpecificCharacterSet = "ISO_IR 192"


                    ds.save_as(norm_path, write_like_original=False)


                    target_for_validate = norm_path


                except Exception:


                    target_for_validate = dcm_path
                # ★ 검증 실행 (이 줄이 있어야 v_res가 존재합니다)
                # 보정본이 저장되어 있으면 그걸, 아니면 원본을 검증
                v_res = run_dciodvfy(dcm=Path(target_for_validate), output_dir=val_dir)

                
                # JSONL: 덤프 1줄 + 메시지 행들
                dump_text = ""
                try:
                    dump_file = v_res.get("files", {}).get("dump")
                    if dump_file:
                        dump_text = Path(dump_file).read_text(encoding="utf-8")
                except Exception:
                    pass

                append_jsonl(jsonl_path, {"file": dcm_path.name,
                                        "ts": datetime.now().strftime("%Y%m%d_%H%M%S"),
                                        "dump": dump_text})

                msgs = []
                for sev, arr in (("ERR", v_res.get("errors", [])),
                                ("WARN", v_res.get("warnings", [])),
                                ("PRIV", v_res.get("priv_warns", []))):
                    for t in arr:
                        msgs.append({"severity": sev, "text": t})

                try:
                    append_jsonl(jsonl_path, {"file": dcm_path.name,
                                            "exit": v_res.get("exit"),
                                            "messages": msgs})
                    log(f"[ok] JSONL append → {jsonl_path}", "ok")
                except Exception as ex_jsonl:
                    log(f"[warn] JSONL 기록 실패: {ex_jsonl}", "warn")


                # ③ 해설 파싱은 실패해도 진행 (레코드 단위 try/except)
                for m in msgs:
                    try:
                        explained_messages.append(parse_vfy_messages(m["text"]))
                    except Exception as _:
                        explained_messages.append({"severity": m["severity"], "msg": m["text"],
                                                "category": "General", "tag": None, "explain": ""})

                # JSONL append (파일이 없어도 자동 생성)
         

                    except Exception as ex_jsonl:
                        log(f"[warn] JSONL 기록 실패: {ex_jsonl}", "warn")

            finally:
                root.after(0, lambda v=progress["value"]+1: progress.config(value=v))

        # ─── Manifest & Excel 저장 ───────────────
        manifest = {
            "@context":"https://iiif.io/api/presentation/3/context.json",
            "id": _manifest_path.get(), "type":"Manifest",
            "label": lang_map("DICOCH DICOM Study","en"),
            "summary": lang_map(datetime.now().isoformat(),"en"),
            "items": canvases,
            "metadata": summary_stats(tag_rows)
        }
        # === SAVE: manifest to its own folder under output ===
        # base_out : output_{ts} (이미 코드 상단에서 계산되어 있음)
        ts_short = ts_all[:13]  # 예: 20250824_0239
        manifest_folder = f"manifest_{ts_short}"
        manifest_name   = f"manifest_{ts_short}.json"
        mpath = save_json_in_folder(base_out, manifest_folder, manifest_name, manifest)
        log(f"[ok] manifest → {mpath}", "ok")


        # JSONL → 단일 HTML 리포트 생성 (하나만 생성)
        html_path = val_dir / "dciodvfy_report.html"
        write_html_report_from_jsonl(jsonl_path, html_path)
        # Populate GUI Messages tab
        populate_treeview(explained_messages)
        log(f"[ok] HTML report → {html_path}", "ok")

        # ─── Excel outputs ───────────────────────────────────────────
        # output 루트: base_out  (이미 상단에서 output_{시각}으로 설정되어 있음)
        # 실행 타임스탬프 예: 20250824_023951
        ts_short   = ts_all[:13]                 # 20250824_0239
        tags_dir   = ensure_dir(base_out / f"tags_{ts_short}")
        out_xlsx   = tags_dir / f"tags_{ts_short}.xlsx"

        df_tags  = pd.DataFrame(tag_rows)
        df_tags['group']   = df_tags['group'].astype(str).str.upper().str.zfill(4)
        df_tags['element'] = df_tags['element'].astype(str).str.upper().str.zfill(4)
        df_tags.to_excel(out_xlsx, index=False)

        # openpyxl 후처리 그대로
        from openpyxl import load_workbook
        wb = load_workbook(out_xlsx)
        for col in ('B','C'):                 # B=group, C=element – 실제 열 인덱스 확인
            for cell in wb.active[col]:
                cell.number_format = '@'      # Excel 'Text' 형식
        wb.save(out_xlsx)

        log(f"[ok] tags.xlsx → {out_xlsx}", "ok")

        # 성능 리포트는 기존대로 validation 폴더에 저장 (원하면 동일 패턴으로 폴더화 가능)
        pd.DataFrame(perf_rows).to_excel(val_dir / "perf_metrics.xlsx", index=False)
        log(f"[ok] perf_metrics.xlsx → {val_dir/'perf_metrics.xlsx'}", "ok")
        # ─── IIIF metadata JSON ──────────────────────────────────────
        if _export_iiif_var.get():
            iiif_folder = f"iiif_metadata_{ts_all}"
            iiif_name   = f"iiif_metadata_{ts_all}.json"
            iiif_path   = save_json_in_folder(base_out, iiif_folder, iiif_name, canvases)
            log(f"[ok] iiif_metadata.json → {iiif_path}", "ok")



    except Exception as e:
        log(f"[error] {e}", "error")
        messagebox.showerror("Error", str(e))
    finally:
        root.after(0, lambda: (progress.config(value=0), root.config(cursor="")))
        log("[info] Tag Processing Result")

# ─── build_html_report ─────────────────────────
def build_html_report(rows: list[dict], out_path: Path):
    with out_path.open("a", encoding="utf-8") as hp:
        hp.write("<h2>Detailed Messages</h2><table><tr><th>Severity</th><th>Message</th><th>Explain</th></tr>")
        for r in rows:
            color = {"ERR":"#d9534f","WARN":"#f0ad4e","PRIV":"#5bc0de"}.get(r['severity'],"black")
            hp.write(
                f"<tr><td style='color:{color}'>{r['severity']}</td>"
                f"<td>{r['msg']}</td><td>{r['explain']}</td></tr>"
            )
        hp.write("</table></body></html>")

# ─── GUI Messages population ─────────────────────────
def populate_treeview(rows: list[dict]):
    tree.delete(*tree.get_children())
    for r in rows:
        tree.insert('', 'end', values=(r['severity'], r['msg'], r['explain']))

# ── [RUN & VALIDATE 버튼 영역] ─────────────────────────
TAG_RECORDS = {}            # 템플릿 캐시 (전역)

btn_frame = ttk.Frame(root)
btn_frame.grid(row=999, column=0, columnspan=3, sticky="we", **_pad)
#  └─ row=999 : 다른 row 번호와 충돌 방지용 임의의 큰 값

run_btn = ttk.Button(
    btn_frame, text="RUN",
    command=lambda: threading.Thread(
        target=convert_validate, daemon=True).start()
)
run_btn.grid(row=0, column=0, padx=4)


# ───────────────────────────────────────────────────────



root.mainloop()