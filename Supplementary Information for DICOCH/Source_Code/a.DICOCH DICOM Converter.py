# -*- coding: utf-8 -*-
# ──────────────────────────────────────────────────────────────
# 📦 DICOCH DICOM Converter — v3.3 (2025-08-11)
#   * JPEG/PNG 입력 지원 (8-bit 포함)
#   * parenttag 검증 루프 버그 수정 (중첩 iterrows 변수 덮임 제거)
#   * dicom.dic 산출 시 HEX 검증 / 오타 수정(Private)
#   * SpecificCharacterSet → ["ISO_IR 192"] 리스트 지정
#   * build_dataset()의 parent SQ 처리 안정화
#   * 예외 처리 로그에 traceback 포함
#   * Tag Viewer 가독성 보완, Info 탭 최신화
# Author : Song Jung-il (NRICH)
# License: CC BY-SA 4.0
# ──────────────────────────────────────────────────────────────
from __future__ import annotations   # ← 이 줄을 ‘최상단’으로 이동

import tkinter as tk

def _norm_tag_key(key):
    # Normalize a dict key into a (group, element) 2-tuple of ints.
    # Accepts keys like (0x0008,0x0008), ('0008','0008'), ('0008','0008','CS'), '0008,0008', '00080008'.
    def _to_int_hex(x):
        if isinstance(x, int):
            return x
        s = str(x).strip().strip('()').replace(' ','').upper()
        if s.startswith('0X'):
            s = s[2:]
        try:
            return int(s, 16)
        except Exception:
            return None

    if isinstance(key, (tuple, list)):
        if len(key) >= 2:
            g = _to_int_hex(key[0])
            e = _to_int_hex(key[1])
            if g is not None and e is not None:
                return (g, e)
        return None

    s = str(key).strip().strip('()').replace(' ', '').upper()
    if ',' in s:
        a, b = s.split(',', 1)
    else:
        a, b = s[:4], (s[4:8] if len(s) >= 8 else '0000')
    g = _to_int_hex(a)
    e = _to_int_hex(b)
    if g is None or e is None:
        return None
    return (g, e)
from tkinter import ttk, filedialog, scrolledtext, messagebox
import json, os, re, threading, concurrent.futures as cf, sys, subprocess, webbrowser, traceback
from datetime import datetime as dt
from pathlib import Path
from typing import Callable, Dict, List
import numpy as np
import pydicom
from pydicom.dataset import Dataset, FileDataset
from pydicom.sequence import Sequence
from pydicom.uid import generate_uid, SecondaryCaptureImageStorage, ExplicitVRLittleEndian
from pydicom.datadict import add_private_dict_entry
from PIL import Image
import tifffile
from openpyxl import load_workbook
import logging
import pandas as pd
# --- pydicom UID 호환 임포트 (v2.x/3.x 모두 동작) ---
try:
    # pydicom ≥ 2.2 ~ 3.x
    from pydicom.uid import JPEGBaseline8Bit as JPEGBaseline
except Exception:
    try:
        # 일부 구버전은 여전히 JPEGBaseline 제공
        from pydicom.uid import JPEGBaseline  # type: ignore
    except Exception:
        # 최후 수단: UID 문자열로 직접 지정
        from pydicom.uid import UID
        JPEGBaseline = UID("1.2.840.10008.1.2.4.50") #("1.2.840.10008.1.2.1")

from pydicom.encaps import encapsulate
import io


# ── VR 규격 정보 ────────────────────────────────────────────
VR_MAXLEN = {
    "AE":16,"AS":4,"CS":16,"DA":8,"DS":16,"DT":26,"IS":12,
    "LO":64,"LT":10240,"PN":64,"SH":16,"ST":1024,"TM":16,
    "UI":64,"UT":0xFFFFFFFE,
    # Numeric VRs
    "UL":12,"US":12,"SL":12,"SS":12,"FL":16,"FD":32
}
VALID_VR = set(VR_MAXLEN) | {"SQ","UN","OW","OB","OF","OD","OL","UR"}

# ── 정규식 / 상수 ───────────────────────────────────────────
HEX_RE       = re.compile(r"^[0-9A-Fa-f]{1,4}$")
NUM_RE       = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")
MULTI_DELIM  = re.compile(r"[,;/\s]+")
VR_DOWN      = {"UR":"UT"}

logging.basicConfig(level=logging.INFO)
LOG = logging.getLogger("dicoch.v33")

# ── 기관 OID 프리픽스 (실사용 시 공식 OID로 교체 권장) ───────────────
OID_PREFIX = "1.2.410.999999.20250724."

# ── Private Creator 토큰 ─────────────────────────────────────
CREATORS = [
    "DICOCH", "Heritage_NRICH", "GrayCal_NRICH", "ROIGray_NRICH",
    "HUCal_NRICH", "ROIHU_NRICH", "IIIF_NRICH", "Security_NRICH",
    "DICOCH_Dict_NRICH",
]
MIRADOR_DEMO = "https://projectmirador.org/demo/?manifest="


INFO_TEXT = (
            "DICOCH DICOM Converter  v3.3  (2025-08-11)\n\n"
            "▶ Updates\n"
            "1) JPEG/PNG input support (8-bit)\n"
            "2) parenttag validation bug fix\n"
            "3) dicom.dic HEX guard & name fix\n"
            "4) UTF-8 charset list form\n"
            "5) parent SQ handling stabilized\n"
            "6) traceback logging\n\n"
            "▶ How to use\n"
            "1) Set image folder / Excel tags / Output folder\n"
            "2) Convert → DICOM\n"
            "3) (Optional) Open IIIF viewer\n"
            "4) Save tags (TXT/XLSX/JSON)\n\n"
            "Author: Song Jung-il / NRICH  |  License: CC-BY-SA 4.0\n"
        )
# ── VR 보정 / Value 정규화 ─────────────────────────────────
def _num(s, default="0"):
    m = NUM_RE.search(str(s))
    return m.group(0) if m else default

def _fix_cs(v): return str(v).upper().replace(" ","_")[:16]
def _fix_da(v): return re.sub(r"\D","",str(v))[:8]
def _fix_tm(v): return (re.sub(r"\D","",str(v))+"000000")[:6]

def _truncate(v:str, vr:str)->str:
    lim = VR_MAXLEN.get(vr, 1024)
    s = str(v)
    return s[:lim] if lim>0 and len(s)>lim else s

def _clean_ui(v:str)->str:
    raw = re.sub(r"[^\d.]", "", str(v))
    if "." not in raw:
        raw = f"{OID_PREFIX}{raw}"
    if raw.endswith("."):
        raw = raw[:-1]
    arcs = [str(int(a)) if a and a!="0" else "0" for a in raw.split(".")]
    uid  = ".".join(arcs)
    return _truncate(uid, "UI")

def _clean_ds(v):
    return MULTI_DELIM.sub(r"\\", str(v).strip("[] "))

VR_RULES: Dict[str, Callable[[str], object]] = {
    "CS": _fix_cs, "PN": lambda v: str(v).replace(",", "^")[:64],
    "SH": lambda v: str(v)[:16], "LO": lambda v: str(v)[:64],
    "UI": _clean_ui, "UT": lambda v: v,
    "DS": lambda v: _truncate(_clean_ds(v),"DS"),
    "IS": lambda v: str(int(float(_num(v)))),
    "US": lambda v: int(float(_num(v))), "UL": lambda v: int(float(_num(v))),
    "FL": lambda v: float(_num(v)), "FD": lambda v: float(_num(v)),
    "OB": lambda v: v, "OW": lambda v: v, "DA": _fix_da, "TM": _fix_tm,
}

def safe_value(vr: str, val: str):
    if vr in ("OB","OW","UN"):
        v = str(val).strip()
        try:    return bytes.fromhex(v)
        except: return v.encode("utf-8")
    if vr == "UI":
        return _clean_ui(val)
    return VR_RULES.get(vr, lambda x: x)(val)

# ── 공통 유틸 ───────────────────────────────────────────────

def _validate_image_pixel_module(ds: FileDataset) -> None:
    """
    DICOM 저장 직전, Image Pixel Module/Pixel Data 일관성 검증.
    - 비압축: PixelData 길이 == Rows*Cols*SamplesPerPixel*(BitsAllocated/8)*(NumberOfFrames or 1)
    - 압축: Encapsulated 여부와 필수 속성 조합만 점검
    문제가 있으면 AssertionError/ValueError raise.
    """
    # 필수 공통 속성
    required = ["Rows","Columns","SamplesPerPixel","BitsAllocated","BitsStored","HighBit","PixelRepresentation","PhotometricInterpretation"]
    for k in required:
        if not hasattr(ds, k):
            raise ValueError(f"Missing required attribute: {k}")

    rows = int(ds.Rows); cols = int(ds.Columns)
    spp  = int(getattr(ds, "SamplesPerPixel", 1))
    bits = int(getattr(ds, "BitsAllocated", 8))
    frames = int(getattr(ds, "NumberOfFrames", 1))

    if rows <= 0 or cols <= 0:
        raise ValueError(f"Invalid geometry Rows/Columns: {rows}x{cols}")
    if bits not in (8,16,32):
        raise ValueError(f"Unsupported BitsAllocated: {bits}")

    ts = ds.file_meta.TransferSyntaxUID
    is_compressed = getattr(ts, "is_compressed", False)

    if not is_compressed:
        # 네이티브(비압축): PixelData 길이 정합
        need = rows * cols * spp * (bits // 8) * frames
        have = len(ds.PixelData) if hasattr(ds, "PixelData") and ds.PixelData else 0
        if need != have:
            raise AssertionError(f"PixelData length mismatch: need={need}, have={have}")
    else:
        # 압축: Encapsulation 여부(Undefined Length + 프래그먼트)는 pydicom 캡슐화가 보장
        # Photometric/샘플수 조합만 간단히 점검
        if spp == 3 and ds.PhotometricInterpretation not in ("YBR_FULL_422","YBR_FULL","YBR_PARTIAL_422"):
            raise AssertionError(f"Unexpected Photometric for color JPEG: {ds.PhotometricInterpretation}")
        if spp == 1 and ds.PhotometricInterpretation != "MONOCHROME2":
            raise AssertionError(f"Unexpected Photometric for grayscale JPEG: {ds.PhotometricInterpretation}")


def _encapsulate_jpeg_from_bytes(jpg_bytes: bytes) -> bytes:
    """단일 프레임 JPEG 비트스트림을 DICOM PixelData(Encapsulated)로 포장"""
    return encapsulate([jpg_bytes])  # Basic Offset Table = empty

def _encode_jpeg_baseline(arr: np.ndarray, quality: int = 90) -> bytes:
    """numpy 배열을 JPEG Baseline(손실) 단일 프레임으로 인코딩"""
    pil = Image.fromarray(arr.astype(np.uint8) if arr.dtype != np.uint8 else arr)
    bio = io.BytesIO()
    # subsampling 기본(4:2:0) — 그레이스케일은 하위채널 없음
    pil.save(bio, format="JPEG", quality=int(quality), optimize=True)
    return bio.getvalue()


def _normalize_cols(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [c.lower().strip().replace(" ","") for c in df.columns]
    return df.loc[:, ~df.columns.duplicated(keep="first")]

def _parse_hex(v:str)->str:
    s = str(v).strip()
    if not s: return ""
    if HEX_RE.fullmatch(s): return s.zfill(4).upper()
    return f"{int(float(s)):04X}"

def _hex2int(s: str) -> int:
    return int(s, 16) if s else -1

# ── Private Creator 삽입 ────────────────────────────────────
def insert_all_creators(ds: Dataset):
    for slot, name in enumerate(CREATORS):
        ds.add_new((0x0013, 0x0010 + slot), "LO", name)

# ── dicom.dic 작성 ──────────────────────────────────────────
def write_dic(df: pd.DataFrame, out_dir: Path) -> Path:
    df = _normalize_cols(df)
    out_dic = out_dir / "dicom.dic"
    lines   = []
    for _, r in df.iterrows():
        g, e, vr = r.get("group",""), r.get("element",""), r.get("vr","")
        if not g or not e:
            continue
        if not (HEX_RE.fullmatch(g) and HEX_RE.fullmatch(e)):
            LOG.warning("skip non-HEX tag in dicom.dic: %s,%s", g, e)
            continue
        if vr == "SQ":
            continue
        vr = VR_DOWN.get(vr, vr)
        kw = r.get("keyword","") or f"DICOCH_{e}"
        lines.append(f"({g},{e}) {vr} 1 {kw}")
    out_dic.write_text("\n".join(lines), encoding="utf-8")
    return out_dic

# ── Excel 로더 ──────────────────────────────────────────────
def _read_excel_any(path: Path) -> pd.DataFrame:
    # 디렉터리를 파일로 읽으려 할 때 바로 예외
    if Path(path).is_dir():
        raise IsADirectoryError(f"Expected a file, got directory: {path}")
    ext = path.suffix.lower()
    try:
        if ext in (".xlsx",".xlsm",".xlsb"):
            return pd.read_excel(path, dtype=str, engine="openpyxl")
        elif ext == ".xls":
            return pd.read_excel(path, dtype=str, engine="xlrd")
        else:
            return pd.read_csv(path, dtype=str)
    except ValueError as e:
        if "Excel file format" in str(e):
            raise RuntimeError(
                f"파일 형식을 판별할 수 없습니다: {path.name}\n"
                "· 확장자/파일 형식을 확인하세요\n"
                "· openpyxl/xlrd 설치 여부 점검"
            ) from e
        raise

# ── 태그 로딩 / 정규화 (유연 헤더 지원: Tag 또는 group/element) ──────
def load_tags(xlsx_path: str|Path) -> pd.DataFrame:
    """
    Load tags from Excel/CSV.
    Policy: **Excel-first** — any value present in Excel overrides defaults.
    Defaults are only used when a tag is missing or its value is blank.
    """
    import re
    _TAG_RE = re.compile(r"\(?\s*([0-9A-Fa-f]{4})\s*[,:\s]\s*([0-9A-Fa-f]{4})\s*\)?")

    def _norm_cols(cols):
        return [str(c).strip().lower().replace(" ", "").replace("-", "").replace("_", "") for c in cols]

    def _parse_tag_cell(s: str):
        if pd.isna(s):
            return "", ""
        m = _TAG_RE.search(str(s))
        if not m:
            return "", ""
        return m.group(1).upper(), m.group(2).upper()





    df_in = _read_excel_any(Path(xlsx_path)).fillna("")
    df = df_in.copy()
    df.columns = _norm_cols(df.columns)

    alias = {"group":None, "element":None, "vr":None, "keyword":None,
             "value":None, "parenttag":None, "tag":None}
    for c in df.columns:
        if c in ("group","grp","g"): alias["group"] = c
        elif c in ("element","elem","el","e"): alias["element"] = c
        elif c == "vr": alias["vr"] = c
        elif c in ("keyword","key","kw"): alias["keyword"] = c
        elif c in ("value","val","v"): alias["value"] = c
        elif c in ("parenttag","parent","ptag","parentseq","parentsequence"): alias["parenttag"] = c
        elif c in ("tag","tagelement","ge"): alias["tag"] = c

    if alias["group"] is None or alias["element"] is None:
        if alias["tag"] is None:
            raise KeyError("엑셀에 group/element 또는 tag 열이 없습니다.")
        gs, es = [], []
        for s in df[alias["tag"]]:
            g, e = _parse_tag_cell(s)
            gs.append(g); es.append(e)
        df["group"], df["element"] = gs, es
    else:
        df["group"]   = df[alias["group"]].astype(str).str.strip().str.upper()
        df["element"] = df[alias["element"]].astype(str).str.strip().str.upper()

    df["vr"]      = df[alias["vr"]].astype(str).str.strip().str.upper() if alias["vr"] else "LO"
    df["keyword"] = df[alias["keyword"]].astype(str).str.strip() if alias["keyword"] else ""
    df["value"]   = df[alias["value"]].astype(str).str.strip() if alias["value"] else ""

    if alias["parenttag"]:
        pt = df[alias["parenttag"]].astype(str).str.strip()
        pts = []
        for s in pt:
            g, e = _parse_tag_cell(s)
            pts.append((g+e) if g and e else "")
        df["parenttag"] = pts
    else:
        df["parenttag"] = ""

    # 이후 기존 파이프라인
    df["group"]   = df["group"].apply(_parse_hex)
    df["element"] = df["element"].apply(_parse_hex)
    df["vr"]      = df["vr"].str.upper().str.strip()

    bad = df[~df["vr"].isin(VALID_VR)]
    if not bad.empty:
        LOG.warning("Unknown VR → LO :\n%s", bad[["group","element","vr"]])
        df.loc[bad.index, "vr"] = "LO"

    for idx, r in df.iterrows():
        vr, val = r["vr"], r["value"]
        if vr=="UI": df.at[idx,"value"] = _truncate(_clean_ui(val),"UI")
        elif vr=="DS": df.at[idx,"value"] = _truncate(_clean_ds(val),"DS")
        elif vr=="IS":
            m = NUM_RE.search(val)
            if m: df.at[idx,"value"] = str(int(round(float(m.group(0)))))
        elif vr=="CS":
            # Do NOT split on spaces — preserve tokens like "ISO_IR 192" exactly
            if str(r.get("group","")) == "0008" and str(r.get("element","")) == "0005":
                df.at[idx,"value"] = str(val).strip()
            else:
                parts = re.split(r"\\|[,;/]+", str(val).strip())
                parts = [_truncate(_fix_cs(p), "CS") for p in parts if p]
                df.at[idx,"value"] = "\\".join(parts)
        elif vr=="DA": df.at[idx,"value"] = _fix_da(val)
        elif vr=="TM": df.at[idx,"value"] = _fix_tm(val)

    def _parenttag_valid(df_: pd.DataFrame, tag: str) -> bool:
        if not tag or len(tag) != 8:
            return False
        g, e = tag[:4], tag[4:]
        sq_mask = ((df_["group"]==g) & (df_["element"]==e) & (df_["vr"].str.upper()=="SQ"))
        return bool(sq_mask.any())

    for idx, row in df.iterrows():
        pt = row["parenttag"]
        own = (row["group"] + row["element"]) if row["group"] and row["element"] else ""
        if pt and (pt == own or pt.endswith("0000") or not _parenttag_valid(df, pt)):
            LOG.warning("%s,%s invalid/self parenttag=%s → cleared", row["group"], row["element"], pt)
            df.at[idx,"parenttag"] = ""

    before = len(df)
    df = df[(df["group"]!="") & (df["element"]!="")]
    if (rm := before - len(df)):
        LOG.warning("❎ 빈 Group/Element 행 %d 개 삭제", rm)

    df = (df.sort_values(["group","element","parenttag"])
            .groupby(["group","element","parenttag"], as_index=False)
            .first())

    today, now = dt.now().strftime("%Y%m%d"), dt.now().strftime("%H%M%S")
    def _add(g,e,vr,v):
        sel = (df["group"]==g) & (df["element"]==e)
        if sel.any():
            df.loc[sel & df["value"].eq(""), "value"] = v
        else:
            df.loc[len(df)] = {"group":g,"element":e,"vr":vr,"keyword":"","value":v,"parenttag":""}
    core = [
        # --- Core identifiers / dates & IDs (only filled when Excel is blank) ---
        ("0008","0060","CS","OT"),   # Modality (Other) — Excel can override (e.g., CR/CT/MG)
        ("0010","0010","PN","UNKNOWN^HeritageObject"),  # PatientName (object-centered)
        ("0010","0020","LO","OBJ-0001"),                # PatientID   (object id)
        ("0008","0020","DA",today),                     # Study Date
        ("0008","0030","TM",now),                       # Study Time
        ("0020","000D","UI",generate_uid(prefix=OID_PREFIX)),  # StudyInstanceUID
        ("0020","000E","UI",generate_uid(prefix=OID_PREFIX)),  # SeriesInstanceUID
        ("0020","0052","UI",generate_uid(prefix=OID_PREFIX)),  # FrameOfReferenceUID
        ("0008","0018","UI",generate_uid(prefix=OID_PREFIX)),  # SOPInstanceUID
    ]
    extra = [
        # --- Recommended defaults for cultural‑heritage (Excel values always take precedence) ---
        ("0008","0005","CS","ISO_IR 192"),                      # SpecificCharacterSet (UTF-8)
        ("0008","0008","CS","DERIVED\\SECONDARY"),            # ImageType
        ("0008","1030","LO","Cultural Heritage Study"),         # StudyDescription
        ("0008","103E","LO","Cultural Heritage Scan"),          # SeriesDescription
        ("0008","0070","LO","DICOCH"),                          # Manufacturer
        ("0008","1090","LO","Converter v3.3"),                  # Model Name
        ("0008","0080","LO","National Research Institute of Cultural Heritage"),  # InstitutionName
        ("0018","1020","LO","DICOCH 3.3"),                      # SoftwareVersions
        ("0020","0011","IS","1"),                               # SeriesNumber
        # (0008,0061) CS ModalitiesInStudy — removed (not for per-instance)

    ]
    for g,e,vr,v in core+extra: _add(g,e,vr,v)

    for i,r in df.iterrows():
        v = str(r["value"])
        if r["vr"] == "CS" and ("\\" in v or any(c in v for c in [",",";","/"," "])):

            parts = re.split(r"\\|[,;/\s]+", v.strip())
            parts = [_truncate(_fix_cs(p), "CS") for p in parts if p]
            df.at[i,"value"] = "\\".join(parts)
        else:
            df.at[i,"value"] = _truncate(v, r["vr"])

    return df.sort_values(["group","element","parenttag"]).reset_index(drop=True)


def _load_tag_map(tag_dir: Path) -> Dict[str, pd.DataFrame]:
    """태그 폴더에서 파일명(stem) → DataFrame 매핑을 만든다.
       우선순위: .xlsx > .xls > .csv/.tsv
    """
    priority = {".xlsx": 3, ".xls": 2, ".csv": 1, ".tsv": 1}
    tag_map: Dict[str, pd.DataFrame] = {}
    rank: Dict[str, int] = {}

    for ext in (".xlsx", ".xls", ".csv", ".tsv"):
        for p in sorted(tag_dir.glob(f"*{ext}")):
            stem = p.stem.lower()
            pr   = priority.get(ext, 0)
            if (stem not in tag_map) or (pr > rank.get(stem, 0)):
                try:
                    df = load_tags(p)
                except Exception as e:
                    LOG.error("Tag load failed: %s → %s", p.name, e)
                    continue
                tag_map[stem] = df
                rank[stem] = pr
    return tag_map

def _merge_tags_for_dic(tag_map: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    """모든 태그를 병합하여 dicom.dic/미리보기용으로 중복 제거한 DataFrame 반환"""
    if not tag_map:
        return pd.DataFrame(columns=["group","element","vr","keyword","value","parenttag"])
    df = pd.concat(tag_map.values(), ignore_index=True)
    # group/element/parenttag 기준 중복 제거 (VR/keyword/value는 첫 항목 사용)
    df = (df.sort_values(["group","element","parenttag"])
            .drop_duplicates(subset=["group","element","parenttag"], keep="first")
            .reset_index(drop=True))
    return df


# ── 이미지 로더: TIFF(16-bit) + JPG/PNG(8-bit) ──────────────────────
def read_image_any(p: Path) -> np.ndarray:
    """TIFF는 16-bit 우선, 그 외(JPG/PNG)는 그레이스케일 8-bit로 로드.
       RGB 입력은 L로 변환. 반환 dtype은 uint16 또는 uint8.
    """
    ext = p.suffix.lower()
    if ext in (".tif", ".tiff"):
        # TIFF는 16-bit 로딩 우선
        try:
            return np.asarray(Image.open(p).convert("I;16"), dtype=np.uint16)
        except Exception:
            return tifffile.imread(str(p)).astype(np.uint16)
    else:
        im = Image.open(p)
        if im.mode not in ("L", "I;16"):
            im = im.convert("L")  # RGB→그레이 8-bit
        arr = np.asarray(im)
        if arr.dtype != np.uint8 and arr.dtype != np.uint16:
            arr = arr.astype(np.uint8)
        return arr

# ── Rescale 파라미터 ─────────────────────────────────────────
def get_rescale_params(tags: pd.DataFrame, gui_slope: float, gui_int: float, override: bool) -> tuple[str,str]:
    tags = _normalize_cols(tags)
    def _val(code: str) -> str:
        sel = (tags["group"]=="0028") & (tags["element"]==code) & (tags["parenttag"]=="")
        row = tags[sel]
        if row.empty: return ""
        v = row["value"].iloc[0]
        return str(v) if v else ""
    slope_val = _val("1053")
    int_val   = _val("1052")
    if override or not slope_val: slope_val = str(gui_slope)
    if override or not int_val:   int_val   = str(gui_int)
    return slope_val, int_val

# ── DICOM Dataset 생성 ───────────────────────────────────────
VR_DEF: dict[tuple[str, str], str] = {
    ("0008","0060"): "CS", ("0008","0020"): "DA", ("0008","0030"): "TM",
    ("0008","0018"): "UI", ("0020","000D"): "UI", ("0020","000E"): "UI",
    ("0020","0052"): "UI", ("0028","0002"): "US", ("0028","0004"): "CS",
    ("0028","0010"): "US", ("0028","0011"): "US", ("0028","0100"): "US",
    ("0028","0101"): "US", ("0028","0102"): "US", ("0028","0103"): "US",
}

def build_dataset(img: Path, tags: pd.DataFrame, gui_slope: float, gui_int: float, override: bool,
                  compress_mode: str = "uncompressed", jpeg_quality: int = 90) -> FileDataset:
    tags = _normalize_cols(tags)

    # 1) 픽셀
    arr        = read_image_any(img)
    rows, cols = arr.shape
    bits       = arr.itemsize * 8

    # 2) file meta & 루트
    meta = Dataset()
    meta.FileMetaInformationVersion = b"\x00\x01"
    meta.MediaStorageSOPClassUID    = SecondaryCaptureImageStorage
    meta.MediaStorageSOPInstanceUID = generate_uid(prefix=OID_PREFIX)
    meta.TransferSyntaxUID          = ExplicitVRLittleEndian
    meta.ImplementationClassUID     = f"{OID_PREFIX}1"
    meta.ImplementationVersionName  = "DICOCH_3_3"

    ds = FileDataset(img.stem + ".dcm", {}, file_meta=meta, preamble=b"\0"*128)
    ds.SOPClassUID    = meta.MediaStorageSOPClassUID
    ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID

    ds.SpecificCharacterSet = ["ISO_IR 192"]
    now = dt.now()
    ds.StudyDate = ds.SeriesDate = ds.ContentDate = now.strftime("%Y%m%d")
    ds.StudyTime = ds.SeriesTime = ds.ContentTime = now.strftime("%H%M%S")

    ds.Modality = "OT"
    ds.Rows, ds.Columns = rows, cols
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = ds.BitsStored = bits
    ds.HighBit = bits - 1
    ds.PixelRepresentation = int(arr.dtype.kind == "i")

    ds.StudyInstanceUID  = generate_uid(prefix=OID_PREFIX)
    ds.SeriesInstanceUID = generate_uid(prefix=OID_PREFIX)
    ds.ImageType = ["DERIVED","SECONDARY"]

    insert_all_creators(ds)

    # 3) SQ 선처리
    for _, sq in tags[tags["vr"]=="SQ"].iterrows():
        g = int(sq["group"],16); e = int(sq["element"],16)
        seq = build_sequence(sq["group"]+sq["element"], tags)
        if seq:
            ds.add_new((g,e),"SQ",seq)

    # 3.5) 시스템/이미지 기본값 보완 (엑셀 값이 없을 때만 채움)
    tag_map = {(r["group"], r["element"]): r for _, r in tags.iterrows()}
    excel_keys = { (int(r["group"],16), int(r["element"],16)) for _, r in tags.iterrows() if r["group"] and r["element"] }
    excel_has_laterality_val = False
    try:
        sel = (tags["group"]=="0020") & (tags["element"]=="0060")
        if sel.any():
            vals = tags.loc[sel, "value"].astype(str).str.strip()
            excel_has_laterality_val = (vals != "").any()
    except Exception:
        excel_has_laterality_val = False
    img_defaults = {
        ("0028","0010"): rows, ("0028","0011"): cols,
        ("0028","0100"): bits, ("0028","0101"): bits, ("0028","0102"): bits-1,
        ("0028","0103"): ds.PixelRepresentation, ("0028","0002"): 1,
        ("0028","0004"): "MONOCHROME2",
    }
    sys_defaults = {
        ("0008","0060"): "OT", ("0008","0020"): now.strftime("%Y%m%d"),
        ("0008","0030"): now.strftime("%H%M%S"),
        ("0020","000D"): ds.StudyInstanceUID, ("0020","000E"): ds.SeriesInstanceUID,
        ("0020","0052"): generate_uid(prefix=OID_PREFIX),
        ("0008","0018"): ds.SOPInstanceUID,
    }
    for key, val in {**img_defaults, **sys_defaults}.items():
        k = _norm_tag_key(key)
        if not k:
            continue
        g_int, e_int = k
        g = (f"{g_int:04X}" if isinstance(g_int, int) else str(g_int).upper().zfill(4))
        e = (f"{e_int:04X}" if isinstance(e_int, int) else str(e_int).upper().zfill(4))
        key_str = (g, e)
        if key_str not in tag_map or (tag_map[key_str]["value"] or "") == "":
            vr = VR_DEF.get(key_str)
            if not vr:
                continue  # unknown VR for this default key; skip
            tgt = ds.file_meta if g == "0002" else ds
            tgt.add_new((int(g,16), int(e,16)), vr, safe_value(vr, val))

    # 4) 일반 태그 삽입 — 엑셀 값이 있으면 무조건 우선
    for _, r in tags.iterrows():
        if r["vr"] == "SQ":
            continue
        if not r["group"] or not r["element"]:
            continue

        g = _hex2int(r["group"]); e = _hex2int(r["element"])
        if g < 0 or e < 0:
            continue

        # parenttag 처리
        parent_ds = ds
        pt = (r.get("parenttag") or "").strip()
        if pt and not pt.endswith("0000"):
            g_p = _hex2int(pt[:4]); e_p = _hex2int(pt[4:])
            if g_p >= 0 and e_p >= 0:
                if (g_p, e_p) not in ds:
                    ds.add_new((g_p, e_p), "SQ", [Dataset()])
                seq_elem = ds[(g_p, e_p)]
                if getattr(seq_elem, "VR", "SQ") != "SQ":
                    try: del ds[(g_p, e_p)]
                    except Exception: pass
                    parent_ds = ds
                else:
                    if (not seq_elem.value) or (not isinstance(seq_elem.value[-1], Dataset)):
                        seq_elem.value.append(Dataset())
                    parent_ds = seq_elem.value[-1]

        target = ds.file_meta if g == 0x0002 else parent_ds

        # PlanarConfiguration 무시
        if (g,e) == (0x0028,0x0006) and compress_mode in ("jpeg_keep","jpeg_reencode"):
            continue

        if (g == 0x0002) and (g,e) in target:
            continue

        val = (r.get("value") or "").strip()
        if val == "":
            continue

        vr = VR_DOWN.get(r["vr"], r["vr"])
        if (g,e) in target:
            target[(g,e)].value = safe_value(vr, val)
        else:
            target.add_new((g,e), vr, safe_value(vr, val))

                # --- Post-merge cleanups & CT-specific fixes ---
        # (a) Ensure multi-valued CS like ImageType keep all components from Excel
        try:
            if (0x0008,0x0008) in ds:
                v = str(ds[(0x0008,0x0008)].value)
                parts = [p for p in re.split(r"\\|[,;/\s]+", v) if p]
                # If CT IOD and fewer than 3, supply the 3rd item heuristically
                is_ct = str(getattr(ds, "SOPClassUID", "")) == "1.2.840.10008.5.1.4.1.1.2"
                if is_ct and len(parts) < 3:
                    third = "LOCALIZER" if ("proj" in str(img.name).lower() or "local" in str(img.name).lower()) else "AXIAL"
                    while len(parts) < 2:
                        parts.append("PRIMARY")
                    parts = parts[:2] + [third]
                ds[(0x0008,0x0008)].value = [_truncate(_fix_cs(p), "CS") for p in parts]
        except Exception:
            pass
        # (a-2) If Excel provided ImageType, enforce it exactly
        try:
            if not tags.empty:
                sel = (tags["group"]=="0008") & (tags["element"]=="0008") & (tags["parenttag"]=="")
                if sel.any():
                    excel_imgtype = str(tags.loc[sel, "value"].iloc[0])
                    if excel_imgtype:
                        et_parts = [p for p in re.split(r"\\|[,;/\s]+", excel_imgtype) if p]
                        if et_parts:
                            ds[(0x0008,0x0008)].value = [_truncate(_fix_cs(p), "CS") for p in et_parts]
        except Exception:
            pass

        # (c) Never keep ModalitiesInStudy on instance — delete if present
        try:
            if (0x0008,0x0061) in ds:
                del ds[(0x0008,0x0061)]
        except Exception:
            pass

        # (b) Laterality must not be present as empty — drop if blank
        try:
            if (0x0020,0x0060) in ds:
                if not str(ds[(0x0020,0x0060)].value).strip():
                    del ds[(0x0020,0x0060)]
        except Exception:
            pass
        # (b-2) Laterality: keep only if explicitly present in Excel
        try:
            if (0x0020,0x0060) in ds and not excel_has_laterality_val:
                del ds[(0x0020,0x0060)]
        except Exception:
            pass

# 5) Rescale & PixelData
    slope, intercept = get_rescale_params(tags, gui_slope, gui_int, override)
    ds.RescaleSlope, ds.RescaleIntercept = slope, intercept
    try:
        it = list(getattr(ds, "ImageType", []))
        third = str(it[2]).upper() if len(it) >= 3 else ""
        if third == "LOCALIZER":
            try:
                del ds.RescaleType
            except Exception:
                pass
        else:
            ds.RescaleType = "HU"
    except Exception:
        ds.RescaleType = "HU"

    ext = img.suffix.lower()
    is_jpg_like = ext in (".jpg", ".jpeg")

    # default transfer syntax: uncompressed
    ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.is_little_endian = True
    ds.is_implicit_VR  = False

    if is_jpg_like and compress_mode in ("jpeg_keep","jpeg_reencode"):
        # --- JPEG Baseline (encapsulated) ---
        ds.file_meta.TransferSyntaxUID = JPEGBaseline
        if compress_mode == "jpeg_keep":
            with open(img, "rb") as f:
                jpg_bytes = f.read()
            deriv = "Converted from input JPEG (kept bitstream)"
        else:
            jpg_bytes = _encode_jpeg_baseline(arr, jpeg_quality)
            deriv = f"Re-encoded JPEG Baseline (quality={jpeg_quality}) from source"

        ds.PixelData = _encapsulate_jpeg_from_bytes(jpg_bytes)

        # Single-frame JPEG: remove PlanarConfiguration if present
        try:
            if getattr(ds.file_meta.TransferSyntaxUID, "is_compressed", False):
                if (0x0028, 0x0006) in ds:
                    del ds[(0x0028, 0x0006)]
        except Exception:
            pass

        # Photometric & bit depth for JPEG
        if ds.SamplesPerPixel == 3:
            ds.PhotometricInterpretation = "YBR_FULL_422"
            ds.BitsAllocated = 8; ds.BitsStored = 8; ds.HighBit = 7; ds.PixelRepresentation = 0
        else:
            ds.SamplesPerPixel = 1
            ds.PhotometricInterpretation = "MONOCHROME2"
            ds.BitsAllocated = 8; ds.BitsStored = 8; ds.HighBit = 7; ds.PixelRepresentation = 0

        # Lossy metadata (Excel can override later)
        try:
            raw_bytes = arr.size * arr.itemsize
            ratio = max(1e-6, raw_bytes / max(1, len(jpg_bytes)))
            ds.LossyImageCompression = "01"
            ds.LossyImageCompressionRatio = f"{ratio:.3f}"
            prev = getattr(ds, "DerivationDescription", "")
            ds.DerivationDescription = (prev + ("; " if prev else "") + deriv)
        except Exception:
            pass
    else:
        # --- Uncompressed ---
        ds.PixelData = arr.tobytes()
        try:
            ds.LossyImageCompression = "00"
        except Exception:
            pass
        # Keep Photometric/bit depth consistent with earlier geometry
        ds.SamplesPerPixel = 1
        ds.PhotometricInterpretation = "MONOCHROME2"
        ds.BitsAllocated = bits; ds.BitsStored = bits; ds.HighBit = bits - 1
        ds.PixelRepresentation = int(arr.dtype.kind == "i")

    return ds

def build_sequence(parent_key: str, tags: pd.DataFrame, _visited=None, _depth: int = 0) -> Sequence:
    # Prevent infinite recursion due to cyclic or self-referencing parenttag links
    if _visited is None:
        _visited = set()
    if parent_key in _visited:
        return Sequence([])
    if _depth > 64:
        # Hard recursion cap as a last resort
        return Sequence([])
    _visited.add(parent_key)

    children = tags[tags["parenttag"] == parent_key]
    items = []
    for _, r in children.iterrows():
        ds_item = Dataset()
        tag_tuple = (int(r["group"],16), int(r["element"],16))
        if r["vr"] == "SQ":
            sub_key = r["group"] + r["element"]
            if sub_key == parent_key:
                # Self-referencing SQ — skip to avoid infinite recursion
                continue
            sub_seq = build_sequence(sub_key, tags, _visited, _depth + 1)
            if sub_seq:
                ds_item.add_new(tag_tuple, "SQ", sub_seq)
        else:
            vr = VR_DOWN.get(r["vr"], r["vr"])
            val = safe_value(r["vr"], r["value"])
            ds_item.add_new(tag_tuple, vr, val)
        items.append(ds_item)
    return Sequence(items)

# ── 태그 밸리데이션(간단형) ───────────────────────────────────
def validate_tags(df) -> List[Dict[str,str]]:
    issues = []
    bad_da = df[(df["vr"]=="DA") & (~df["value"].str.fullmatch(r"\d{8}", na=False))]
    for _, r in bad_da.iterrows():
        issues.append({
            "group": r["group"], "element": r["element"], "vr": r["vr"],
            "value": r["value"], "error": f"DA 오류: 값 {r['value']}은(는) YYYYMMDD 형식이 아닙니다."
        })
    bad_tm = df[(df["vr"]=="TM") & (~df["value"].str.fullmatch(r"\d{6}", na=False))]
    for _, r in bad_tm.iterrows():
        issues.append({
            "group": r["group"], "element": r["element"], "vr": r["vr"],
            "value": r["value"], "error": f"TM 오류: 값 {r['value']}은(는) HHMMSS 형식이 아닙니다."
        })
    return issues

def find_manifest_url(df: pd.DataFrame) -> str:
    cand = df[(df["keyword"].str.contains("IIIF", case=False, na=False)) &
              (df["value"].str.contains("http", na=False))]
    return cand["value"].iloc[0] if not cand.empty else ""

# ── GUI ────────────────────────────────────────────────────
class ConverterGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("DICOCH DICOM Converter v3.3")
        self.geometry("1040x800")
        self._build()

    def _build(self):
        notebook = ttk.Notebook(self); notebook.pack(fill="both", expand=True)
        converter_tab, info_tab = ttk.Frame(notebook), ttk.Frame(notebook)
        notebook.add(converter_tab, text="Converter"); notebook.add(info_tab, text="Info")

        frm = ttk.Frame(converter_tab, padding=12); frm.pack(fill="both", expand=True); frm.columnconfigure(1, weight=1)

        self.e_in, self.e_tag, self.e_out = [ttk.Entry(frm) for _ in range(3)]
        ttk.Label(frm, text="Image Folder (TIFF/JPG/PNG):").grid(row=0, column=0, sticky="w")
        self.e_in.grid(row=0, column=1, sticky="ew", padx=4); ttk.Button(frm, text="Browse", command=self._pick_in).grid(row=0, column=2)

        ttk.Label(frm, text="Tag Information (Excel/CSV or Folder):").grid(row=1, column=0, sticky="w")
        self.e_tag.grid(row=1, column=1, sticky="ew", padx=4)
        tf = ttk.Frame(frm); tf.grid(row=1, column=2)
        ttk.Button(tf, text="File",   command=self._pick_tag).pack(side="left")
        ttk.Button(tf, text="Folder", command=self._pick_tag_folder).pack(side="left", padx=(4,0))


        ttk.Label(frm, text="Output Folder:").grid(row=2, column=0, sticky="w")
        self.e_out.grid(row=2, column=1, sticky="ew", padx=4); ttk.Button(frm, text="Browse", command=self._pick_out).grid(row=2, column=2)

        ttk.Label(frm, text="Slope:").grid(row=0, column=3, sticky="e")
        self.e_slope = ttk.Entry(frm, width=8); self.e_slope.insert(0, "1"); self.e_slope.grid(row=0, column=4)
        ttk.Label(frm, text="Intercept:").grid(row=1, column=3, sticky="e")
        self.e_int = ttk.Entry(frm, width=8); self.e_int.insert(0, "-1024"); self.e_int.grid(row=1, column=4)

        self.gui_override = tk.BooleanVar(value=False)
        ttk.Checkbutton(frm, text="Apply Slope/Intercept in GUI", variable=self.gui_override).grid(row=2, column=3, columnspan=2, sticky="w", pady=2)

        self.open_viewer = tk.BooleanVar(value=False)
        ttk.Checkbutton(frm, text="Open IIIF Viewer", variable=self.open_viewer).grid(row=3, column=3, sticky="w")
        ttk.Label(frm, text="Manifest URL (Optional):").grid(row=3, column=0, sticky="w")
        self.e_manifest = ttk.Entry(frm); self.e_manifest.grid(row=3, column=1, columnspan=2, sticky="ew", padx=4)

        self.auto_open = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm, text="Open Output Folder", variable=self.auto_open).grid(row=4, column=3, columnspan=2, sticky="w")

        bf = ttk.Frame(frm); bf.grid(row=4, column=0, columnspan=3, sticky="ew", pady=10)
        ttk.Button(bf, text="Convert to DCM", command=self._start).pack(side="left", expand=True, fill="x", padx=(0,4))
        ttk.Button(bf, text="Validate Tags", command=self._check).pack(side="left", expand=True, fill="x")
        ttk.Button(bf, text="Save Tag Information", command=self._save_tags).pack(side="left", expand=True, fill="x")

        self.pb  = ttk.Progressbar(frm); self.pb.grid(row=5, column=0, columnspan=5, sticky="ew")
        ttk.Label(frm, text="■ Data Processing Status").grid(row=6, column=0, sticky="w")
        self.log = scrolledtext.ScrolledText(frm, height=10); self.log.grid(row=7, column=0, columnspan=5, sticky="nsew", pady=4)

        ttk.Label(frm, text="■ Tag Processing Result").grid(row=8, column=0, sticky="w")
        self.tag_view = scrolledtext.ScrolledText(frm, height=12, font=("Consolas", 9), padx=2, pady=0, wrap="none")
        self.tag_view.grid(row=9, column=0, columnspan=5, sticky="nsew"); frm.rowconfigure(9, weight=1)

        # ── Compression 옵션 ─────────────────────────────────
        self.compress_mode = tk.StringVar(value="uncompressed")
        grp = ttk.LabelFrame(frm, text="Compression"); grp.grid(row=10, column=0, columnspan=3, sticky="ew", pady=(6,2))


        ttk.Radiobutton(grp, text="Uncompressed (Explicit VR Little Endian)",
                        variable=self.compress_mode, value="uncompressed").grid(row=0, column=0, sticky="w")

        ttk.Radiobutton(grp, text="JPEG Baseline (keep original JPG bitstream)",
                        variable=self.compress_mode, value="jpeg_keep").grid(row=1, column=0, sticky="w")

        ttk.Radiobutton(grp, text="JPEG Baseline (re-encode with quality)",
                        variable=self.compress_mode, value="jpeg_reencode").grid(row=2, column=0, sticky="w")

        ttk.Label(grp, text="Quality (re-encode):").grid(row=2, column=1, sticky="e", padx=(8,2))
        self.jpeg_q = tk.IntVar(value=90)
        ttk.Spinbox(grp, from_=50, to=100, textvariable=self.jpeg_q, width=5).grid(row=2, column=2, sticky="w")

        # ── Notebook & Tabs (중복 생성 제거·바인딩만) ───────────────
        self.nb          = notebook            # 1번째 Notebook을 클래스 속성으로 바인딩
        self.tab_convert = converter_tab
        self.tab_info    = info_tab

        # ── 정보 로그 박스(ScrolledText) — 전역(인스턴스)으로 1회 생성 ──
        self.info_box = scrolledtext.ScrolledText(
            self.tab_info, font=("Segoe UI", 11), wrap="word",
            padx=12, pady=10, state="normal"
        )
        self.info_box.tag_configure("hdr", font=("Segoe UI", 11, "bold"))
        self.info_box.tag_configure("gap", spacing1=2, spacing3=2)

        try:
            self.info_box.insert("1.0", INFO_TEXT)  # 필요 시 초기 안내문
            for line in (3, 7, 13):
                self.info_box.tag_add("hdr", f"{line}.0", f"{line}.end")
            self.info_box.tag_add("gap", "1.0", "end")
        except Exception:
            pass

        self.info_box.config(state="disabled")
        self.info_box.pack(fill="both", expand=True)


        # 태그 스타일은 여기서 '한 번만' 설정
        self.info_box.tag_configure("hdr", font=("Segoe UI", 11, "bold"))
        self.info_box.tag_configure("gap", spacing1=2, spacing3=2)

        # 초기 안내문(있다면) 삽입
        try:
            self.info_box.insert("1.0", INFO_TEXT)  # INFO_TEXT가 모듈 상단에 정의되어 있어야 함
            for line in (3, 7, 13):
                self.info_box.tag_add("hdr", f"{line}.0", f"{line}.end")
            self.info_box.tag_add("gap", "1.0", "end")
        except Exception:
            pass  # INFO_TEXT가 없다면 넘어감

        self.info_box.config(state="disabled")
        self.info_box.pack(fill="both", expand=True)

        # 편의 함수(어디서든 호출 가능)
    def _log_info(self, text: str):
        self.info_box.configure(state="normal")
        self.info_box.insert("end", text + "\n")
        self.info_box.tag_add("gap", "end-1l linestart", "end-1l lineend")  # 줄 간격 유지
        self.info_box.see("end")
        self.info_box.configure(state="disabled")



    def _pick_tag_folder(self):
        d = filedialog.askdirectory()
        if d:
            self.e_tag.delete(0, tk.END)
            self.e_tag.insert(0, d)
            # 정보 탭 로그
            self.info_box.configure(state="normal")
            self.info_box.insert("end", f"[태그 폴더 선택] {d}\n")
            self.info_box.tag_add("gap", "end-1l linestart", "end-1l lineend")
            self.info_box.see("end")
            self.info_box.configure(state="disabled")
            # 또는 헬퍼 사용: self._log_info(f"[태그 폴더 선택] {d}")




        # 예: path = 선택된 태그 폴더
        self.info_box.configure(state="normal")

        # 필요하면 INFO_TEXT를 여기서 다시 넣을 수도 있지만, 권장 경로는 _build() 초기화 시 1회 삽입
        # self.info_box.insert("end", INFO_TEXT + "\n")

        self.info_box.insert("end", f"[태그 폴더 선택] {d}\n")
        self.info_box.tag_add("gap", "end-1l linestart", "end-1l lineend")  # 줄 간격 태그
        self.info_box.see("end")
        self.info_box.configure(state="disabled")

        # 또는 헬퍼로 단순화:
        # self._log_info(f"[태그 폴더 선택] {path}")



    # 파일 선택
    def _pick_in(self):
        p = filedialog.askdirectory()
        if p:
            self.e_in.delete(0,tk.END); self.e_in.insert(0,p)
            ts=dt.now().strftime("%Y%m%d_%H%M%S")
            self.e_out.delete(0,tk.END); self.e_out.insert(0,str(Path.cwd()/f"{Path(p).name}_{ts}"))

    def _pick_tag(self):
        p = filedialog.askopenfilename(filetypes=[("Excel","*.xlsx;*.xls;*.csv;*.tsv")])
        if p:
            self.e_tag.delete(0,tk.END); self.e_tag.insert(0,p)

    def _pick_out(self):
        base = filedialog.askdirectory()
        if base:
            ts=dt.now().strftime("%Y%m%d_%H%M%S")
            self.e_out.delete(0,tk.END); self.e_out.insert(0,str(Path(base)/f"output_{ts}"))

    # 로그 도우미
    def _log(self, m): 
        try:
            self.log.insert(tk.END, m+"\n"); self.log.see(tk.END)
        except Exception:
            pass

    # 태그 검사
    def _check(self):
        try:
            src = Path(self.e_tag.get().strip())
            if not src.exists():
                messagebox.showerror("오류", "태그 경로가 없습니다."); return

            if src.is_dir():
                # ── 폴더 모드: 파일명(stem) → DataFrame 매핑
                tag_map = _load_tag_map(src)
                if not tag_map:
                    messagebox.showerror("오류", "태그 폴더에 읽을 파일이 없습니다."); return

                # 미리보기/사전(dic)용 합집합 표시
                tags_for_view = _merge_tags_for_dic(tag_map)
                self.cur_tags = tags_for_view
                self.after(0, lambda: self._update_tag_view(tags_for_view))
                # 워커 스레드 내부 예시
                #self.after(0, lambda: self._log_info(f"[태그 폴더 선택] {path}"))


                # 파일별 검증
                total_issues = 0
                for stem, df in tag_map.items():
                    issues = validate_tags(df)
                    if issues:
                        for it in issues:
                            self._log(f"[{stem}] {it}")
                        total_issues += len(issues)

                if total_issues == 0:
                    self._log("No Tag Structure Issues (folder mode)")
                    messagebox.showinfo("검사 결과", "No Tag Structure Issues")
                else:
                    messagebox.showwarning("검사 결과", f"Issues: {total_issues}개 (로그 확인)")

            else:
                # ── 단일 파일 모드(기존 동작)
                tags = load_tags(src)
                self.cur_tags = tags
                self.after(0, lambda: self._update_tag_view(tags))

                issues = validate_tags(tags)
                if issues:
                    for it in issues:
                        self._log(str(it))
                    messagebox.showwarning("검사 결과", f"Issues: {len(issues)}개 (로그 확인)")
                else:
                    self._log("No Tag Structure Issues")
                    messagebox.showinfo("검사 결과", "No Tag Structure Issues")

        except Exception as e:
            # 안전 로그
            self._log(f"[check] {e}")
            messagebox.showerror("오류", str(e))


    def _start(self):
        self.pb.config(value=0)
        threading.Thread(target=self._convert, daemon=True).start()

    def _update_tag_view(self, df: pd.DataFrame) -> None:
        df = df.copy()
        widths = {c: max(len(c), df[c].astype(str).map(len).max()) + 2 for c in df.columns}
        pretty = df.to_string(index=False, col_space=widths, justify="left")
        self.tag_view.config(state="normal"); self.tag_view.delete("1.0", tk.END)
        self.tag_view.insert(tk.END, pretty)
        self.tag_view.tag_add("tight", "1.0", "end"); self.tag_view.tag_configure("tight", spacing1=0, spacing3=0)
        self.tag_view.config(state="disabled")

    def _save_tags(self):
        if not hasattr(self, "cur_tags"):
            messagebox.showwarning("저장", "변환된 태그 정보가 없습니다."); return
        out_dir = Path(self.e_out.get())
        if not out_dir.exists():
            messagebox.showerror("저장", "출력 폴더가 없습니다."); return

        ts = dt.now().strftime("%Y%m%d_%H%M%S")
        txt_path  = out_dir / f"tag_info_{ts}.txt"
        xlsx_path = out_dir / f"tag_info_{ts}.xlsx"
        json_path = out_dir / f"tag_info_{ts}.json"

        tags_df = self.cur_tags.copy()
        if hasattr(self, "cur_tag_errors") and not getattr(self, "cur_tag_errors", pd.DataFrame()).empty:
            errs = self.cur_tag_errors.copy(); errs["key"] = errs["group"] + errs["element"]
            tags_df["key"] = tags_df["group"] + tags_df["element"]
            tags_df = tags_df.merge(errs[["key","error"]], on="key", how="left").drop(columns=["key"])
            tags_df["error"] = tags_df["error"].fillna("")
        else:
            tags_df["error"] = ""

        tags_df.to_csv(txt_path, sep="\t", index=False)
        tags_df.to_excel(xlsx_path, index=False)
        json_path.write_text(json.dumps(tags_df.to_dict(orient="records"), ensure_ascii=False, indent=2), encoding="utf-8")

        self._log(f"[Tag Save] {txt_path.name}, {xlsx_path.name}, {json_path.name}")
        messagebox.showinfo("저장 완료", f"TXT / XLSX / JSON 저장\n{txt_path}\n{xlsx_path}\n{json_path}")

    def _collect_images(self, in_dir: Path) -> List[Path]:
        imgs: List[Path] = []
        for ext in ("*.tif", "*.tiff", "*.jpg", "*.jpeg", "*.png"):
            imgs.extend(sorted([p for p in in_dir.glob(ext) if p.is_file()]))
        return imgs

    def _convert(self):
        try:
            in_dir  = Path(self.e_in.get().strip())
            tag_src = Path(self.e_tag.get().strip())   # 파일 or 폴더
            out_dir = Path(self.e_out.get().strip())
            if not in_dir.is_dir() or not tag_src.exists():
                messagebox.showerror("오류","입력/태그 경로 확인"); return

            imgs = self._collect_images(in_dir)
            if not imgs:
                messagebox.showerror("오류","이미지 없음 (지원: TIFF/JPG/PNG)"); return

            gui_slope = float(self.e_slope.get() or 1)
            gui_int   = float(self.e_int.get() or -1024)

            # ── 태그 로딩: 단일 파일 vs 폴더 매핑 모드
            tag_dir_mode = tag_src.is_dir()
            if tag_dir_mode:
                # 폴더 안의 *.xlsx/*.xls/*.csv/*.tsv → {stem: DataFrame}
                tag_map = _load_tag_map(tag_src)
                if not tag_map:
                    messagebox.showerror("오류","태그 폴더에서 읽을 파일이 없습니다."); return

                # 미리보기/사전(dic) 생성용 합집합
                tags_for_view = _merge_tags_for_dic(tag_map)
                self.cur_tags = tags_for_view
                self.after(0, lambda: self._update_tag_view(tags_for_view))

                out_dir.mkdir(parents=True, exist_ok=True)
                tmp_dic = write_dic(tags_for_view, out_dir)
                dic_fname = f"Dic_DICOCH_DICOM_PrivateTag_{dt.now():%Y%m%d_%H%M%S}.txt"
                dic_path  = tmp_dic.with_name(dic_fname); tmp_dic.rename(dic_path)
                self._log(f"[{dic_fname}] {dic_path}")
            else:
                # 단일 파일: 기존 동작(모든 이미지에 동일 태그 적용)
                tags = load_tags(tag_src)
                self.cur_tags = tags
                self.after(0, lambda: self._update_tag_view(tags))

                out_dir.mkdir(parents=True, exist_ok=True)
                tmp_dic = write_dic(tags, out_dir)
                dic_fname = f"Dic_DICOCH_DICOM_PrivateTag_{dt.now():%Y%m%d_%H%M%S}.txt"
                dic_path  = tmp_dic.with_name(dic_fname); tmp_dic.rename(dic_path)
                self._log(f"[{dic_fname}] {dic_path}")

            # 미리보기/폴더모드에서도 cur_tags를 기준으로 IIIF를 찾도록
            manifest_val = find_manifest_url(self.cur_tags)
            if manifest_val: self._log(f"[IIIF Link] {manifest_val}")

            log_f = (out_dir / f"log_{dt.now():%Y%m%d_%H%M%S}.txt").open("w", encoding="utf-8")
            if manifest_val: log_f.write(f"[IIIF Link] {manifest_val}\n")

            succ = fail = 0
            lock = threading.Lock()
            self.pb.config(maximum=len(imgs))

            mode = self.compress_mode.get()
            q    = int(self.jpeg_q.get())

            def task(fp: Path):
                nonlocal succ, fail
                try:
                    # ── 이미지별 태그 선택: 폴더 모드면 stem으로 매칭
                    if tag_dir_mode:
                        key = fp.stem.lower()
                        if key not in tag_map:
                            raise FileNotFoundError(f"Tag not found for image '{fp.name}' (search key='{key}')")
                        tags_df = tag_map[key]
                    else:
                        tags_df = tags

                    ds = build_dataset(
                        fp, tags_df, gui_slope, gui_int,
                        override=self.gui_override.get(),
                        compress_mode=self.compress_mode.get(),
                        jpeg_quality=int(self.jpeg_q.get() if hasattr(self, "jpeg_q") else 90)
                    )
                                        # 저장 직전 강제 검증
                    _validate_image_pixel_module(ds)

                    pydicom.dcmwrite(
                        out_dir / f"{fp.stem}.dcm", ds,
                        # Encapsulated(압축)일 때 원본 스트림 보존
                        write_like_original=(self.compress_mode.get() != "uncompressed")
                    )

                    with lock: succ += 1
                    return f"✔ {fp.name}" + (f"  ← tag='{key}'" if tag_dir_mode else "")
                except Exception as e:
                    tb = traceback.format_exc()
                    with lock: fail += 1
                    return f"✖ {fp.name} → {e}\n{tb}"

            with cf.ThreadPoolExecutor(max_workers=max(1, os.cpu_count()//2)) as ex:
                for idx, msg in enumerate(ex.map(task, imgs), 1):
                    self._log(msg); log_f.write(msg+"\n"); self.pb.config(value=idx)

            summary = f"Completed {succ}   Failed {fail}"
            self._log(summary); log_f.write(summary+"\n"); log_f.close()
            messagebox.showinfo("Completed", summary)

            if self.auto_open.get() and succ and not fail:
                try:
                    os.startfile(out_dir)
                except AttributeError:
                    opener = "open" if sys.platform=="darwin" else "xdg-open"
                    subprocess.Popen([opener, str(out_dir)])

            if self.open_viewer.get():
                manifest = self.e_manifest.get().strip() or manifest_val
                if manifest:
                    url = MIRADOR_DEMO + manifest
                    self._log(f"[Mirador] {url}")
                    webbrowser.open_new_tab(url)
                else:
                    self._log("Manifest URL이 없어 IIIF 뷰어 호출 생략")

        finally:
            self.pb.config(value=0)


# ── 실행 ────────────────────────────────────────────────────
def main():
    try:
        import ctypes; ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    ConverterGUI().mainloop()

if __name__ == "__main__":
    main()
