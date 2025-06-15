# -*- coding: utf-8 -*-
"""
DICOCH DICOM Converter – GUI Edition (v2.9m · 2025-06-15)
──────────────────────────────────────────────────────────
◎ UR VR → UT 자동 변환(Bio-Formats 6.x 호환)
◎ 값이 비어도 0013 태그 길이 0으로 기록 → 누락 방지
◎ DA/TM 자동 교정, 빈 Sequence prune, 순환·깊이 20단계 차단
◎ UTF-8(Specific Character Set = ISO_IR 192), UT VR 4-byte VL 상한 검사
◎ Private Creator (0013,0010)=DICOCH + pydicom 사전 등록
◎ 변환 로그(log_*.txt) & dicom.dic → ‘출력 폴더’ 내부 동일 위치 저장
◎ “출력 폴더” 선택 시 <선택경로>/output_YYYYMMDD_HHMMSS 자동 지정
"""
from __future__ import annotations
from pathlib import Path
from datetime import datetime
import os, threading, re, concurrent.futures as cf
import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext, messagebox
from typing import Dict, Callable, List, Set

import numpy as np
import pandas as pd
import pydicom
from pydicom.dataset import Dataset, FileDataset
from pydicom.sequence import Sequence
from pydicom.uid import ExplicitVRLittleEndian, SecondaryCaptureImageStorage, generate_uid
from pydicom.datadict import add_private_dict_entry
from PIL import Image
import tifffile as tiff

# ── 상수 & 정규식 ────────────────────────────────────────────
_NUM_RE   = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")
MAX_UT    = 0xFFFFFFFE
CREATOR   = "DICOCH"
GROUP_HEX = "0013"
VR_DOWN   = {"UR": "UT"}  # UR→UT

# ── VR 보정 함수 ─────────────────────────────────────────────
def numstr(s,d="0"): m=_NUM_RE.search(str(s)); return m.group(0) if m else d
def fix_cs(v): return str(v).upper().replace(" ","_")[:16]
def fix_da(v): return re.sub(r"\D","",str(v))[:8]
def fix_tm(v): return (re.sub(r"\D","",str(v))+"000000")[:6]

VR_RULES: Dict[str,Callable[[str],object]] = {
    "CS":fix_cs,"PN":lambda v:str(v)[:64],"SH":lambda v:str(v)[:16],
    "LO":lambda v:str(v)[:64],"UT":lambda v:v,
    "DS":lambda v:numstr(v)[:16],"IS":lambda v:str(int(float(numstr(v)))),
    "US":lambda v:int(float(numstr(v))),"FL":lambda v:float(numstr(v)),
    "FD":lambda v:float(numstr(v)),"OW":lambda v:v,
    "DA":fix_da,"TM":fix_tm,
}

def safe_value(vr:str,val):
    """VR 변환 + 길이/포맷 체크; 빈 값일 때 '' 반환"""
    vr_eff = VR_DOWN.get(vr, vr)
    if vr_eff == "UT":
        if val is None: return ""
        if len(str(val).encode("utf-8")) > MAX_UT:
            raise ValueError("UT value too long")
        return str(val)
    try: return VR_RULES[vr_eff](val)
    except Exception: return ""

# ── pydicom 사전 등록 & dicom.dic 작성 ─────────────────────
def register_private_tags(df:pd.DataFrame):
    for _,r in df.iterrows():
        if r.Element == "0010":  # Creator 태그 제외
            continue
        tag_int = (int(GROUP_HEX,16)<<16) | int(r.Element,16)
        add_private_dict_entry(
            CREATOR, tag_int, VR_DOWN.get(r.VR,r.VR),
            r.Keyword or f"DICOCH_{r.Element}", "1")

def write_dic(df:pd.DataFrame,out_dir:Path):
    out = out_dir/"dicom.dic"
    lines=[f"# generated {datetime.now():%Y-%m-%dT%H:%M:%S}"]
    for _,r in df.iterrows():
        lines.append(f"({GROUP_HEX},{r.Element}) {VR_DOWN.get(r.VR,r.VR)} 1 {r.Keyword or f'DICOCH_{r.Element}'}")
    out.write_text("\n".join(lines),encoding="utf-8")
    return out

# ── 태그 로더(prune + DA/TM 교정) ──────────────────────────
def load_tags(xlsx:Path)->pd.DataFrame:
    df=pd.read_excel(xlsx,dtype=str).fillna("")
    df["Group"],df["Element"]=df["Group"].str.zfill(4),df["Element"].str.zfill(4)
    df["ParentTag"]=df["ParentTag"].str.upper(); df["ItemIndex"]=df["ItemIndex"].replace("","0")
    df.loc[df.VR=="DA","Value"]=df.loc[df.VR=="DA","Value"].apply(fix_da)
    df.loc[df.VR=="TM","Value"]=df.loc[df.VR=="TM","Value"].apply(fix_tm)

    prune=(df.VR=="SQ") & (~df.apply(lambda r:(df.ParentTag==r.Group+r.Element).any(),axis=1))
    if prune.any(): print(f"[PRUNE] 빈 SQ {prune.sum()}개 삭제")
    df=df[~prune].reset_index(drop=True)

    register_private_tags(df)
    return df

# ── Sequence 빌더 ───────────────────────────────────────────
def build_sequence(root:str,df:pd.DataFrame,visited:Set[str]|None=None,depth:int=0)->Sequence:
    if visited is None: visited=set()
    if root in visited or depth>20: return Sequence([])
    visited.add(root); items=[]
    for _,rows in df[df.ParentTag==root].groupby("ItemIndex"):
        item=Dataset()
        for _,r in rows.iterrows():
            tag=(int(r.Group,16),int(r.Element,16))
            if r.VR=="SQ":
                seq=build_sequence(r.Group+r.Element,df,visited.copy(),depth+1)
                if len(seq): item.add_new(tag,"SQ",seq)
            else:
                item.add_new(tag,VR_DOWN.get(r.VR,r.VR),safe_value(r.VR,r.Value))
        if len(item): items.append(item)
    return Sequence(items)

# ── 이미지 로더 ─────────────────────────────────────────────
def read_tiff16(p:Path)->np.ndarray:
    try: return np.asarray(Image.open(p).convert("I;16"),dtype=np.uint16)
    except Exception: return tiff.imread(p).astype(np.uint16)

# ── Dataset 빌더 ────────────────────────────────────────────
def build_dataset(img:Path,tags:pd.DataFrame,slope:float,intercept:float)->FileDataset:
    arr=read_tiff16(img); rows,cols=arr.shape
    meta=Dataset(); meta.MediaStorageSOPClassUID=SecondaryCaptureImageStorage
    meta.MediaStorageSOPInstanceUID=generate_uid(); meta.TransferSyntaxUID=ExplicitVRLittleEndian
    ds=FileDataset(img.stem+".dcm",{},file_meta=meta,preamble=b"\0"*128)
    ds.is_implicit_VR=False; ds.is_little_endian=True; ds.SpecificCharacterSet="ISO_IR 192"

    now=datetime.now(); ds.StudyDate=ds.SeriesDate=ds.ContentDate=now.strftime("%Y%m%d")
    ds.StudyTime=ds.SeriesTime=ds.ContentTime=now.strftime("%H%M%S")
    ds.Modality="OT"; ds.Rows,ds.Columns=rows,cols
    ds.SamplesPerPixel=1; ds.PhotometricInterpretation="MONOCHROME2"
    ds.BitsAllocated=ds.BitsStored=16; ds.HighBit=15; ds.PixelRepresentation=0
    ds.add_new((0x0013,0x0010),"LO",CREATOR)

    if not ((tags.Group=="0028")&(tags.Element=="1053")).any(): ds.RescaleSlope=str(slope)
    if not ((tags.Group=="0028")&(tags.Element=="1052")).any(): ds.RescaleIntercept=str(intercept)
    ds.RescaleType="HU"

    for _,sq in tags[tags.VR=="SQ"].iterrows():
        seq=build_sequence(sq.Group+sq.Element,tags)
        if len(seq): ds.add_new((int(sq.Group,16),int(sq.Element,16)),"SQ",seq)

    for _,r in tags.iterrows():
        if r.VR=="SQ" or r.ParentTag: continue
        ds.add_new((int(r.Group,16),int(r.Element,16)),VR_DOWN.get(r.VR,r.VR),safe_value(r.VR,r.Value))

    ds.PixelData=arr.tobytes()
    return ds

# ── 간단 형식 검사 ──────────────────────────────────────────
def validate_tags(df)->List[str]:
    msgs=[]
    bad_da=df[(df.VR=="DA") & (~df.Value.str.fullmatch(r"\d{8}",na=False))]
    bad_tm=df[(df.VR=="TM") & (~df.Value.str.fullmatch(r"\d{6}",na=False))]
    msgs += [f"DA 오류: {v}" for v in bad_da.Value]
    msgs += [f"TM 오류: {v}" for v in bad_tm.Value]
    return msgs

# ── GUI ─────────────────────────────────────────────────────
class ConverterGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("DICOCH DICOM Converter v2.9m"); self.geometry("1000x680")
        self._build()

    def _build(self):
        frm=ttk.Frame(self,padding=12); frm.pack(fill="both",expand=True)
        frm.columnconfigure(1,weight=1)
        # 입력 필드
        self.e_in,self.e_tag,self.e_out=[ttk.Entry(frm) for _ in range(3)]
        ttk.Label(frm,text="TIFF 폴더:").grid(row=0,column=0,sticky="w")
        self.e_in.grid(row=0,column=1,sticky="ew",padx=4); ttk.Button(frm,text="찾기",command=self._pick_in).grid(row=0,column=2)
        ttk.Label(frm,text="태그 엑셀:").grid(row=1,column=0,sticky="w")
        self.e_tag.grid(row=1,column=1,sticky="ew",padx=4); ttk.Button(frm,text="찾기",command=self._pick_tag).grid(row=1,column=2)
        ttk.Label(frm,text="출력 폴더:").grid(row=2,column=0,sticky="w")
        self.e_out.grid(row=2,column=1,sticky="ew",padx=4); ttk.Button(frm,text="찾기",command=self._pick_out).grid(row=2,column=2)
        # slope/intercept
        ttk.Label(frm,text="Slope:").grid(row=0,column=3,sticky="e")
        self.e_slope=ttk.Entry(frm,width=8); self.e_slope.insert(0,"1"); self.e_slope.grid(row=0,column=4)
        ttk.Label(frm,text="Intercept:").grid(row=1,column=3,sticky="e")
        self.e_int=ttk.Entry(frm,width=8); self.e_int.insert(0,"-1024"); self.e_int.grid(row=1,column=4)
        # 버튼/진행률/로그
        bf=ttk.Frame(frm); bf.grid(row=3,column=0,columnspan=5,sticky="ew",pady=10)
        ttk.Button(bf,text="변환 시작",command=self._start).pack(side="left",expand=True,fill="x",padx=(0,4))
        ttk.Button(bf,text="태그 검사",command=self._check).pack(side="left",expand=True,fill="x")
        self.pb=ttk.Progressbar(frm); self.pb.grid(row=4,column=0,columnspan=5,sticky="ew")
        self.log=scrolledtext.ScrolledText(frm,height=18); self.log.grid(row=5,column=0,columnspan=5,sticky="nsew",pady=8); frm.rowconfigure(5,weight=1)

    # 경로 선택
    def _pick_in(self):
        p=filedialog.askdirectory()
        if p:
            self.e_in.delete(0,tk.END); self.e_in.insert(0,p)
            ts=datetime.now().strftime("%Y%m%d_%H%M%S")
            self.e_out.delete(0,tk.END); self.e_out.insert(0,str(Path.cwd()/f"{Path(p).name}_{ts}"))
    def _pick_tag(self):
        p=filedialog.askopenfilename(filetypes=[("Excel","*.xlsx")])
        if p: self.e_tag.delete(0,tk.END); self.e_tag.insert(0,p)
    def _pick_out(self):
        base=filedialog.askdirectory()
        if base:
            ts=datetime.now().strftime("%Y%m%d_%H%M%S")
            self.e_out.delete(0,tk.END); self.e_out.insert(0,str(Path(base)/f"output_{ts}"))
    def _log(self,m): self.log.insert(tk.END,m+"\n"); self.log.see(tk.END)

    # 태그 검사
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

    # 변환 스레드
    def _start(self):
        self.pb.config(value=0); threading.Thread(target=self._convert,daemon=True).start()
    def _convert(self):
        try:
            in_dir,tag_xls,out_dir=Path(self.e_in.get()),Path(self.e_tag.get()),Path(self.e_out.get())
            if not in_dir.is_dir() or not tag_xls.is_file():
                messagebox.showerror("오류","경로 확인"); return
            tiffs=sorted(in_dir.glob("*.tif*"))
            if not tiffs:
                messagebox.showerror("오류","TIFF 없음"); return
            slope,intercept=float(self.e_slope.get() or 1),float(self.e_int.get() or -1024)
            tags=load_tags(tag_xls); out_dir.mkdir(parents=True,exist_ok=True)
            dic_path=write_dic(tags,out_dir); self._log(f"[dicom.dic 작성] {dic_path}")
            log_f=(out_dir/f"log_{datetime.now():%Y%m%d_%H%M%S}.txt").open("w",encoding="utf-8")
            succ=fail=0; self.pb.config(maximum=len(tiffs))
            def task(fp:Path):
                nonlocal succ,fail
                try:
                    ds=build_dataset(fp,tags,slope,intercept)
                    pydicom.dcmwrite(out_dir/f"{fp.stem}.dcm",ds,write_like_original=False)
                    succ+=1; return f"✔ {fp.name}"
                except Exception as e:
                    fail+=1; return f"✖ {fp.name} → {e}"
            with cf.ThreadPoolExecutor(max_workers=max(1,os.cpu_count()//2)) as ex:
                for idx,msg in enumerate(ex.map(task,tiffs),1):
                    self._log(msg); log_f.write(msg+"\n"); self.pb.config(value=idx)
            summary=f"완료 {succ}   실패 {fail}"
            self._log(summary); log_f.write(summary+"\n"); log_f.close()
            messagebox.showinfo("완료",summary)
        finally:
            self.pb.config(value=0)

# ── 실행 ───────────────────────────────────────────────────
def main():
    try:
        import ctypes; ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception: pass
    ConverterGUI().mainloop()

if __name__=="__main__":
    main()
