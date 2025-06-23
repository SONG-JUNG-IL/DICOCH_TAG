# -*- coding: utf-8 -*-
"""
DICOCH DICOM Converter – GUI Edition (v2.9s · 2025-06-20)
──────────────────────────────────────────────────────────
◎ RescaleSlope / Intercept: 엑셀 우선, 누락 시 GUI 값 보완
◎ IIIF 뷰어 자동 호출 옵션(Manifest URL 추출/수동 입력)
◎ 변환 완료 후 태그 결과 전용 로그 창 제공
◎ “태그 결과 저장” 버튼: TXT·XLSX 동시 저장, 파일명 tag_info_YYYYMMDD_HHMMSS
◎ Creator 8 슬롯(0013,0010–0017) 자동 삽입·사전 등록
◎ UR→UT, 빈 SQ prune, UTF-8, ThreadPoolExecutor, 실시간 로그 유지
"""
from __future__ import annotations
import json, os, re, threading, concurrent.futures as cf, sys, subprocess, webbrowser
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Set

import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext, messagebox

import numpy as np
import pandas as pd
import pydicom
from pydicom.dataset import Dataset, FileDataset
from pydicom.sequence import Sequence
from pydicom.uid import ExplicitVRLittleEndian, SecondaryCaptureImageStorage, generate_uid
from pydicom.datadict import add_private_dict_entry
from PIL import Image
import tifffile as tiff

# ── 상수 ───────────────────────────────────────────────
_NUM_RE  = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")
MAX_UT   = 0xFFFFFFFE
GROUP_HEX = "0013"
VR_DOWN  = {"UR": "UT"}          # UR→UT
CREATORS = [
    "DICOCH","DICOCH_HeritageMetaSeq", "DICOCH_GrayCalSeq", "DICOCH_ROIGraySeq", "DICOCH_HUCalSeq",
    "DICOCH_ROIHUSeq", "DICOCH_IIIFLinkSeq", "DICOCH_SecuritySeq",
    "DICOCH_PrivTagSeq",
]

MIRADOR_DEMO = "https://projectmirador.org/demo/?manifest="

# ── VR 보정 ────────────────────────────────────────────
def _num(s, d="0"): 
    m=_NUM_RE.search(str(s)); return m.group(0) if m else d
def _fix_cs(v): return str(v).upper().replace(" ", "_")[:16]
def _fix_da(v): return re.sub(r"\D", "", str(v))[:8]
def _fix_tm(v): return (re.sub(r"\D", "", str(v)) + "000000")[:6]

VR_RULES: Dict[str, Callable[[str], object]] = {
    "CS": _fix_cs, "PN": lambda v: str(v)[:64], "SH": lambda v: str(v)[:16],
    "LO": lambda v: str(v)[:64], "UT": lambda v: v,
    "DS": lambda v: _num(v)[:16], "IS": lambda v: str(int(float(_num(v)))),
    "US": lambda v: int(float(_num(v))), "FL": lambda v: float(_num(v)),
    "FD": lambda v: float(_num(v)), "OW": lambda v: v,
    "DA": _fix_da, "TM": _fix_tm,
}

def safe_value(vr: str, val):
    vr_eff = VR_DOWN.get(vr, vr)
    if vr_eff == "UT":
        if not val: return ""
        if len(str(val).encode("utf-8")) > MAX_UT:
            raise ValueError("UT too long")
        return str(val)
    try:  
        return VR_RULES[vr_eff](val)
    except Exception: 
        return ""

# ── Creator 삽입 & 사전 등록 ───────────────────────────
# ---------- Creator helpers (루트·SQ 전용) ----------
def insert_all_creators(ds: Dataset):
    """루트 Dataset에 8개 Creator 전부 삽입"""
    for slot, name in enumerate(CREATORS):
        ds.add_new((0x0013, 0x0010 + slot), "LO", name)

def insert_block_creator(ds: Dataset, block_hex: str):
    """SQ-Item 안에 DICOCH + 해당 블록 Creator 한 줄만 삽입"""
    ds.add_new((0x0013, 0x0010), "LO", "DICOCH")      # 기본 Creator
    bb = int(block_hex, 16) - 0x10                    # 0x11→1 … 0x17→7
    if 0 <= bb < len(CREATORS):
        ds.add_new((0x0013, 0x0010 + bb), "LO", CREATORS[bb])

def register_private_tags(df: pd.DataFrame):
    for slot, name in enumerate(CREATORS):
        add_private_dict_entry(name, 0x00130010 + slot, "LO", name, "1")
    for _, r in df.iterrows():
        if r.Element == "0010": 
            continue
        tag_int = (int(GROUP_HEX,16)<<16) | int(r.Element,16)
        slot = max(0, min(7, int(r.Element[:2],16)-0x10))
        add_private_dict_entry(
            CREATORS[slot], tag_int,
            VR_DOWN.get(r.VR, r.VR),
            r.Keyword or f"DICOCH_{r.Element}", "1"
        )

# ── dicom.dic ─────────────────────────────────────────
def write_dic(df: pd.DataFrame, out_dir: Path):
    out_dic  = out_dir / "dicom.dic"
    lines=[]
    for _, r in df.iterrows():
        vr = VR_DOWN.get(r.VR, r.VR)
        kw = r.Keyword or f"DICOCH_{r.Element}"
        lines.append(f"({r.Group},{r.Element}) {vr} 1 {kw}")
    out_dic.write_text("\n".join(lines), encoding="utf-8")
    return out_dic

# ── 태그 로더 ──────────────────────────────────────────
def _parse_elem(e):
    if pd.isna(e) or str(e).strip() == "":
        return ""
    s=str(e).strip()
    return s.zfill(4).upper() if re.fullmatch(r"[0-9A-Fa-f]{1,4}",s) else f"{int(float(s)):04X}"

def load_tags(xlsx: Path) -> pd.DataFrame:
    df = pd.read_excel(xlsx, dtype=str).fillna("")
    df["Group"]    = df["Group"].str.zfill(4).str.upper()
    df["Element"]  = df["Element"].apply(_parse_elem)
    df["ParentTag"]= df["ParentTag"].str.upper()
    df["ItemIndex"]= df["ItemIndex"].replace("", "0")
    df.loc[df.VR=="DA","Value"] = df.loc[df.VR=="DA","Value"].apply(_fix_da)
    df.loc[df.VR=="TM","Value"] = df.loc[df.VR=="TM","Value"].apply(_fix_tm)
    prune = (df.VR=="SQ") & (~df.apply(lambda r:(df.ParentTag==r.Group+r.Element).any(),axis=1))
    df = df[~prune].reset_index(drop=True)
    register_private_tags(df)
    return df

# ── Sequence 재귀 ────────────────────────────────────
# ── Sequence 재귀 ─────────────────────────────────
def build_sequence(root: str,
                   df: pd.DataFrame,
                   seen: Set[str] | None = None,
                   depth: int = 0) -> Sequence:
    """
    Excel 트리 구조(df) → pydicom Sequence 재귀 생성
    * root        : 상위 SQ 태그(‘00131100’ 등) 8자리 문자열
    * seen        : 순환 방지용 방문 집합
    * depth       : 최대 20단계
    """
    if seen is None:
        seen = set()
    if root in seen or depth > 20:
        return Sequence([])

    seen.add(root)
    items = []

    # ItemIndex(0,1,2…) 별로 그룹핑
    for _, rows in df[df.ParentTag == root].groupby("ItemIndex"):
        it = Dataset()

        # 🔄 변경: Item마다 해당 Block Creator 한 줄만 삽입
        block_hex = root[4:6]           # ‘11’, ‘12’, … (상위 1byte)
        insert_block_creator(it, block_hex)

        # 기존: insert_creators(it)  ← 삭제했습니다

        # 태그 삽입
        for _, r in rows.iterrows():
            tag = (int(r.Group, 16), int(r.Element, 16))
            if r.VR == "SQ":                           # 하위 Sequence 재귀
                seq = build_sequence(r.Group + r.Element,
                                      df, seen.copy(), depth + 1)
                if len(seq):
                    it.add_new(tag, "SQ", seq)
            else:                                      # 일반 태그
                it.add_new(tag,
                           VR_DOWN.get(r.VR, r.VR),
                           safe_value(r.VR, r.Value))

        if len(it):
            items.append(it)

    return Sequence(items)


# ── TIFF 16-bit 로더 ─────────────────────────────────
def read_tiff16(p:Path)->np.ndarray:
    try:  
        return np.asarray(Image.open(p).convert("I;16"), dtype=np.uint16)
    except Exception:
        return tifffile.imread(str(p)).astype(np.uint16)

# ── Dataset 빌더 ─────────────────────────────────────
def _has(df, grp:str, elm:str)->bool:
    return ((df.Group==grp)&(df.Element==elm)&(df.Value!="")).any()

def build_dataset(img:Path, tags:pd.DataFrame, gui_slope:float, gui_int:float)->FileDataset:
    arr = read_tiff16(img); rows,cols = arr.shape
    meta=Dataset(); meta.MediaStorageSOPClassUID=SecondaryCaptureImageStorage
    meta.MediaStorageSOPInstanceUID=generate_uid(); meta.TransferSyntaxUID=ExplicitVRLittleEndian

    ds=FileDataset(img.stem+".dcm",{},file_meta=meta,preamble=b"\0"*128)
    ds.is_implicit_VR=False; ds.is_little_endian=True; ds.SpecificCharacterSet="ISO_IR 192"

    now=datetime.now()
    ds.StudyDate=ds.SeriesDate=ds.ContentDate=now.strftime("%Y%m%d")
    ds.StudyTime=ds.SeriesTime=ds.ContentTime=now.strftime("%H%M%S")
    ds.Modality="OT"; ds.Rows,ds.Columns=rows,cols
    ds.SamplesPerPixel=1; ds.PhotometricInterpretation="MONOCHROME2"
    ds.BitsAllocated=ds.BitsStored=16; ds.HighBit=15; ds.PixelRepresentation=0
    # insert_creators(ds)   ← 삭제
    insert_all_creators(ds)  # 8개 Creator 전부 (루트만)


    if not _has(tags,"0028","1053"):
        ds.RescaleSlope = str(gui_slope)
    if not _has(tags,"0028","1052"):
        ds.RescaleIntercept = str(gui_int)
    ds.RescaleType="HU"

    for _, sq in tags[tags.VR=="SQ"].iterrows():
        seq=build_sequence(sq.Group+sq.Element, tags)
        if len(seq):
            ds.add_new((int(sq.Group,16), int(sq.Element,16)),"SQ",seq)

    for _, r in tags.iterrows():
        if r.VR=="SQ" or r.ParentTag:
            continue
        ds.add_new((int(r.Group,16), int(r.Element,16)),
                   VR_DOWN.get(r.VR,r.VR), safe_value(r.VR,r.Value))

    ds.PixelData = arr.tobytes()
    return ds

# ── 간단 태그 검사 ───────────────────────────────────
def validate_tags(df)->List[str]:
    da=df[(df.VR=="DA")&(~df.Value.str.fullmatch(r"\d{8}",na=False))]
    tm=df[(df.VR=="TM")&(~df.Value.str.fullmatch(r"\d{6}",na=False))]
    return [f"DA 오류: {v}" for v in da.Value] + [f"TM 오류: {v}" for v in tm.Value]

# ── manifest URL 탐색 ───────────────────────────────
def find_manifest_url(df: pd.DataFrame) -> str:
    cand = df[(df.Keyword.str.contains("IIIF", case=False, na=False)) &
              (df.Value.str.contains("http", na=False))]
    return cand.Value.iloc[0] if not cand.empty else ""

# ── GUI ──────────────────────────────────────────────
class ConverterGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("DICOCH DICOM Converter v2.9s")
        self.geometry("1020x780")
        self._build()

    # ── GUI 레이아웃 ────────────────────────────────
    def _build(self):
            # Notebook ────────────────────────
        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True)

        converter_tab = ttk.Frame(notebook)
        info_tab      = ttk.Frame(notebook)
        notebook.add(converter_tab, text="Converter")
        notebook.add(info_tab,     text="Info / 정보")

        # ── 1) Converter 탭 ───────────────────────────
        frm = ttk.Frame(converter_tab, padding=12)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        # ── 입력/출력 경로
        self.e_in, self.e_tag, self.e_out = [ttk.Entry(frm) for _ in range(3)]
        ttk.Label(frm, text="TIFF 폴더:").grid(row=0, column=0, sticky="w")
        self.e_in.grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(frm, text="찾기", command=self._pick_in)\
            .grid(row=0, column=2)

        ttk.Label(frm, text="태그 엑셀:").grid(row=1, column=0, sticky="w")
        self.e_tag.grid(row=1, column=1, sticky="ew", padx=4)
        ttk.Button(frm, text="찾기", command=self._pick_tag)\
            .grid(row=1, column=2)

        ttk.Label(frm, text="출력 폴더:").grid(row=2, column=0, sticky="w")
        self.e_out.grid(row=2, column=1, sticky="ew", padx=4)
        ttk.Button(frm, text="찾기", command=self._pick_out)\
            .grid(row=2, column=2)

        # Slope / Intercept
        ttk.Label(frm, text="Slope:").grid(row=0, column=3, sticky="e")
        self.e_slope = ttk.Entry(frm, width=8); self.e_slope.insert(0, "1")
        self.e_slope.grid(row=0, column=4)
        ttk.Label(frm, text="Intercept:").grid(row=1, column=3, sticky="e")
        self.e_int   = ttk.Entry(frm, width=8); self.e_int.insert(0, "-1024")
        self.e_int.grid(row=1, column=4)

        # IIIF 옵션
        self.open_viewer = tk.BooleanVar(value=False)
        ttk.Checkbutton(frm, text="IIIF 뷰어 열기", variable=self.open_viewer)\
            .grid(row=3, column=3, sticky="w")
        ttk.Label(frm, text="Manifest URL (선택):").grid(row=3, column=0, sticky="w")
        self.e_manifest = ttk.Entry(frm)
        self.e_manifest.grid(row=3, column=1, columnspan=2, sticky="ew", padx=4)

        # 자동 폴더 열기
        self.auto_open = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm, text="변환 후 폴더 열기", variable=self.auto_open)\
            .grid(row=4, column=3, columnspan=2, sticky="w")

        # 버튼 영역
        bf = ttk.Frame(frm); bf.grid(row=4, column=0, columnspan=3, sticky="ew", pady=10)
        ttk.Button(bf, text="변환 시작", command=self._start).pack(side="left", expand=True, fill="x", padx=(0,4))
        ttk.Button(bf, text="태그 검사", command=self._check).pack(side="left", expand=True, fill="x")
        ttk.Button(bf, text="태그 결과 저장", command=self._save_tags).pack(side="left", expand=True, fill="x")

        # 진행률 / 로그
        self.pb  = ttk.Progressbar(frm); self.pb.grid(row=5, column=0, columnspan=5, sticky="ew")
        ttk.Label(frm, text="■ 변환 로그").grid(row=6, column=0, sticky="w")
        self.log = scrolledtext.ScrolledText(frm, height=10)
        self.log.grid(row=7, column=0, columnspan=5, sticky="nsew", pady=4)

        ttk.Label(frm, text="■ 태그 결과").grid(row=8, column=0, sticky="w")
        self.tag_view = scrolledtext.ScrolledText(frm, height=12,
                                                font=("Consolas", 9),
                                                padx=2, pady=0, wrap="none")
        self.tag_view.grid(row=9, column=0, columnspan=5, sticky="nsew")
        frm.rowconfigure(9, weight=1)

        # ── 2) Info 탭 ────────────────────────────────
        INFO_TEXT = (
        "DICOCH DICOM Converter  v2.9s  (2025-06-20)\n\n"

        "▶ 주요 업데이트 │ Updates\n"
        "1) 16-byte Creator token + FullName(UT)\n"
        "2) Tag viewer & save  (TXT / XLSX)\n"
        "3) IIIF auto-viewer option\n\n"

        "▶ 사용법 │ How to use\n"
        "1) Converter 탭에서 TIFF·엑셀·출력 폴더 지정\n"
        "2) [변환 시작] → DICOM 생성\n"
        "3) 필요 시 [IIIF 뷰어 열기] 체크\n"
        "4) 태그 결과 저장 버튼으로 TXT/XLSX 추출\n\n"

        "▶ 제작자 │ Author\n"
        "▶ 제작자 │ Author\n"
        "기관  : 국립문화재연구원 X-선·CT 분석실\n"
        "        National Research Institute of Cultural Heritage (NRICH), X-ray / CT Lab\n"
        "업무  : 문화유산 X-선·CT 비파괴 진단 · 3D 스캔 · 연륜연대 분석 · 디지털 데이터 표준 연구\n"
        "        Non-destructive X-ray/CT diagnostics, 3D scanning & dendrochronology, digital-standard research\n"
        "이름  : 송정일  Song Jung-il\n"
        "e-mail: ssong85@korea.kr\n"
        "배포  : CC-BY-SA 4.0 — 자유 복제·수정·재배포(출처 표기)\n"
        "        CC-BY-SA 4.0 — Free use, modification and redistribution (attribution required)\n"
        "GitHub: https://github.com/SONG-JUNG-IL/DICOCH_TAG\n"
        )
        info_box = scrolledtext.ScrolledText(
            info_tab,
            font=("Segoe UI", 11),
            wrap="word",
            padx=12, pady=10,
            state="normal"
        )
        info_box.insert("1.0", INFO_TEXT)

        # 헤더(▶ …) 줄만 굵게
        for line in (3, 9, 17):          # 1-based line 번호
            info_box.tag_add("hdr", f"{line}.0", f"{line}.end")
        info_box.tag_configure("hdr", font=("Segoe UI", 11, "bold"))

        # 전체 행간 살짝 여유
        info_box.tag_add("gap", "1.0", "end")
        info_box.tag_configure("gap", spacing1=2, spacing3=2)

        info_box.config(state="disabled")
        info_box.pack(fill="both", expand=True)
    # ── 파일선택 유틸 ────────────────────────────────
    def _pick_in(self):
        p=filedialog.askdirectory()
        if p:
            self.e_in.delete(0,tk.END); self.e_in.insert(0,p)
            ts=datetime.now().strftime("%Y%m%d_%H%M%S")
            self.e_out.delete(0,tk.END); self.e_out.insert(0,str(Path.cwd()/f"{Path(p).name}_{ts}"))

    def _pick_tag(self):
        p=filedialog.askopenfilename(filetypes=[("Excel","*.xlsx")])
        if p:
            self.e_tag.delete(0,tk.END); self.e_tag.insert(0,p)

    def _pick_out(self):
        base=filedialog.askdirectory()
        if base:
            ts=datetime.now().strftime("%Y%m%d_%H%M%S")
            self.e_out.delete(0,tk.END); self.e_out.insert(0,str(Path(base)/f"output_{ts}"))

    # ── 로그 출력 도우미
    def _log(self,m): self.log.insert(tk.END,m+"\n"); self.log.see(tk.END)

    # ── 태그 검사
    def _check(self):
        try:
            issues=validate_tags(load_tags(Path(self.e_tag.get())))
            if issues:
                [self._log(i) for i in issues]
                messagebox.showwarning("검사",f"문제 {len(issues)}건")
            else:
                self._log("구조 이상 없음")
                messagebox.showinfo("검사","정상")
        except Exception as e:
            self._log(str(e)); messagebox.showerror("오류",str(e))

    # ── 변환 스레드 시작
    def _start(self):
        self.pb.config(value=0)
        threading.Thread(target=self._convert,daemon=True).start()

    def _update_tag_view(self, df):
        # 컬럼별 최소 폭 계산 = max(헤더길이, 값 최대길이) + 2
        widths = {c: max(len(c), df[c].astype(str).map(len).max()) + 2
                for c in df.columns}
        pretty = df.to_string(index=False, col_space=widths, justify="left")

        # 출력 + 행간 압축
        self.tag_view.delete("1.0", tk.END)
        self.tag_view.insert(tk.END, pretty)
        self.tag_view.tag_add("tight", "1.0", "end")
        self.tag_view.tag_configure("tight", spacing1=0, spacing3=0)  # 행간 0
        self.tag_view.see("1.0")
        # ── tag_view : 태그 출력 창 ─────────────────────────────
        self.tag_view = scrolledtext.ScrolledText(
            frm,
            height=12,
            font=("Consolas", 9),     # <= 좁은 고정폭 글꼴
            padx=2, pady=0,           # <= 내부 여백 최소화
            wrap="none"               # <= 수평 스크롤 사용
        )
        self.tag_view.grid(row=9, column=0, columnspan=5, sticky="nsew")


    # ── 태그 결과 저장
    def _save_tags(self):
        if not hasattr(self,"cur_tags"):
            messagebox.showwarning("저장","변환된 태그 정보가 없습니다."); return
        out_dir=Path(self.e_out.get())
        if not out_dir.exists():
            messagebox.showerror("저장","출력 폴더가 없습니다."); return
        ts=datetime.now().strftime("%Y%m%d_%H%M%S")
        txt_path  = out_dir / f"tag_info_{ts}.txt"
        xlsx_path = out_dir / f"tag_info_{ts}.xlsx"
        self.cur_tags.to_csv(txt_path, sep="\t", index=False)
        self.cur_tags.to_excel(xlsx_path, index=False)
        self._log(f"[태그 저장] {txt_path.name}, {xlsx_path.name}")
        messagebox.showinfo("저장 완료", f"TXT / XLSX 저장\n{txt_path}\n{xlsx_path}")

    # ── 변환 로직
    def _convert(self):
        try:
            in_dir = Path(self.e_in.get()); tag_xls = Path(self.e_tag.get()); out_dir = Path(self.e_out.get())
            if not in_dir.is_dir() or not tag_xls.is_file():
                messagebox.showerror("오류","경로 확인"); return
            tiffs=sorted(in_dir.glob("*.tif*"))
            if not tiffs:
                messagebox.showerror("오류","TIFF 없음"); return

            gui_slope=float(self.e_slope.get() or 1)
            gui_int =float(self.e_int.get() or -1024)

            tags=load_tags(tag_xls)         # 태그 로드
            self.cur_tags = tags            # GUI 저장용
            self.after(0, lambda: self._update_tag_view(tags))

            out_dir.mkdir(parents=True,exist_ok=True)
            dic_path=write_dic(tags,out_dir); self._log(f"[dicom.dic 작성] {dic_path}")

            manifest_val = find_manifest_url(tags)
            if manifest_val:
                self._log(f"[IIIF Link] {manifest_val}")

            log_f=(out_dir/f"log_{datetime.now():%Y%m%d_%H%M%S}.txt").open("w",encoding="utf-8")
            if manifest_val: log_f.write(f"[IIIF Link] {manifest_val}\n")

            succ=fail=0; lock=threading.Lock(); self.pb.config(maximum=len(tiffs))

            def task(fp:Path):
                nonlocal succ,fail
                try:
                    ds=build_dataset(fp,tags,gui_slope,gui_int)
                    pydicom.dcmwrite(out_dir/f"{fp.stem}.dcm", ds, write_like_original=False)
                    with lock: succ+=1
                    return f"✔ {fp.name}"
                except Exception as e:
                    with lock: fail+=1
                    return f"✖ {fp.name} → {e}"

            with cf.ThreadPoolExecutor(max_workers=max(1, os.cpu_count()//2)) as ex:
                for idx,msg in enumerate(ex.map(task,tiffs),1):
                    self._log(msg); log_f.write(msg+"\n"); self.pb.config(value=idx)

            summary=f"완료 {succ}   실패 {fail}"
            self._log(summary); log_f.write(summary+"\n"); log_f.close()
            messagebox.showinfo("완료",summary)

            # 폴더 자동 열기
            if self.auto_open.get() and succ and not fail:
                try: os.startfile(out_dir)
                except AttributeError:
                    opener="open" if sys.platform=="darwin" else "xdg-open"
                    subprocess.Popen([opener,str(out_dir)])

            # IIIF 뷰어 호출
            if self.open_viewer.get():
                manifest = self.e_manifest.get().strip() or manifest_val
                if manifest:
                    url = MIRADOR_DEMO + manifest   # no encoding
                    self._log(f"[Mirador] {url}")
                    webbrowser.open_new_tab(url)
                else:
                    self._log("Manifest URL이 없어 IIIF 뷰어 호출 생략")

        finally:
            self.pb.config(value=0)

# ── 실행 ──────────────────────────────────────────────
def main():
    try:
        import ctypes; ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    ConverterGUI().mainloop()

if __name__=="__main__":
    main()
