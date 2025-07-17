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
import os, ctypes, json, pathlib, re, subprocess, threading, urllib.parse
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
        exe = Path(p) / "dciodvfy.exe"
        if exe.is_file():
            return exe
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



# ─── dciodvfy 절대 경로(필요 시 수정) ─────────────────────────
#DCIODVFY = r"C:\\Users\\USER\\Desktop\\논문_ 코드20250702\\2.DICOM to JPEG _ tags_IIIF manifest converter\\2.DICOM to JPEG _ tags_IIIF manifest converter\\20250707\\dicom3tools\\dciodvfy.exe"
# 예시 경로이므로 .exe 가 정확히 존재하는 위치로 수정
# ------------------------------------------------------------------

def run_dciodvfy(dcm: Path) -> dict:
    exe = find_dciodvfy()
    if not exe:
        raise RuntimeError("dciodvfy.exe not found – 경로를 지정하거나 PATH에 추가하세요!")

    cmd = [str(exe), short_path(str(dcm))]
    if _dump_flag.get() == "on":
        cmd.insert(1, "-dump")

    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=30)

    out    = proc.stdout + proc.stderr
    errors = [l for l in out.splitlines() if l.startswith("E:") or "Abort" in l]

    warns_all  = [l for l in out.splitlines() if l.startswith("W:")]
    priv_warns = [w for w in warns_all
                  if "Unrecognized tag" in w and "(0x0013," in w]     # DICOCH 전용
    warns      = [w for w in warns_all if w not in priv_warns]

    return {
        "exit": proc.returncode,
        "errors": errors,
        "warnings": warns,
        "priv_warns": priv_warns,
        "dump": out if _dump_flag.get() == "on" else ""
    }

# ─── FAQ 패턴 → 설명 맵 ───
FAQ = {
    r"Unrecognized tag.*0x0013": "DICOCH Private-Tag – Creator Tag가 있으면 정상",
    r"MediaStorageSOPInstanceUID .* missing SOPInstanceUID":
        "Dataset(0008,0018) UID 누락 – file_meta UID 복사 또는 새 UID 발급",
    r"MediaStorageSOPClassUID .* missing SOPClassUID":
        "Dataset(0008,0016) SOPClassUID 누락 – CT/OT 등 올바른 UID 기입",
    r"Missing attribute .* Patient ID":
        "Patient ID(0010,0020) 없음 – 최소 식별자 입력 권장",
    r"Value dubious .* Person Name":
        "PN 값은 '^' 구분 표준형 권장(성^이름^중간이름)",
}

def parse_vfy_messages(msg: str) -> dict:
    """
    dciodvfy 한 줄을 dict 형태로 변환 + FAQ 매칭
    반환: {'severity':'WARN/PRIV/ERR', 'msg':msg, 'explain':ex or ''}
    """
    sev = ("ERR" if msg.startswith("E:") else
           "PRIV" if "(0x0013," in msg else "WARN")
    expl = ""
    for pat, ans in FAQ.items():
        if re.search(pat, msg):
            expl = ans; break
    return {"severity": sev, "msg": msg, "explain": expl}


    
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


def load_dict(path: str) -> Dict[Tuple[int, int], Dict[str, str]]:
    if not path:                     # 경로 비어있으면 바로 빈 dict
        return {}
    p = pathlib.Path(path)
    records: Dict[Tuple[int, int], Dict[str, str]] = {}

    try:
        suf = p.suffix.lower()
        # ───────── CSV / TSV ─────────
        if suf in (".csv", ".tsv"):
            df = pd.read_csv(p, sep="," if suf==".csv" else "\t",
                             encoding=None, engine="python", on_bad_lines="skip")
        # ───────── XLSX / XLS ─────────
        elif suf in (".xlsx", ".xls"):
            df = pd.read_excel(p)
        # ───────── JSON ─────────
        elif suf == ".json":
            text = read_text_autodetect(p, _dict_enc.get() or None)
            data = json.loads(text)
            for k, v in data.items():
                g, e = int(k[1:5], 16), int(k[6:10], 16)
                records[(g, e)] = {"keyword": v["keyword"], "vr": v["vr"],
                                   "desc": v.get("description", "")}
            df = None
        # ───────── TXT ─────────
        else:                         # 일반 txt (dciodvfy dump)
            text = read_text_autodetect(p, _dict_enc.get() or None)
            lines = text.splitlines()
            df = pd.DataFrame([
                {"group": m["grp"], "element": m["elm"],
                 "vr": m["vr"], "keyword": m["keyword"]}
                for line in lines if (m := TXT_RE.match(line.strip()))
            ])
        # ───────── DataFrame → dict ─────────
        if df is not None:
            for r in df.itertuples():
                g, e = int(r.group, 16), int(r.element, 16)
                records[(g, e)] = {"keyword": r.keyword, "vr": r.vr,
                                   "desc": getattr(r, "description", "")}

    except Exception as ex:
        messagebox.showerror("사전 로드 오류", str(ex))
        return {}

    # pydicom datadict 확장
    for (g, e), meta in records.items():
        tag = (g << 16) | e
        try:
            (pydicom.datadict.add_private_dict_entry if g % 2 else
             pydicom.datadict.add_dict_entry)(tag, meta["vr"], meta["keyword"], meta["desc"])
        except Exception:
            pass
    return records


def build_canvas_metadata(ds: pydicom.Dataset, dmap):
    md = []
    for elem in ds.iterall():
        if elem.tag == (0x7FE0, 0x0010) or elem.VR in IGNORE_VR:
            continue
        g, e = elem.tag.group, elem.tag.element
        kw = dmap.get((g, e), {}).get("keyword") or elem.keyword or f"({g:04X},{e:04X})"
        md.append({"label": lang_map(kw),
                   "value": lang_map(str(elem.value)[:1024])})
    return md

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


root = tk.Tk()
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
_out_dir       = StringVar()
_base_url      = StringVar(value="https://song-jung-il.github.io/Public_image")
_img_base      = StringVar(value="https://raw.githubusercontent.com/SONG-JUNG-IL/Public_image/main")
_dict_file     = StringVar()
_dciodvfy      = StringVar(value=DEFAULT_DCIODVFY) # dciodvfy.exe 절대경로
_dict_enc      = StringVar(value="utf-8")   # 사전 파일 인코딩
_dump_flag     = StringVar(value="off")    # ★ 새 변수: -dump on/off
_manifest_path = StringVar()
_excel_path    = StringVar()

_pad = {"padx": 4, "pady": 4}




# 2) labels · vars_ · btn_specs  → 인덱스 0-7 동일
labels = ["DICOM Folder", "Output Folder", "Base URL", "Image Base URL",
                  "Tag Dictionary", "dciodvfy.exe", "Manifest File", "Tags Excel"]

vars_  = [
    _dic_dir, _out_dir, _base_url, _img_base,
    _dict_file, _dciodvfy, _manifest_path, _excel_path
]

btn_specs = [
    ("Browse", lambda: browse(_dic_dir,  "dir",  "DICOM Folder")),   # 0
    ("Browse", lambda: browse(_out_dir,  "dir",  "Output Folder")),   # 1
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
dump_cb.grid(row=len(labels)+1, column=0, columnspan=3,
             sticky="w", **_pad)


# 3) 공통 루프 ― 그대로 두면 됨
for i, (lb, v, (btxt, bcmd)) in enumerate(zip(labels, vars_, btn_specs)):
    ttk.Label(tab_config, text=lb).grid(row=i, column=0, sticky="w", **_pad)
    ttk.Entry(tab_config, textvariable=v, width=60).grid(row=i, column=1, **_pad)
    if btxt:
        ttk.Button(tab_config, text=btxt, command=bcmd).grid(row=i, column=2, **_pad)
        # Description tabs



def browse(var, mode, title):
    path = (filedialog.askdirectory(title=title)
            if mode=="dir" else
            filedialog.askopenfilename(title=title))
    if path:
        var.set(path)
        if var is _out_dir:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            _manifest_path.set(str(pathlib.Path(path)/f"manifest_{ts}.json"))
            _excel_path.set(str(pathlib.Path(path)/f"tags_{ts}.xlsx"))

def save_as(var, title, ext):
    f = filedialog.asksaveasfilename(defaultextension=ext,
                                     filetypes=[(ext.upper(), f"*{ext}")])
    if f:
        var.set(f)



#for i,(lb,v,(btxt,bcmd)) in enumerate(zip(labels, vars_, btn_specs)):
#    ttk.Label(root, text=lb).grid(row=i, column=0, sticky="w", **_pad)
#    ttk.Entry(root, textvariable=v, width=60).grid(row=i, column=1, **_pad)
#    if btxt:
#        ttk.Button(root, text=btxt, command=bcmd).grid(row=i, column=2, **_pad)
        
        

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

# ─────────────── 변환 + 검증 ───────────────
def convert_validate():
    exe_test = find_dciodvfy()
    if not exe_test:
        messagebox.showerror("dciodvfy 확인 실패",
            "dciodvfy.exe 를 찾지 못했습니다.\n경로를 선택하거나 PATH에 추가해 주세요.")
        return

    # ──── 이제 기존 Frame·Button 등을 tab_config 안에서 배치 ────
    run_btn = ttk.Button(tab_config,  text="RUN", command=lambda: threading.Thread(target=convert_validate, daemon=True).start())
    run_btn.grid(row=len(labels) + 1, column=0, columnspan=3, sticky="we", padx=4, pady=5)
    try:
        dcm_root = pathlib.Path(_dic_dir.get())
        out_root = pathlib.Path(_out_dir.get())
        if not dcm_root.is_dir():
            raise ValueError("DICOM 폴더를 지정하세요.")
        if urllib.parse.urlparse(_base_url.get()).scheme not in ("http","https"):
            raise ValueError("Base URL 은 http/https 로 시작해야 합니다.")

        ts_all = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_root.mkdir(parents=True, exist_ok=True)
        img_dir = out_root / "images"; img_dir.mkdir(exist_ok=True)
        val_dir = out_root / f"validation_{ts_all}"; val_dir.mkdir(exist_ok=True)

        if not _manifest_path.get():
            _manifest_path.set(str(out_root/f"manifest_{ts_all}.json"))
        if not _excel_path.get():
            _excel_path.set(str(out_root/f"tags_{ts_all}.xlsx"))

        dmap = load_dict(_dict_file.get())
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
                md = build_canvas_metadata(ds, dmap)
                tag_rows.extend([
                    {"file": dcm_path.name,
                     "keyword": m["label"]["none"][0],
                     "value":   m["value"]["none"][0]}
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
                v_res = run_dciodvfy(dcm_path)
                                # 2) ❷ 경고·Priv-경고 → 해설 붙여 누적  # ← NEW
                # ─── 2. explained_messages 누적 라인 교체 ───────────────────
                explained_messages.extend(
                    parse_vfy_messages(m)
                    for m in (v_res["errors"] + v_res["warnings"] + v_res["priv_warns"])
                )
                                  # ← NEW
                with open(val_dir/'dciodvfy.jsonl', 'a', encoding='utf-8') as jf:
                    jf.write(json.dumps({"file": dcm_path.name, **v_res}, ensure_ascii=False) + "\n")

                perf_rows.append({
                    "file": dcm_path.name,
                    "time_s": round(t_img, 3),
                    "orig_kB": dcm_path.stat().st_size//1024,
                    "jpeg_kB": (img_dir/jpg_name).stat().st_size//1024 if jpg_name else 0,
                    "errors": len(v_res["errors"]),
                    "warnings": len(v_res["warnings"])
                })

                msg = f"[ok] {dcm_path.name} Converter"
                if v_res["exit"] is None:
                    msg += " (dciodvfy 미설치)"
                elif v_res["errors"]:
                    msg += f" • 규격 오류 {len(v_res['errors'])}건"
                    log(msg, "warn")
                elif v_res["warnings"]:
                    msg += f" • 규격 경고 {len(v_res['warnings'])}건"
                    log(msg, "warn")
                else:
                    log(msg, "ok")

            except Exception as ex:
                log(f"[Error] {dcm_path.name}: {ex}", "Error")
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
        pathlib.Path(_manifest_path.get()).write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        log(f"[ok] manifest → {_manifest_path.get()}","ok")

        # ─── HTML report: header, summary, dump toggle ─────────────────
        html_path = val_dir / "dciodvfy_report.html"
        with html_path.open("w", encoding="utf-8") as hp:
            hp.write(
                "<html><head><meta charset='utf-8'><title>DICOM Validation Report</title>"
                "<style>"
                "body{font-family:Arial;} table{border-collapse:collapse;}"
                "th,td{border:1px solid #ccc;padding:4px;}"
                ".err{color:#d9534f}.warn{color:#f0ad4e}"
                "details{margin:8px 0;border:1px solid #ccc;padding:4px;}"
                "summary{cursor:pointer;font-weight:bold;}"
                "</style></head><body>"
            )
            hp.write(f"<h1>DICOM Validation Report – {datetime.now():%Y-%m-%d %H:%M}</h1>")
            # Summary table
            hp.write("<h2>File Summary</h2><table><tr><th>File</th><th>Errors</th><th>Warnings</th></tr>")
            for r in perf_rows:
                hp.write(
                    f"<tr><td>{r['file']}</td><td class='err'>{r['errors']}</td>"
                    f"<td class='warn'>{r['warnings']}</td></tr>"
                )
            hp.write("</table>")
            # Dump toggle script
            hp.write(
                "<h2>Full DCIODVFY Dumps (click filename)</h2>"
                "<script>function toggleDump(id){var e=document.getElementById(id);"
                "e.style.display = (e.style.display==='none')?'block':'none';}</script>"
            )
            # Dump contents
            idx = 0
            with open(val_dir/'dciodvfy.jsonl', encoding='utf-8') as jf:
                for line in jf:
                    obj = json.loads(line)
                    dump = obj.get("dump", "").strip()
                    if not dump: continue
                    idx += 1
                    dump_id = f"dump{idx}"
                    safe = dump.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
                    hp.write(
                        f"<p><a href=javascript:toggleDump('{dump_id}')>{obj['file']}</a></p>"
                        f"<pre id='{dump_id}' style='display:none;white-space:pre-wrap;font-size:12px;'>" + safe + "</pre>"
                    )
            # FAQ section
            hp.write("<h2>FAQ – Error / Warning patterns</h2><table><tr><th>Pattern</th><th>Explanation</th></tr>")
            for pat, desc in FAQ.items():
                hp.write(f"<tr><td>{pat}</td><td>{desc}</td></tr>")
            hp.write("</table>")
        # End of with -> file closed here

        # ─── Detailed messages append via helper ──────────────────────
        build_html_report(explained_messages, html_path)
        # Populate GUI Messages tab
        populate_treeview(explained_messages)
        log(f"[ok] HTML report → {html_path}", "ok")

        # ─── Excel outputs ───────────────────────────────────────────
        pd.DataFrame(tag_rows).to_excel(_excel_path.get(), index=False)
        log(f"[ok] tags.xlsx → {_excel_path.get()}", "ok")
        pd.DataFrame(perf_rows).to_excel(val_dir/"perf_metrics.xlsx", index=False)
        log(f"[ok] perf_metrics.xlsx → {val_dir/'perf_metrics.xlsx'}", "ok")

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


 # ─── 실행 버튼(grid 사용) ─────────────────────────
run_btn = tk.Button(
     root,
     text="RUN",
     command=lambda: threading.Thread(
         target=convert_validate, daemon=True
).start()
)
# row 인덱스는 progress bar 아래로, labels 개수 기반으로 설정
run_btn.grid(
row=len(labels) + 4,    # progress가 row=len(labels)이므로, +2 하면 바로 다음 줄
column=0,
columnspan=3,
sticky="we",
padx=4, pady=5
)
root.mainloop()
