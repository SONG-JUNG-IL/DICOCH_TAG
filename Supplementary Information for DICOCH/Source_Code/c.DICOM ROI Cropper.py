# -*- coding: utf-8 -*-
import unicodedata, shutil, tempfile, pathlib, re, sys
from pathlib import Path
import os, io, gzip, math, tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from datetime import datetime
from PIL import Image, ImageTk, ImageEnhance, ImageDraw
import numpy as np, pydicom
from pydicom.valuerep import DS
from pydicom.uid import ExplicitVRLittleEndian, JPEGLSLossless, UID, generate_uid
from PIL import ImageFont
# [ADD] ───────────────────────────────────────────────────────────────────
import re
import uuid
import numpy as np

# ======= Third-party (optional guards) =======
try:
    import psutil
    HAS_PSUTIL = True
except Exception:
    HAS_PSUTIL = False

try:
    import nrrd  # pynrrd
    HAS_NRRD = True
except Exception:
    HAS_NRRD = False

# pydicom UID helpers (일부 함수가 직접 사용)
try:
    from pydicom.uid import UID, generate_uid
except Exception:
    UID = lambda x: x
    def generate_uid(prefix="1.2.826.0.1.3680043.10.1234."):
        # 아주 예외적인 폴백
        return prefix + datetime.now().strftime("%Y%m%d%H%M%S%f")

# ======= Tiny helpers used widely =======
def p(*args, **kwargs):
    """간단한 print 별칭(코드 전반에 p(...)가 있을 때 대비)"""
    print(*args, **kwargs)

def _safe_float_list(val, n=None):
    """
    DICOM 속성(list/tuple/str 등)에서 안전하게 float 리스트를 만든다.
    n을 주면 길이를 n으로 패딩/절단한다.
    """
    out = []
    try:
        if isinstance(val, (list, tuple)):
            out = [float(x) for x in val]
        else:
            # 공백/콤마/백슬래시 구분 모두 허용
            s = str(val).replace("\\", " ")
            parts = re.split(r"[,\s]+", s.strip())
            out = [float(x) for x in parts if x != ""]
    except Exception:
        out = []
    if n is not None:
        out = (out + [0.0]*n)[:n]
    return out


def _infer_slice_spacing_from_ipp(slices):
    """
    Robustly infer average slice spacing (mm) and std from a list of pydicom datasets.
    Uses ImagePositionPatient (IPP) and ImageOrientationPatient (IOP) when available.
    Returns (mean_gap_mm, std_gap_mm); if not computable, returns (None, None).
    """
    import numpy as np, math
    if not slices or len(slices) < 2:
        return (None, None)

    ipps = []
    normals = []
    for ds in slices:
        try:
            ipp = [float(v) for v in getattr(ds, "ImagePositionPatient")]
        except Exception:
            ipp = None
        ipps.append(ipp)
        try:
            iop = [float(v) for v in getattr(ds, "ImageOrientationPatient")]
            if len(iop) == 6:
                r = np.array(iop[:3], dtype=float); c = np.array(iop[3:6], dtype=float)
                r /= (np.linalg.norm(r)+1e-12); c /= (np.linalg.norm(c)+1e-12)
                n = np.cross(r, c); n /= (np.linalg.norm(n)+1e-12)
                normals.append(n)
        except Exception:
            pass
    nvec = None
    if normals:
        nvec = np.mean(np.stack(normals, axis=0), axis=0)
        nvec = nvec / (np.linalg.norm(nvec) + 1e-12)

    idxs = list(range(len(slices)))
    def sort_key(i):
        if ipps[i] is None:
            return float(getattr(slices[i], "InstanceNumber", i))
        if nvec is not None:
            return float(np.dot(np.array(ipps[i], dtype=float), nvec))
        try:
            return float(ipps[i][2])
        except Exception:
            return float(getattr(slices[i], "InstanceNumber", i))
    idxs.sort(key=sort_key)

    gaps = []
    for a, b in zip(idxs[:-1], idxs[1:]):
        p1, p2 = ipps[a], ipps[b]
        if p1 is None or p2 is None:
            continue
        if nvec is not None:
            d = abs(float(np.dot(np.array(p2)-np.array(p1), nvec)))
        else:
            try:
                d = abs(float(p2[2]) - float(p1[2]))
            except Exception:
                d = None
        if d is not None and not math.isnan(d) and d > 0:
            gaps.append(d)
    if not gaps:
        return (None, None)
    gaps = np.array(gaps, dtype=float)
    return float(np.mean(gaps)), float(np.std(gaps))


def _phys_shift_ipp(ds, x_px, y_px, sx_mm, sy_mm):
    """
    Shift ImagePositionPatient by (x_px, y_px) pixels with per-pixel spacing (sx_mm, sy_mm).
    Uses ImageOrientationPatient (0020,0037) to map pixel axes to patient coordinates.
    Fallback: axis-aligned X/Y if IOP is missing or malformed.
    Returns a [x, y, z] list of floats.
    """
    try:
        ipp = ds.get((0x0020,0x0032), None) or ds.get("ImagePositionPatient", None)
        if ipp is None:
            return None
        ipp = [float(v) for v in list(ipp)]
        x_px = float(x_px); y_px = float(y_px)
        sx   = float(sx_mm); sy   = float(sy_mm)

        iop = ds.get((0x0020,0x0037), None) or ds.get("ImageOrientationPatient", None)
        if iop is not None and len(list(iop)) == 6:
            r = [float(i) for i in list(iop)[:3]]   # row direction cosines
            c = [float(i) for i in list(iop)[3:]]  # col direction cosines
            shift = [x_px*sx*r[0] + y_px*sy*c[0],
                     x_px*sx*r[1] + y_px*sy*c[1],
                     x_px*sx*r[2] + y_px*sy*c[2]]
            return [ipp[0]+shift[0], ipp[1]+shift[1], ipp[2]+shift[2]]
        else:
            # Fallback: assume orthonormal axes aligned to patient X/Y
            return [ipp[0] + x_px*sx, ipp[1] + y_px*sy, ipp[2]]
    except Exception:
        try:
            return [float(ipp[0]), float(ipp[1]), float(ipp[2])]
        except Exception:
            return None



def _build_crop_volume(self, zidx, ys, xs, hs, ws, dtype_hint=None):
    """
    Build 3D ROI volume (Z,Y,X).  ⚠ 정합/계측 모드에서는 Z 디시메이션 금지.
    Returns vol (np.ndarray), zsel (사용한 Z 인덱스), stride(=1)
    """
    import numpy as np
    # --- 기존 메모리 계산/stride 결정 로직은 유지하되 ---
    stride = 1

    # ✅ 정합/계측 모드면 무조건 stride=1로 강제
    if getattr(self, "mode_reg_measure", None) and bool(self.mode_reg_measure.get()):
        stride = 1
    else:
        # (보기/경량화용) 메모리 초과 시에만 stride>1 허용
        try:
            import psutil
            cap = int(psutil.virtual_memory().available * 0.60)
        except Exception:
            cap = 2 * 1024**3  # fallback 2 GiB
        sample = self.stack[zidx[0]][ys:ys+hs, xs:xs+ws]
        dtype  = sample.dtype if dtype_hint is None else np.dtype(dtype_hint)
        need   = len(zidx) * hs * ws * np.dtype(dtype).itemsize
        if need > cap:
            stride = max(1, int((need + cap - 1) // cap))  # ceil
            # 로그 남기기
            try: self._log(f"[Stack] mem cap → Z decimate by {stride}")
            except Exception: pass

    zsel = zidx[::stride]          # <- 여기서 최종 사용 Z 인덱스 확정
    vol  = np.empty((len(zsel), hs, ws), dtype=(self.stack[zidx[0]][ys:ys+hs, xs:xs+ws]).dtype if dtype_hint is None else np.dtype(dtype_hint))
    for i, z in enumerate(zsel):
        vol[i] = self.stack[z][ys:ys+hs, xs:xs+ws]

    # ✅ 감사 로그에 stride 기록(문제 재발 추적)
    try:
        if hasattr(self, "_resample_audit_log"):
            src_shape   = (len(zidx), hs, ws)      # (Z,Y,X) 관점
            dst_shape   = (len(zsel), hs, ws)
            src_spacing = (self.orig_sx, self.orig_sy, float(self.orig_sz or 0.0))
            dst_spacing = src_spacing  # 정합/계측: spacing 불변
            note = f"z_stride={stride}"
            self._resample_audit_log(self.out_dir, "build_vol", "ROI",
                                     src_shape[::-1], src_spacing,  # 로그가 XxYxZ 포맷이면 뒤집기
                                     dst_shape[::-1], dst_spacing,
                                     method="crop_only", note=note)
    except Exception:
        pass

    return vol, zsel, stride


# ──────────────────────────────────────────────────────────────────────────────
# Numeric → DS formatter (safe for PixelSpacing, SliceThickness, etc.)
# ──────────────────────────────────────────────────────────────────────────────



def _ipp_for_iso_slice(base_ipp, normal_vec, sz_mm, k):
    """IPP_k = base_ipp + k * sz_mm * normal_vec"""
    bx, by, bz = [float(v) for v in base_ipp]
    nx, ny, nz = [float(v) for v in normal_vec]
    s = float(sz_mm) * float(k)
    return [bx + nx*s, by + ny*s, bz + nz*s]



def _row_col_normal_from_iop(iop):
    """Return (row, col, normal) from ImageOrientationPatient (6 floats)."""
    r = [float(i) for i in iop[:3]]
    c = [float(i) for i in iop[3:6]]
    n = [r[1]*c[2]-r[2]*c[1], r[2]*c[0]-r[0]*c[2], r[0]*c[1]-r[1]*c[0]]
    import math
    norm = math.sqrt(n[0]**2+n[1]**2+n[2]**2)
    if norm > 0:
        n = [n[0]/norm, n[1]/norm, n[2]/norm]
    return r, c, n



def _fmt(x, digits: int = 6):
    """
    Format a number as a DICOM DS (Decimal String) with length and notation control.
    Returns a pydicom DS when possible; otherwise falls back to plain string.
    """
    try:
        val = float(x)
        s = f"{val:.{digits}g}"                # compact; may use scientific
        if "e" in s or "E" in s:
            s = f"{val:.{digits}f}".rstrip("0").rstrip(".")  # avoid scientific for DS
        return DS(s)  # pydicom DS ensures VR=DS constraints
    except Exception:
        try:
            return DS(str(x))
        except Exception:
            return str(x)

# ──────────────────────────────────────────────────────────────────────────────
# Geometry helper: infer slice spacing from IPP (+IOP if present)
# ──────────────────────────────────────────────────────────────────────────────

# ──────────────────────────────────────────────────────────────────────────────
# Geometry helper: shift IPP by pixel offsets using IOP and Pixel Spacing
# ──────────────────────────────────────────────────────────────────────────────



# ──────────────────────────────────────────────────────────────────────────────
# UID sanitizer: ensure invalid UIDs are replaced by valid, short ones
# ──────────────────────────────────────────────────────────────────────────────
UID_PREFIX = "1.2.392.200036.9125.20"  # generic site prefix (<= 24 chars component), adjust as needed

def _fix_or_gen_uid(val: str) -> str:
    from pydicom.uid import UID, generate_uid
    try:
        u = UID(val)
        # Validate length and allowed chars
        if len(str(u)) <= 64 and all(c.isdigit() or c=='.' for c in str(u)):
            return str(u)
    except Exception:
        pass
    return generate_uid(prefix=UID_PREFIX + ".")

def sanitize_uids_in_dataset(ds, prefix: str | None = None):
    """Repair common UID fields in-place. Does not touch StudyDate etc."""
    from pydicom.uid import generate_uid
    if prefix is None:
        prefix = UID_PREFIX
    # file meta
    fm = getattr(ds, "file_meta", None)
    if fm is not None:
        for tag in ("MediaStorageSOPClassUID", "MediaStorageSOPInstanceUID", "ImplementationClassUID", "ImplementationVersionName"):
            if hasattr(fm, tag):
                try:
                    val = getattr(fm, tag)
                    if isinstance(val, (bytes, bytearray)):
                        val = val.decode("ascii", "ignore")
                    setattr(fm, tag, _fix_or_gen_uid(str(val)))
                except Exception:
                    setattr(fm, tag, generate_uid(prefix=prefix + "."))
    # dataset core
    for tag in ("SOPClassUID", "SOPInstanceUID", "StudyInstanceUID", "SeriesInstanceUID", "FrameOfReferenceUID"):
        if hasattr(ds, tag):
            try:
                val = getattr(ds, tag)
                if isinstance(val, (bytes, bytearray)):
                    val = val.decode("ascii", "ignore")
                setattr(ds, tag, _fix_or_gen_uid(str(val)))
            except Exception:
                from pydicom.uid import generate_uid
                setattr(ds, tag, generate_uid(prefix=prefix + "."))


def ascii_sanitize(name: str, fallback: str="out") -> str:
    s = unicodedata.normalize("NFKD", name)
    s = "".join(ch if (0x20 <= ord(ch) < 0x7f and ch not in '\\/:*?"<>|') else "_" for ch in s)
    s = re.sub(r"_+", "_", s).strip("._")
    return s or fallback

def make_ascii_subdir(base_dir: str, human_name: str, prefix: str) -> str:
    os.makedirs(base_dir, exist_ok=True)
    safe = ascii_sanitize(human_name) or "Dataset"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(base_dir, f"{prefix}_{safe}_{ts}")
    os.makedirs(out_dir, exist_ok=True)
    return out_dir

def ensure_valid_uid(val: str) -> str:
    try:
        UID(val)
        return str(UID(val))
    except Exception:
        return generate_uid()

# Note: HAS_SITK, HAS_GDCM 플래그는 본문 import try/except에서 이미 설정되어 있다고 가정
def _save_nrrd(arr3d, spacing_xyz, origin_xyz, direction_3x3,
               out_dir, base_name, mode="nrrd"):
    """
    NRRD 저장:
      - mode="nrrd": 단일 .nrrd (gzip)
      - mode="nhdr+raw.gz": .nhdr + .raw.gz
    SimpleITK 사용 시 .nrrd 우선 시도; 경로 이슈면 ASCII 임시 경로 후 move.
    마지막 폴백은 .nhdr+.raw.gz 수동 writer.
    """
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.join(out_dir, base_name)
    z, y, x = arr3d.shape
    sx, sy, sz = spacing_xyz

    if mode == "nrrd" and 'HAS_SITK' in globals() and HAS_SITK:
        import SimpleITK as sitk  # 안전
        import numpy as np        # 안전
        img = sitk.GetImageFromArray(arr3d)  # z,y,x
        img.SetSpacing(tuple(spacing_xyz))
        img.SetOrigin(tuple(origin_xyz))
        if direction_3x3 is not None:
            flat = tuple([float(v) for v in np.asarray(direction_3x3).reshape(-1)])
            img.SetDirection(flat)
        target_path = stem + ".nrrd"
        try:
            sitk.WriteImage(img, target_path, useCompression=True)
            return target_path
        except Exception:
            tmp_dir = tempfile.mkdtemp(prefix="nrrd_tmp_")
            safe_base = ascii_sanitize(os.path.basename(stem))
            tmp_path = os.path.join(tmp_dir, safe_base + ".nrrd")
            sitk.WriteImage(img, tmp_path, useCompression=True)
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            shutil.move(tmp_path, target_path)
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return target_path

    # 수동 .nhdr + .raw.gz
    nhdr_path = stem + ".nhdr"
    rawgz_path = stem + ".raw.gz"
    header = (
        "NRRD0005\n"
        "# Created by ROI Cropper\n"
        "type: ushort\n"
        "dimension: 3\n"
        f"sizes: {x} {y} {z}\n"
        "encoding: gzip\n"
        "endian: little\n"
        "space: left-posterior-superior\n"
        f"space directions: ({sx},0,0) (0,{sy},0) (0,0,{sz})\n"
        f"space origin: ({origin_xyz[0]},{origin_xyz[1]},{origin_xyz[2]})\n"
        f"data file: {os.path.basename(rawgz_path)}\n"
    )
    with open(nhdr_path, "w", encoding="utf-8") as f:
        f.write(header)
    with gzip.open(rawgz_path, "wb") as gz:
        gz.write(arr3d.astype("uint16").tobytes(order="C"))
    return nhdr_path

def _save_dicom_uncompressed(ds, out_path):
    ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.is_implicit_VR = False
    ds.is_little_endian = True
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    sanitize_uids_in_dataset(ds, UID_PREFIX)
    ds.save_as(out_path, write_like_original=False)

def _save_dicom_jpegls(ds, out_path):
    """
    JPEG-LS 무손실 저장 (UID=1.2.840.10008.1.2.4.80).
    GDCM가 없으면 False 반환(호출부에서 무압축 폴백).
    """
    if 'HAS_GDCM' not in globals() or not HAS_GDCM:
        return False
    import gdcm
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    out_dir, out_name = os.path.dirname(out_path), os.path.basename(out_path)
    safe_out = os.path.join(out_dir, ascii_sanitize(out_name))

    tmp_dir = tempfile.mkdtemp(prefix="dcm_tmp_")
    try:
        tmp_name = ascii_sanitize(os.path.splitext(out_name)[0]) + ".uncompressed.dcm"
        tmp_path = os.path.join(tmp_dir, tmp_name)

        _save_dicom_uncompressed(ds, tmp_path)

        reader = gdcm.Reader(); reader.SetFileName(tmp_path)
        if not reader.Read(): return False

        comp = gdcm.ImageChangeTransferSyntax()
        comp.SetTransferSyntax(gdcm.TransferSyntax(gdcm.TransferSyntax.JPEGLSLossless))
        de = reader.GetFile().GetDataSet().GetDataElement(gdcm.Tag(0x7fe0,0x0010))
        comp.SetInput(de)
        if not comp.Change(): return False

        file_g = reader.GetFile()
        file_g.GetHeader().SetDataSetTransferSyntax(gdcm.TransferSyntax(gdcm.TransferSyntax.JPEGLSLossless))
        file_g.GetDataSet().Replace(comp.GetOutput())

        writer = gdcm.Writer()
        writer.SetFile(file_g)
        writer.SetFileName(safe_out)
        ok = writer.Write()
        if not ok: return False

        if safe_out != out_path:
            try:
                if os.path.exists(out_path):
                    os.remove(out_path)
                os.replace(safe_out, out_path)
            except Exception:
                pass
        return True
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
# ======= end of global utilities =======


# 선택적 의존성
try:
    import SimpleITK as sitk
    HAS_SITK = True
except Exception:
    HAS_SITK = False

try:
    import gdcm
    HAS_GDCM = True
except Exception:
    HAS_GDCM = False

# [ADD] ───────────────────────────────────────────────────────────────────
def _next_outpath_dcm(self, base_name=None):
    name = (base_name or "output") + self.output_suffix + ".dcm"
    return self.out_dir / name   # self.out_dir: 출력 폴더 Path

# [REPLACE] — z-spacing 전달인자 추가
def save_dicom_cropped(self, ds_template, pixel_array, crop_dx_px:int, crop_dy_px:int,
                       base_name=None, z_spacing_used:float|None=None):
    ds = ds_template.copy()
    H, W = int(pixel_array.shape[0]), int(pixel_array.shape[1])
    ds.Rows, ds.Columns = H, W
    ds.PixelData = pixel_array.tobytes()

    # spacing 불변
    if self.orig_sx and self.orig_sy:
        ds.PixelSpacing = [str(self.orig_sx), str(self.orig_sy)]
    if self.orig_sz is not None:
        ds.SliceThickness = str(self.orig_sz)

    # SpacingBetweenSlices 기입(있다면)
    if z_spacing_used is None:
        z_spacing_used = float(self.orig_sz or 0.0)
    try:
        ds.SpacingBetweenSlices = str(float(z_spacing_used))
    except Exception:
        pass

    # IPP 보정
    sx, sy = map(float, ds.PixelSpacing)
    self._shift_ipp_by_crop(ds, crop_dx_px, crop_dy_px, sx, sy)

    outpath = self._next_outpath_dcm(base_name)
    ds.save_as(outpath, write_like_original=False)

    # 감사 로그(항상 기록)
    try:
        self.audit_writer.writerow({
            "axis_order": "N/A (DICOM slice)",
            "dz_mean_ipp": "", "dz_std_ipp": "",
            "sx": float(ds.PixelSpacing[0]), "sy": float(ds.PixelSpacing[1]),
            "sz_used": float(getattr(ds, "SliceThickness", 0)),
        })
    except Exception:
        pass

    return outpath, ds


def _ascii_slug(name: str, fallback: str="out") -> str:
    s = unicodedata.normalize("NFKD", name)
    s = "".join(ch if (0x20 <= ord(ch) < 0x7f and ch not in '\\/:*?"<>|') else "_" for ch in s)
    s = re.sub(r"_+", "_", s).strip("._")
    return s or fallback

class DICOMRoiBatchApp:
    
    # Generic fallback: if an attribute (e.g., UI handler) is missing on the instance,
    # but a same-named global function exists, return a bound callable to that function.
    def __getattr__(self, name):
        g = globals().get(name)
        if callable(g):
            return lambda *a, **k: g(self, *a, **k)
        raise AttributeError(name)

    def __init__(self, root):
        self.root = root
        self.root.title("DICOM ROI Cropper – 3D, IPP spacing, Batch, JPEG-LS, NRRD_NRICH_SONG JUNG IL")

        self.slices = []
        self.stack = None
        self.slice_index = 0

        self.roi = []
        self.start_pt = None
        self.contrast = 1.0

        # 원본 메타
        self.orig_sx = self.orig_sy = self.orig_th = 1.0
        self.orig_slope = 1.0
        self.orig_inter = -1024.0

        # 현재 편집 메타
        self.sx = self.sy = self.th = 1.0
        self.slope = 1.0
        self.inter = -1024.0

        # Z 범위
        self.z_start = 0
        self.z_end   = 0
        self.invert_z = tk.BooleanVar(value=False)

        # --- HU/Point 측정 상태값 추가 ---
        self.point_mode = tk.BooleanVar(value=False)  # 포인트 HU 모드 on/off
        self.points = []                               # [{'idx','slice','x','y','hu'}]
        self.point_idx = 0
        self._last_preview_img = None  # 최근 프리뷰(L, 512x512) 저장 → 화면 Gray 산출용


        # 출력 옵션 (UI에서 사용하므로 _build_ui() 이전에 준비)
        self.out_kind = tk.StringVar(value="DICOM")      # "DICOM" or "NRRD"
        self.ts_jpegls = tk.BooleanVar(value=False)      # JPEG-LS 무손실
        self.nrrd_mode = tk.StringVar(value="nrrd")      # "nrrd" or "nhdr+raw.gz"
        self.force_ascii_path = tk.BooleanVar(value=True)# ASCII 전용 경로 강제

        self.batch = []  # list of dict(name, xywh, z0, z1)


        self.overlay_show_values = tk.BooleanVar(value=True)  # 이미지 위 값 라벨 표시
        self.last_roi_stats = None  # {'slice','xs','ys','ws','hs','mean','std','min','max'}
        self.last_save_dir = ""     # 저장 루틴이 갱신
        self.dicom_dir = ""         # load_dicom에서 채움
        # 표시 설정(기본: 노랑)
        self.roi_color   = tk.StringVar(value="yellow")  # "yellow" 또는 "red"
        self.point_color = tk.StringVar(value="yellow")  # 포인트 마커 색
        self.roi_color_auto = tk.BooleanVar(value=True)  # ← 자동 대비 색상(기본 켜기 권장)


        # 톤 보정 상태
        self.contrast   = 1.0    # 대비 (기존)
        self.brightness = 1.0    # 밝기
        self.gamma      = 1.0    # 감마

        # 자동 저장 옵션
        self.auto_save_preview = tk.BooleanVar(value=False)  # 톤 변경마다 512 프리뷰 PNG 자동 저장
        self.auto_save_hu      = tk.BooleanVar(value=False)  # 포인트/ROI HU 측정 자동 저장

        # 자동 저장 디바운스(연속 드래그 폭주 방지)
        self._last_preview_save_ts = 0.0
        self._preview_autosave_debounce_sec = 0.7
        
        self.xy_resample = tk.BooleanVar(value=True)  # ROI 저장 시 XY 리샘플 실행
        self.xy_target   = tk.IntVar(value=512)       # 목표 폭(px). 높이는 비율 유지
        self.xy_method   = tk.StringVar(value="linear")  # "linear"|"bspline"|"nearest"

        # === ISO 3D 리샘플 옵션 ===
        self.iso_on     = tk.BooleanVar(value=False)  # 켜면 XYZ 모두 등방성으로 리샘플
        self.iso_mm     = tk.DoubleVar(value=0.20)    # 목표 voxel 크기(mm)
        self.iso_method = tk.StringVar(value="linear")  # "linear"|"bspline"|"nearest"

        # --- ROI 도구 상태 ---
        self.lock_square       = tk.BooleanVar(value=False)   # 정사각형 잠금
        self.center_draw       = tk.BooleanVar(value=False)   # 중심 기준 드래그
        self.fixed_size_mode   = tk.BooleanVar(value=False)   # 고정 크기 사용
        self.fixed_unit        = tk.StringVar(value="mm")     # "px" or "mm"
        self.fixed_w           = tk.DoubleVar(value=10.0)     # 고정 가로 (mm 또는 px)
        self.fixed_h           = tk.DoubleVar(value=10.0)     # 고정 세로 (mm 또는 px)

        # 숫자 직접입력용 ROI(프리뷰 512 좌표계 기준)
        self.roi_x = tk.IntVar(value=0)
        self.roi_y = tk.IntVar(value=0)
        self.roi_w = tk.IntVar(value=0)
        self.roi_h = tk.IntVar(value=0)

        # [ADD/REPLACE] ───────────────────────────────────────────────────────────
        # 프리셋: 정합/계측 모드 ON → XY 리샘플 OFF
        self.mode_reg_measure  = tk.BooleanVar(value=True)
        self.xy_resample       = tk.BooleanVar(value=False)   # 기본 OFF
        self.keep_spacing_meta = tk.BooleanVar(value=False)   # 정합/계측에선 사용 안 함
        self.output_suffix     = "_cropped_reg"

        # z-spacing 검증 임계값(기본 0.02mm)
        self.z_eps_mm = tk.DoubleVar(value=0.02)

        # 원본 spacing 백업
        self.orig_sx = self.orig_sy = self.orig_sz = None


        # --- Polygon ROI 상태 ---
        self.poly_mode   = tk.BooleanVar(value=False)  # 폴리곤 ROI 모드 on/off
        self.poly_pts    = []      # [(x,y), ...]  — 512 프리뷰 좌표계
        self.poly_closed = False   # 다각형 닫힘 여부
        self.poly_hover  = None    # 마우스 이동 중 임시 끝점

        # 키 바인딩(토글/닫기/취소/되돌리기)
        self.root.bind("<KeyPress-p>", lambda e: self._toggle_poly_mode())
        self.root.bind("<Return>",     lambda e: self._poly_close())
        self.root.bind("<Escape>",     lambda e: self._poly_clear())
        self.root.bind("<Control-z>",  lambda e: self._poly_undo())
        # 키보드 바인딩
        self.root.bind("<Key>", self._on_key)
                # [ADD] — 초기 상태 보장
        self._apply_reg_measure_preset()

        # ── Window/Level 상태 (뷰 슬라이스 표시용)
        self.win_width  = tk.DoubleVar(value= 2037.0)   # WW
        self.win_level  = tk.DoubleVar(value= 144.0) # WL (예: CT)
        self._link_ww_wl = tk.BooleanVar(value=False)  # 필요시 잠금 토글

        # 슬라이더 이벤트 디바운스용
        self._wlw_debounce_id = None


        self._build_ui()

    # ─────────────────────────────────────────────────────────────────────
    # 설명 탭(다국어) 지원: KO/EN 을 동시에 운용하기 위한 공통 유틸
    # ─────────────────────────────────────────────────────────────────────
    # ─────────────────────────────────────────────────────────────────────
    # 설명 탭(다국어) 유틸: KO/EN 공통
    # ─────────────────────────────────────────────────────────────────────

    def export_slice_as_derived_ct_16bit(self, use_roi_crop: bool = False, new_series: bool = True):
        import os
        from datetime import datetime
        import numpy as np
        import pydicom
        from pydicom.dataset import Dataset, FileDataset
        from pydicom.uid import generate_uid, ExplicitVRLittleEndian

        if self.stack is None:
            messagebox.showerror("Error", "No DICOM stack loaded."); return
        z  = int(getattr(self, "slice_index", 0))
        src = self.slices[z]

        raw = src.pixel_array
        H, W = raw.shape

        do_crop = bool(use_roi_crop and self.roi)
        if do_crop:
            x, y, w, h = [int(v) for v in self.roi]
            x = max(0, min(x, W-1)); y = max(0, min(y, H-1))
            xe = max(1, min(x+w, W)); ye = max(1, min(y+h, H))
            if xe <= x or ye <= y:
                messagebox.showerror("ROI error", "Empty ROI."); return
            out = raw[y:ye, x:xe].copy()
            out_H, out_W = out.shape
        else:
            out = raw.copy()
            out_H, out_W = H, W

        file_meta = Dataset()
        file_meta.MediaStorageSOPClassUID = pydicom.uid.CTImageStorage
        file_meta.MediaStorageSOPInstanceUID = generate_uid()
        file_meta.TransferSyntaxUID = ExplicitVRLittleEndian

        ts = datetime.now()
        out_dir = self._measure_root("DerivedCT16bit")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"CT16_slice{z:04d}_{'crop_' if do_crop else ''}{ts.strftime('%Y%m%d_%H%M%S')}.dcm")

        ds = FileDataset(out_path, {}, file_meta=file_meta, preamble=b"\0"*128)

        for tag in ("StudyInstanceUID","SeriesInstanceUID","SeriesNumber","StudyID","AccessionNumber",
                    "PatientID","PatientName","PatientBirthDate","PatientSex",
                    "StudyDate","StudyTime","ReferringPhysicianName","Manufacturer","InstitutionName",
                    "StationName","SoftwareVersions","BodyPartExamined","KVP","SliceThickness",
                    "SpacingBetweenSlices","PixelSpacing","ImageOrientationPatient","ImagePositionPatient",
                    "FrameOfReferenceUID","Laterality","PatientOrientation","ProtocolName","SeriesDescription"):
            if hasattr(src, tag):
                setattr(ds, tag, getattr(src, tag))

        if new_series:
            ds.SeriesInstanceUID = generate_uid()
            ds.SeriesDescription = (getattr(ds, "SeriesDescription", "") or "") + (" | Derived 16-bit (ROI crop)" if do_crop else " | Derived 16-bit (full)")

        ds.SOPClassUID     = pydicom.uid.CTImageStorage
        ds.SOPInstanceUID  = file_meta.MediaStorageSOPInstanceUID
        ds.Modality        = "CT"
        ds.ImageType       = ["DERIVED","SECONDARY"]
        ds.Rows, ds.Columns = int(out_H), int(out_W)
        ds.SamplesPerPixel = 1
        ds.PhotometricInterpretation = "MONOCHROME2"

        ds.BitsAllocated       = int(getattr(src, "BitsAllocated", 16))
        ds.BitsStored          = int(getattr(src, "BitsStored", 16))
        ds.HighBit             = int(getattr(src, "HighBit", ds.BitsStored-1))
        ds.PixelRepresentation = int(getattr(src, "PixelRepresentation", 1))  # 0 unsigned, 1 signed

        if hasattr(src, "RescaleIntercept"): ds.RescaleIntercept = float(src.RescaleIntercept)
        if hasattr(src, "RescaleSlope"):     ds.RescaleSlope     = float(src.RescaleSlope)

        try:
            wl = float(getattr(self, "wl", None)) if getattr(self, "wl", None) is not None else float(getattr(src, "WindowCenter", 40))
            ww = float(getattr(self, "ww", None)) if getattr(self, "ww", None) is not None else float(getattr(src, "WindowWidth", 400))
            ds.WindowCenter = wl; ds.WindowWidth = ww
        except Exception:
            pass

        if do_crop and hasattr(src, "ImageOrientationPatient") and hasattr(src, "PixelSpacing") and hasattr(src, "ImagePositionPatient"):
            try:
                import numpy as np
                iop = [float(v) for v in src.ImageOrientationPatient]
                row_dir = np.array(iop[:3], dtype=np.float64)
                col_dir = np.array(iop[3:], dtype=np.float64)
                sy, sx = [float(v) for v in src.PixelSpacing]
                ipp0 = np.array([float(v) for v in src.ImagePositionPatient], dtype=np.float64)
                delta = row_dir * (y * sy) + col_dir * (x * sx)
                new_ipp = ipp0 + delta
                ds.ImagePositionPatient = [float(new_ipp[0]), float(new_ipp[1]), float(new_ipp[2])]
            except Exception:
                pass

        ds.PixelData = out.tobytes()
        ds.is_little_endian = True
        ds.is_implicit_VR   = False

        ds.save_as(out_path)
        try: self._log(f"Saved 16-bit Derived CT → {out_path}")
        except Exception: pass
        messagebox.showinfo("Derived CT (16-bit)", f"Saved:\n{out_path}")


    def _get_info_ctx(self, lang: str):
        if not hasattr(self, "_info_ctx"):
            self._info_ctx = {}
        if lang not in self._info_ctx:
            self._info_ctx[lang] = {"find_var": None, "entry": None, "txt": None, "list": None, "anchors": []}
        return self._info_ctx[lang]

    def _init_info_tab(self, lang: str, parent, *, md_filename: str | None = None, default_text: str | None = None):
        import re
        from tkinter import ttk
        from tkinter.scrolledtext import ScrolledText

        ctx = self._get_info_ctx(lang)

        # 상단: 검색 + 새로고침
        top = tk.Frame(parent); top.pack(fill=tk.X, padx=4, pady=4)
        tk.Label(top, text=("검색" if lang == "ko" else "Search")).pack(side=tk.LEFT)

        ctx["find_var"] = tk.StringVar()
        e = tk.Entry(top, textvariable=ctx["find_var"], width=28); e.pack(side=tk.LEFT, padx=(4,6))
        ctx["entry"] = e

        tk.Button(top, text=("찾기" if lang == "ko" else "Find"),
                command=lambda: self._info_search(lang, "next")).pack(side=tk.LEFT)
        tk.Button(top, text=("이전" if lang == "ko" else "Prev"),
                command=lambda: self._info_search(lang, "prev")).pack(side=tk.LEFT, padx=(2,0))
        tk.Button(top, text=("지우기" if lang == "ko" else "Clear"),
                command=lambda: self._info_clear_search(lang)).pack(side=tk.LEFT, padx=(6,0))
        tk.Button(top, text=("새로고침" if lang == "ko" else "Reload"),
                command=lambda: self._reload_info_tab(lang)).pack(side=tk.LEFT, padx=(8,0))

        # 본문: 좌(헤더 내비) · 우(텍스트)
        body = tk.Frame(parent); body.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0,4))

        navf = tk.Frame(body); navf.pack(side=tk.LEFT, fill=tk.Y)
        tk.Label(navf, text=("헤더 목록" if lang == "ko" else "Headers")).pack(anchor="w")
        lb = tk.Listbox(navf, width=28, height=18); lb.pack(fill=tk.Y, expand=False, side=tk.LEFT)
        sb_nav = ttk.Scrollbar(navf, orient="vertical", command=lb.yview); sb_nav.pack(side=tk.LEFT, fill=tk.Y)
        lb.configure(yscrollcommand=sb_nav.set)
        lb.bind("<<ListboxSelect>>", lambda ev, L=lang: self._on_info_header_select(L, ev))
        ctx["list"] = lb

        viewf = tk.Frame(body); viewf.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8,0))
        txt = ScrolledText(viewf, wrap="word"); txt.pack(fill=tk.BOTH, expand=True)
        txt.tag_configure("hit", background="yellow")
        txt.tag_configure("h1", font=("TkDefaultFont", 11, "bold"))
        txt.tag_configure("h2", font=("TkDefaultFont", 10, "bold"))
        txt.tag_configure("h3", font=("TkDefaultFont", 9, "bold"))
        ctx["txt"] = txt
        txt.bind("<Control-f>", lambda e, L=lang: (self._focus_info_search(L), "break"))

        # 단축키(F1/F2)는 최초 1회만 바인딩
        if not hasattr(self, "_info_hotkeys_set"):
            try:
                self.root.bind("<F1>", lambda e: self.show_info_tab("ko"))
                self.root.bind("<F2>", lambda e: self.show_info_tab("en"))
            finally:
                self._info_hotkeys_set = True

        # ▼ 여기서 헬퍼로 텍스트 읽어와서 세팅
        text = self._read_info_text(lang, md_filename=md_filename, default_text=default_text)
        self._set_info_text(lang, text)


    def _set_info_text(self, lang: str, text: str):
        import re
        ctx = self._get_info_ctx(lang)
        txt, lb = ctx["txt"], ctx["list"]
        txt.config(state="normal"); txt.delete("1.0", tk.END); txt.insert(tk.END, text)
        ctx["anchors"] = []; lb.delete(0, tk.END)
        for i, line in enumerate(text.splitlines(), start=1):
            m = re.match(r'^(#{1,6})\s+(.*)$', line)
            if m:
                level = len(m.group(1)); title = m.group(2).strip()
                disp = ("  " * (level - 1)) + title; idx = f"{i}.0"
                txt.tag_add(f"h{min(level,3)}", f"{i}.0", f"{i}.end")
                lb.insert(tk.END, disp); ctx["anchors"].append((disp, idx, level))
        txt.config(state="disabled")

    def _on_info_header_select(self, lang: str, event=None):
        ctx = self._get_info_ctx(lang); anchors = ctx.get("anchors") or []
        lb, txt = ctx["list"], ctx["txt"]
        sel = lb.curselection()
        if not sel: return
        _, idx, _ = anchors[sel[0]]
        txt.config(state="normal"); txt.see(idx); txt.mark_set("insert", idx); txt.config(state="disabled")

    def _info_clear_search(self, lang: str):
        txt = self._get_info_ctx(lang)["txt"]
        txt.config(state="normal"); txt.tag_remove("hit", "1.0", tk.END); txt.config(state="disabled")

    def _info_search(self, lang: str, direction="next"):
        ctx = self._get_info_ctx(lang); txt = ctx["txt"]; term = (ctx["find_var"].get() or "").strip()
        if not term: return
        txt.config(state="normal"); start = txt.index("insert")
        if direction == "prev":
            pos = txt.search(term, start, stopindex="1.0", backwards=True, nocase=True)
        else:
            pos = txt.search(term, start, stopindex=tk.END, forwards=True, nocase=True)
        if not pos:
            pos = txt.search(term, tk.END if direction=="prev" else "1.0",
                            stopindex="1.0" if direction=="prev" else tk.END,
                            backwards=(direction=="prev"), nocase=True)
        if pos:
            end = f"{pos}+{len(term)}c"
            txt.tag_remove("hit", "1.0", tk.END)
            txt.tag_add("hit", pos, end)
            txt.see(pos); txt.mark_set("insert", end)
        txt.config(state="disabled")

    def _current_info_lang(self) -> str | None:
        try:
            sel = self.nb_right.select()
            for L, tab in (getattr(self, "_info_tabs", {}) or {}).items():
                if str(tab) == str(sel):
                    return L
        except Exception:
            pass
        return None

    def _focus_info_search(self, lang: str | None = None):
        if lang is None:
            lang = self._current_info_lang()
        if not lang: return
        entry = self._get_info_ctx(lang).get("entry")
        if entry:
            try:
                entry.focus_set(); entry.icursor("end")
            except Exception:
                pass

    def show_info_tab(self, lang: str = "ko", event=None):
        try:
            if hasattr(self, "_info_tabs") and lang in self._info_tabs:
                self.nb_right.tab(self._info_tabs[lang], state="normal")
                self.nb_right.select(self._info_tabs[lang])
                self._focus_info_search(lang)
        except Exception:
            pass

    def _reload_info_tab(self, lang: str):
        # 현재 탭의 텍스트만 다시 읽어 적용
        text = self._read_info_text(lang)
        self._set_info_text(lang, text)
        # 현재 탭 선택 유지 및 검색창 포커스
        self.show_info_tab(lang)


    def _fullres_slice_overlay(self, z, points_on_slice=None, roi_rect=None, roi_label=None, wl=None, ww=None):
        raw = self.slices[z].pixel_array
                # 슬라이스별 HU 변환 사용
        ds = self.slices[z]
        slope_used = float(getattr(ds, "RescaleSlope",  getattr(self, "slope", 1.0)))
        inter_used = float(getattr(ds, "RescaleIntercept", getattr(self, "inter", 0.0)))

        hu  = raw.astype("float32") * slope_used + inter_used

        if wl is None: wl = getattr(self, "wl", None)
        if ww is None: ww = getattr(self, "ww", None)
        img8, wl, ww = self._hu_to_uint8(hu, wl=wl, ww=ww)


        # ✅ 복구: 원해상도 8bit 이미지를 RGB로 만들고 draw 컨텍스트 생성
        img = Image.fromarray(img8, mode="L").convert("RGB")
        draw = ImageDraw.Draw(img)

        # 자동 ROI 색 선택(기존 로직 유지)
        roi_col = self._choose_roi_color(Image.fromarray(img8, mode="L"))

        # ROI 박스 + 라벨
        if roi_rect:
            xs, ys, ws, hs = roi_rect
            draw.rectangle([xs, ys, xs+ws, ys+hs], outline=roi_col, width=2)
            if roi_label:
                self._draw_text_with_outline(draw, (xs+5, ys+5), roi_label, fill="yellow")

        # 포인트 마커/라벨
        if points_on_slice:
            for p in points_on_slice:
                px, py = int(p["x"]), int(p["y"])
                draw.ellipse([px-4, py-4, px+4, py+4], fill=(255,255,0))
                self._draw_text_with_outline(
                    draw, (px+8, py-8),
                    f"{p['idx']} HU={p['hu']:.2f}  G={p.get('gray','')}",
                    fill="yellow"
                )

        return img


    def export_points_report(self):
        """
        현재 self.points 목록을 기반으로 CSV(헤더 포함) + 이미지 일괄 저장
        - CSV는 타임스탬프 파일명으로 저장하여 항상 헤더를 포함
        """
        if not self.points:
            messagebox.showwarning("No points", "No point HU measurements yet."); return

        from datetime import datetime
        import numpy as np
        base_dir = os.path.join(self._measure_root("Point"))
        os.makedirs(base_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")

        # 1) CSV (항상 새 파일명으로 저장 → 헤더 포함 보장)
        csv_path = os.path.join(base_dir, f"points_{ts}.csv")
        base_cols, meta_cols = self._csv_header_titles_points()
        with open(csv_path, "w", encoding="utf-8") as f:
            # 헤더
            header = [title for _, title in base_cols] + [title for _, title in meta_cols]
            f.write(",".join(header) + "\n")
            # 행
            for p in self.points:
                ds = self.slices[p["slice"]]
                meta = self._slice_meta(ds)
                # 해당 슬라이스의 메타로 slope/intercept 기록
                slope_used, inter_used = self._rescale_params(ds)      # 또는 ds_slice

                                # 최신 디스플레이 값으로 disp 재계산 (현 WL/WW, 밝기/대비/감마 반영)
                try:
                    _ = self._render_slice_img(p["slice"], overlay=False)  # _last_preview_img 갱신
                except Exception:
                    pass
                W, H = int(self.stack.shape[2]), int(self.stack.shape[1])
                px = int(round(p["x"] * 512.0 / max(W, 1)))
                py = int(round(p["y"] * 512.0 / max(H, 1)))
                disp_now = self._display_gray_from_preview(px, py)

                row_base = [
                    str(p["idx"]), str(p["slice"]), str(p["x"]), str(p["y"]),
                    str(p.get("gray","")),
                    f"{p['hu']:.6f}",
                    (str(disp_now) if disp_now != "" else ""),
                    str(p.get("slope", slope_used)), str(p.get("intercept", inter_used)),
                    datetime.now().isoformat()
                ]

                row_meta = [str(meta[key]) for key, _ in meta_cols]
                f.write(",".join(row_base + row_meta) + "\n")

        # 2) 슬라이스별 원해상도 오버레이 PNG
        from collections import defaultdict
        by_slice = defaultdict(list)
        for p in self.points:
            by_slice[p["slice"]].append(p)
        for z, pts in by_slice.items():
            img = self._fullres_slice_overlay(z, points_on_slice=pts)
            out_path = os.path.join(base_dir, f"slice_{z:04}_HU_points_{ts}.png")
            img.save(out_path)
            self._log(f"Saved point overlay → {out_path}")

        # 3) 포인트별 원해상도 크롭 PNG (8/16bit)
        patch = 128
        for p in self.points:
            z = p["slice"]; x = int(p["x"]); y = int(p["y"])
            raw = self.slices[z].pixel_array
            H, W = raw.shape

            # 경계 안전 크롭 (최소 1픽셀 보장)
            half = max(1, patch // 2)
            x0 = max(0, min(W-1, x - half))
            y0 = max(0, min(H-1, y - half))
            x1 = min(W, max(x0 + 1, x0 + patch))
            y1 = min(H, max(y0 + 1, y0 + patch))
            if x1 <= x0 or y1 <= y0:
                self._log(f"[SKIP] empty crop at slice {z}, point {p['idx']}"); 
                continue

            crop_raw = raw[y0:y1, x0:x1].copy()
            if crop_raw.size == 0:
                self._log(f"[SKIP] zero-size crop at slice {z}, point {p['idx']}"); 
                continue

            # 슬라이스별 HU 변환
            ds_slice = self.slices[z]
            slope_used, inter_used = self._rescale_params(ds_slice)
            crop_hu = crop_raw.astype("float32") * slope_used + inter_used

            # 8bit 변환
            crop8, _, _ = self._hu_to_uint8(
                crop_hu,
                wl=getattr(self, "wl", None),
                ww=getattr(self, "ww", None)
            )

            # 빈 배열 방어 (드물지만)
            if crop8.size == 0 or crop8.shape[0] == 0 or crop8.shape[1] == 0:
                self._log(f"[SKIP] empty crop8 at slice {z}, point {p['idx']}"); 
                continue

            Image.fromarray(crop8, mode="L").save(os.path.join(base_dir, f"slice_{z:04}_pt_{p['idx']:04}_HU_crop8_{ts}.png"))
            Image.fromarray(crop_raw.astype(np.uint16)).save(os.path.join(base_dir, f"slice_{z:04}_pt_{p['idx']:04}_HU_crop16_{ts}.png"))

        messagebox.showinfo("Done", f"Saved point HU report:\n{csv_path}")




    def export_roi_report(self):
        """
        현재 ROI의 HU 통계를 계산(또는 마지막 결과 사용)하여 CSV(헤더 포함) + 이미지 저장
        """

        # 최근 계산값이 없으면 즉시 계산(저장 없음)
        # 항상 최신 디스플레이/WL/WW/ROI를 반영하기 위해 즉시 재계산
        self.measure_roi_hu()
        if not self.last_roi_stats:
            messagebox.showwarning("No ROI", "No ROI HU measurement yet."); return


        m = self.last_roi_stats
        base_dir = os.path.join(self._measure_root("ROI"))
        os.makedirs(base_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")

        # 1) CSV (항상 새 파일명 → 헤더 포함)
        csv_path = os.path.join(base_dir, f"roi_stats_{ts}.csv")
        base_cols, meta_cols = self._csv_header_titles_roi()
        ds = self.slices[int(m["slice"])]
        meta = self._slice_meta(ds)

        # mm 변환
        px  = getattr(ds, "PixelSpacing", None)
        row_sp = float(px[0]) if (px is not None and len(px) > 0) else None  # Y(행) 간격
        col_sp = float(px[1]) if (px is not None and len(px) > 1) else None  # X(열) 간격
        ws_mm = (m["ws"] * col_sp) if col_sp else ""
        hs_mm = (m["hs"] * row_sp) if row_sp else ""


        with open(csv_path, "w", encoding="utf-8") as f:
            header = [title for _, title in base_cols] + [title for _, title in meta_cols]
            f.write(",".join(header) + "\n")
            # slope/intercept는 해당 슬라이스의 실제 값을 사용
            slope_used = float(getattr(ds, "RescaleSlope",  getattr(self, "slope", 1.0)))
            inter_used = float(getattr(ds, "RescaleIntercept", getattr(self, "inter", 0.0)))
            slope_used = float(m.get("slope", slope_used))
            inter_used = float(m.get("intercept", inter_used))
            row_vals = [
                str(m["slice"]), str(m["xs"]), str(m["ys"]), str(m["ws"]), str(m["hs"]),
                (f"{ws_mm:.6f}" if ws_mm != "" else ""), (f"{hs_mm:.6f}" if hs_mm != "" else ""),
                f"{m['mean']:.6f}", f"{m['std']:.6f}", f"{m['min']:.6f}", f"{m['max']:.6f}",
                f"{m.get('gray_mean',''):.6f}", f"{m.get('gray_std',''):.6f}",
                f"{m.get('gray_min',''):.6f}",  f"{m.get('gray_max',''):.6f}",
                (f"{m.get('disp_mean',''):.6f}" if isinstance(m.get('disp_mean',''), float) else ""),
                (f"{m.get('disp_std',''):.6f}"  if isinstance(m.get('disp_std',''),  float) else ""),
                (str(m.get('disp_min','')) if m.get('disp_min','') != "" else ""),
                (str(m.get('disp_max','')) if m.get('disp_max','') != "" else ""),
                f"{slope_used}", f"{inter_used}",
                datetime.now().isoformat()
            ]
            row_meta = [str(meta[key]) for key, _ in meta_cols]
            f.write(",".join(row_vals + row_meta) + "\n")

        # 2) 프리뷰(512) 오버레이 PNG
        img = self._render_slice_img(m["slice"], overlay=True)
        img.save(os.path.join(base_dir, f"slice_{m['slice']:04}_HU_roi_{ts}.png"))

        # 3) 원해상도 ROI 크롭(8/16bit)
        raw = self.slices[m["slice"]].pixel_array
        xs, ys, xe, ye = m["xs"], m["ys"], m["xs"]+m["ws"], m["ys"]+m["hs"]
        crop_raw = raw[ys:ye, xs:xe].copy()
        # 슬라이스별 RescaleSlope/Intercept로 HU 변환
        ds_slice = self.slices[int(m["slice"])]
        slope_used, inter_used = self._rescale_params(ds)
        crop_hu = crop_raw.astype("float32") * slope_used + inter_used

        # 현재 WL/WW로 8bit 변환(프리뷰/오버레이와 표시 일관성)
        crop8, _, _ = self._hu_to_uint8(
            crop_hu,
            wl=getattr(self, "wl", None),
            ww=getattr(self, "ww", None)
        )

        Image.fromarray(crop8, mode="L").save(os.path.join(base_dir, f"slice_{m['slice']:04}_HU_roi_crop8_{ts}.png"))
        Image.fromarray(crop_raw.astype(np.uint16)).save(os.path.join(base_dir, f"slice_{m['slice']:04}_HU_roi_crop16_{ts}.png"))

        # 4) 원해상도 전체 오버레이(ROI 박스+라벨)
        label = f"ROI HU: {m['mean']:.2f}±{m['std']:.2f} (min {m['min']:.0f}, max {m['max']:.0f})"
        if all(k in m for k in ('gray_mean','gray_std','gray_min','gray_max')):
            label += f" | Gray: {m['gray_mean']:.1f}±{m['gray_std']:.1f} (min {m['gray_min']:.0f}, max {m['gray_max']:.0f})"
            full = self._fullres_slice_overlay(
                m["slice"],
                points_on_slice=None,
                roi_rect=(m["xs"], m["ys"], m["ws"], m["hs"]),
                roi_label=label,
                wl=getattr(self, "wl", None),
                ww=getattr(self, "ww", None),
            )
        else:
            full = self._fullres_slice_overlay(
                m["slice"],
                points_on_slice=None,
                roi_rect=(m["xs"], m["ys"], m["ws"], m["hs"]),
                roi_label=label,
                wl=getattr(self, "wl", None),
                ww=getattr(self, "ww", None),
            )
    
        full.save(os.path.join(base_dir, f"slice_{m['slice']:04}_HU_roi_fullres_{ts}.png"))

        messagebox.showinfo("Done", f"Saved ROI HU report:\n{csv_path}")



    # ---------------- UI ----------------

# Safe binder: prefer instance method; fallback to module-level function with same name

    def _bind_or_fallback(self, name):
        fn = getattr(self, name, None)
        if callable(fn):
            return fn
        g = globals().get(name, None)
        if callable(g):
            return lambda *a, **k: g(self, *a, **k)
        from tkinter import messagebox
        return lambda *a, **k: messagebox.showerror("Missing handler", f"Handler '{name}' is not available.")
    def tree(self, *args, **kwargs):
        """Auto-injected wrapper for missing handler.
        Tries global function `tree(self, ...)`, else shows an error dialog.
        """
        fn = globals().get("tree")
        if callable(fn):
            return fn(self, *args, **kwargs)
        try:
            from tkinter import messagebox
            messagebox.showerror("Missing handler", "Handler 'tree' is not available.")
        except Exception:
            print("[UI] Missing handler: tree")
        return None




    def _build_ui(self):
            # ───────────────────────── Top Bar 1: 파일/ROI/저장/표시 ─────────────────────────
            bar1 = tk.Frame(self.root); bar1.pack(fill=tk.X, padx=8, pady=(6,3))
            tk.Button(bar1, text="Load DICOM Folder", command=self._bind_or_fallback("load_dicom")).pack(side=tk.LEFT, padx=3)
            tk.Button(bar1, text="Select ROI",        command=self._bind_or_fallback("prepare_roi")).pack(side=tk.LEFT, padx=3)

            # 측정/저장 (HU 관련)
            tk.Checkbutton(bar1, text="Point HU", variable=self.point_mode, indicatoron=False).pack(side=tk.LEFT, padx=6)
            tk.Button(bar1, text="ROI HU", command=self.measure_roi_hu).pack(side=tk.LEFT, padx=2)
            tk.Button(bar1, text="Save HU (Points)", command=self.export_points_report).pack(side=tk.LEFT, padx=6)
            tk.Button(bar1, text="Save HU (ROI)",    command=self.export_roi_report).pack(side=tk.LEFT, padx=2)

            tk.Checkbutton(bar1, text="Show values on image", variable=self.overlay_show_values).pack(side=tk.LEFT, padx=10)

            # 단일 ROI 저장 버튼
            self.btn_save_single = tk.Button(bar1, text="Save ROI (Single)", command=self.save_single, state="disabled")
            self.btn_save_single.pack(side=tk.RIGHT, padx=3)

            ttk.Separator(self.root, orient="horizontal").pack(fill=tk.X, padx=8)

                        # ── Polygon ROI 도구 ───────────────────────────────────────────
            tk.Checkbutton(bar1, text="Polygon ROI", variable=self.poly_mode,
                           indicatoron=False, command=self._toggle_poly_mode).pack(side=tk.LEFT, padx=8)
            tk.Button(bar1, text="Close Poly", command=self._poly_close).pack(side=tk.LEFT, padx=2)
            tk.Button(bar1, text="Undo",       command=self._poly_undo).pack(side=tk.LEFT, padx=2)
            tk.Button(bar1, text="Clear",      command=self._poly_clear).pack(side=tk.LEFT, padx=2)

            # ───────────────────────── Top Bar 2: 출력 포맷/경로/색상 ────────────────────────
            bar2 = tk.Frame(self.root); bar2.pack(fill=tk.X, padx=8, pady=(3,3))

            # 출력 형식 (DICOM/NRRD)
            tk.Label(bar2, text="Output").pack(side=tk.LEFT, padx=(2,2))
            ttk.Combobox(bar2, textvariable=self.out_kind, values=["DICOM","NRRD"], width=7, state="readonly").pack(side=tk.LEFT)
            tk.Checkbutton(bar2, text="JPEG-LS lossless (DICOM)", variable=self.ts_jpegls).pack(side=tk.LEFT, padx=6)

            # NRRD 모드
            tk.Label(bar2, text="NRRD mode").pack(side=tk.LEFT, padx=(12,2))
            ttk.Combobox(bar2, textvariable=self.nrrd_mode, values=["nrrd","nhdr+raw.gz"], width=12, state="readonly").pack(side=tk.LEFT)

            # 경로/색상
            tk.Checkbutton(bar2, text="ASCII-only output path", variable=self.force_ascii_path).pack(side=tk.LEFT, padx=12)
            tk.Label(bar2, text="ROI color").pack(side=tk.LEFT, padx=(12,2))
            tk.OptionMenu(bar2, self.roi_color, "yellow", "red").pack(side=tk.LEFT)
            tk.Label(bar2, text="Point color").pack(side=tk.LEFT, padx=(8,2))
            tk.OptionMenu(bar2, self.point_color, "yellow", "red").pack(side=tk.LEFT)

            ttk.Separator(self.root, orient="horizontal").pack(fill=tk.X, padx=8)
                        # ROI/Point color 바로 뒤에 추가
            tk.Checkbutton(bar2, text="Auto-contrast ROI color",
                        variable=self.roi_color_auto).pack(side=tk.LEFT, padx=10)


            # ───────────────────────── ROI 도구 행 ─────────────────────────
            bar_roi = tk.LabelFrame(self.root, text="ROI Tools")
            bar_roi.pack(fill=tk.X, padx=8, pady=(4,4))

            # (A) 드로잉 제어
            tk.Checkbutton(bar_roi, text="Square", variable=self.lock_square).pack(side=tk.LEFT, padx=6)
            tk.Checkbutton(bar_roi, text="Center-drag", variable=self.center_draw).pack(side=tk.LEFT, padx=6)

            # (B) 고정 크기
            tk.Checkbutton(bar_roi, text="Fixed size", variable=self.fixed_size_mode).pack(side=tk.LEFT, padx=(12,4))
            tk.Label(bar_roi, text="W").pack(side=tk.LEFT); e_fw = tk.Entry(bar_roi, textvariable=self.fixed_w, width=6, justify="right"); e_fw.pack(side=tk.LEFT, padx=(2,6))
            tk.Label(bar_roi, text="H").pack(side=tk.LEFT); e_fh = tk.Entry(bar_roi, textvariable=self.fixed_h, width=6, justify="right"); e_fh.pack(side=tk.LEFT, padx=(2,6))
            ttk.Combobox(bar_roi, textvariable=self.fixed_unit, values=["mm","px"], width=4, state="readonly").pack(side=tk.LEFT)

            # (C) 숫자 직접 입력 (512 프리뷰 좌표 기준)
            tk.Label(bar_roi, text="X").pack(side=tk.LEFT, padx=(12,2)); tk.Entry(bar_roi, textvariable=self.roi_x, width=6, justify="right").pack(side=tk.LEFT)
            tk.Label(bar_roi, text="Y").pack(side=tk.LEFT, padx=(6,2));  tk.Entry(bar_roi, textvariable=self.roi_y, width=6, justify="right").pack(side=tk.LEFT)
            tk.Label(bar_roi, text="W").pack(side=tk.LEFT, padx=(6,2));  tk.Entry(bar_roi, textvariable=self.roi_w, width=6, justify="right").pack(side=tk.LEFT)
            tk.Label(bar_roi, text="H").pack(side=tk.LEFT, padx=(6,2));  tk.Entry(bar_roi, textvariable=self.roi_h, width=6, justify="right").pack(side=tk.LEFT)

            tk.Button(bar_roi, text="Apply numeric ROI", command=self._apply_numeric_roi).pack(side=tk.LEFT, padx=(8,4))
            tk.Button(bar_roi, text="From mm (sync)",    command=self._apply_mm_roi).pack(side=tk.LEFT, padx=2)


            # ───────────────────────── Top Bar 3: 스페이싱/HU 파라미터/자동저장 ────────────────
            bar3 = tk.Frame(self.root); bar3.pack(fill=tk.X, padx=10, pady=(6,4))  # ← 바 전체 여백 확대

            tk.Label(bar3, text="Spacing X/Y/Th").pack(side=tk.LEFT, padx=(6,8))   # ← 라벨 좌우 간격 확대
            self.e_sx = tk.Entry(bar3, width=10, justify="right")
            self.e_sy = tk.Entry(bar3, width=10, justify="right")
            self.e_th = tk.Entry(bar3, width=10, justify="right")
            for e in (self.e_sx, self.e_sy, self.e_th):
                e.pack(side=tk.LEFT, padx=6, ipadx=6, ipady=2)                      # ← 필드 간격/내부패딩 확대

            tk.Label(bar3, text="HU Slope/Intercept").pack(side=tk.LEFT, padx=(18,8))
            self.e_k  = tk.Entry(bar3, width=10, justify="right")
            self.e_b  = tk.Entry(bar3, width=10, justify="right")
            for e in (self.e_k, self.e_b):
                e.pack(side=tk.LEFT, padx=6, ipadx=6, ipady=2)

            # 자동 저장 토글
            tk.Checkbutton(bar3, text="Auto-save Preview", variable=self.auto_save_preview).pack(side=tk.RIGHT, padx=10)
            tk.Checkbutton(bar3, text="Auto-save HU",      variable=self.auto_save_hu).pack(side=tk.RIGHT, padx=4)

            ttk.Separator(self.root, orient="horizontal").pack(fill=tk.X, padx=8)

            # ───────────────────────── Top Bar 4: 톤 컨트롤(밝기/대비/감마) ───────────────────
            bar4 = tk.Frame(self.root); bar4.pack(fill=tk.X, padx=8, pady=(3,6))

            tk.Label(bar4, text="Brightness").pack(side=tk.LEFT, padx=(2,2))
            tk.Scale(bar4, from_=0.10, to=3.00, resolution=0.05, orient=tk.HORIZONTAL, length=140,
                    command=lambda v: self._set_brightness(float(v))).pack(side=tk.LEFT)

            tk.Label(bar4, text="Contrast").pack(side=tk.LEFT, padx=(12,2))
            tk.Scale(bar4, from_=0.10, to=5.00, resolution=0.05, orient=tk.HORIZONTAL, length=140,
                    command=lambda v: self._set_contrast(float(v))).pack(side=tk.LEFT)

            tk.Label(bar4, text="Gamma").pack(side=tk.LEFT, padx=(12,2))
            tk.Scale(bar4, from_=0.20, to=5.00, resolution=0.05, orient=tk.HORIZONTAL, length=140,
                    command=lambda v: self._set_gamma(float(v))).pack(side=tk.LEFT)

            tk.Button(bar4, text="Auto WL/WW (slice)",
                    command=lambda: self._auto_wlww_from_slice(self.slice_index)).pack(side=tk.LEFT, padx=(12,2))

            tk.Button(bar4, text="Auto WL/WW (series)",
                    command=self._auto_wlww_from_series).pack(side=tk.LEFT, padx=(6,2))
            tk.Button(bar4, text="Find Body", command=self.find_subject_auto).pack(side=tk.LEFT, padx=(8,2))
            tk.Button(bar4, text="Jump Max HU", command=self.jump_to_max_hu).pack(side=tk.LEFT, padx=(2,2))
            tk.Button(bar4, text="Auto WL from ROI", command=self.auto_wlww_from_roi).pack(side=tk.LEFT, padx=(2,2))
            tk.Button(bar4, text="MIPs", command=self.show_mips_window).pack(side=tk.LEFT, padx=(2,2))



            # ───────────────────────── Z 컨트롤 행 ─────────────────────────────────────────────
            zf = tk.Frame(self.root); zf.pack(fill=tk.X, padx=8, pady=(0,4))
            tk.Label(zf, text="Z Start").pack(side=tk.LEFT); self.sb_z0 = tk.Spinbox(zf, from_=0, to=0, width=6); self.sb_z0.pack(side=tk.LEFT, padx=3)
            tk.Label(zf, text="Z End").pack(side=tk.LEFT);   self.sb_z1 = tk.Spinbox(zf, from_=0, to=0, width=6); self.sb_z1.pack(side=tk.LEFT, padx=3)
            tk.Button(zf, text="Set Start=Cur", command=lambda: self._set_z(sp="start")).pack(side=tk.LEFT, padx=6)
            tk.Button(zf, text="Set End=Cur",   command=lambda: self._set_z(sp="end")).pack(side=tk.LEFT, padx=3)
            tk.Checkbutton(zf, text="Invert Z order on save", variable=self.invert_z).pack(side=tk.LEFT, padx=12)

            # ───────────────────────── 미리보기/배치 영역 (2열) ─────────────────────────────────
            mid = tk.Frame(self.root); mid.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

            # 왼쪽: 슬라이스 스케일 + 프리뷰
            left = tk.Frame(mid); left.pack(side=tk.LEFT, fill=tk.Y)
            self.s_slice = tk.Scale(left, from_=0, to=0, orient=tk.HORIZONTAL, label="Preview Slice",
                                    command=lambda v: self._show(int(v)))
            self.s_slice.pack(fill=tk.X, padx=2, pady=(0,4))
            self.preview = tk.Label(left, width=512, height=512, bg="black")
            self.preview.pack(padx=2, pady=2)
            for ev in ("<Button-1>", "<B1-Motion>", "<ButtonRelease-1>"):
                self.preview.bind(ev, self._mouse)

            # ← 여기 ‘딱 아래’ 3줄 추가
            self.preview.bind("<Motion>",   self._mouse, add="+")   # 마우스 이동 시 hover-라인 미리보기
            self.preview.bind("<Double-1>", self._mouse, add="+")   # 더블클릭으로 폴리곤 닫기
            self.preview.bind("<Button-3>", self._mouse,  add="+")  # 우클릭으로 폴리곤 닫기


            # 오른쪽: Notebook(탭) — Batch/ROI + 설명(한글) + Description(EN)
            right = tk.Frame(mid); right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8,0))
            self.nb_right = ttk.Notebook(right)
            self.nb_right.pack(fill=tk.BOTH, expand=True)

            # ── [탭 1] Batch / ROI ───────────────────────────────────────────────
            tab_batch = tk.Frame(self.nb_right)
            self.nb_right.add(tab_batch, text="Batch / ROI")

            tk.Label(tab_batch, text="Batch ROI List").pack(anchor="w")
            cols = ("name","x","y","w","h","z0","z1")
            self.tree = ttk.Treeview(tab_batch, columns=cols, show="headings", height=12)
            for c in cols:
                self.tree.heading(c, text=c.upper())
                self.tree.column(c, width=80, anchor="center")
            self.tree.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)
            sb = ttk.Scrollbar(tab_batch, orient="vertical", command=self.tree.yview); sb.pack(side=tk.LEFT, fill=tk.Y)
            self.tree.configure(yscrollcommand=sb.set)

            ctl = tk.Frame(tab_batch); ctl.pack(side=tk.LEFT, fill=tk.Y, padx=6)
            tk.Button(ctl, text="Add ROI",           command=self.add_roi_to_list).pack(fill=tk.X, pady=2)
            tk.Button(ctl, text="Remove Selected",   command=self.remove_selected).pack(fill=tk.X, pady=2)
            tk.Button(ctl, text="Save All (Batch)",  command=self.save_batch).pack(fill=tk.X, pady=(10,2))

            # ── [탭 2] 설명(한글) ────────────────────────────────────────────────
            self.tab_info_ko = tk.Frame(self.nb_right)
            self.nb_right.add(self.tab_info_ko, text="설명(한글)")
            self._init_info_tab("ko", self.tab_info_ko, md_filename="README_설명.md")

            # ── [탭 3] Description (EN) ──────────────────────────────────────────
            self.tab_info_en = tk.Frame(self.nb_right)
            self.nb_right.add(self.tab_info_en, text="Description (EN)")
            self._init_info_tab("en", self.tab_info_en, md_filename="README_EN.md")

            # 탭 식별자 저장 및 키보드 전환 기능
            self._info_tabs = {"ko": self.tab_info_ko, "en": self.tab_info_en}

                    # ───────────────── Window / Level 바 ─────────────────
            bar_wl = tk.Frame(self.root); bar_wl.pack(fill=tk.X, padx=8, pady=(0,6))

            # ---- 여기부터 추가: Window/Level 슬라이더 ----
            tk.Label(bar4, text="Window").pack(side=tk.LEFT, padx=(12,2))
            tk.Scale(bar4, from_=1, to=8000, resolution=1, orient=tk.HORIZONTAL, length=180,
                    command=lambda v: self._set_ww(float(v))).pack(side=tk.LEFT)

            tk.Label(bar4, text="Level").pack(side=tk.LEFT, padx=(12,2))
            tk.Scale(bar4, from_=-3000, to=3000, resolution=1, orient=tk.HORIZONTAL, length=180,
                    command=lambda v: self._set_wl(float(v))).pack(side=tk.LEFT)
            # ---- 추가 끝 ----


            tk.Checkbutton(bar_wl, text="Link WL/W (±W/2)",
                        variable=self._link_ww_wl,
                        command=lambda: self._on_wl_or_ww_changed()).pack(side=tk.LEFT)
            tk.Entry(bar_wl, textvariable=self.win_width, width=7).pack(side=tk.LEFT, padx=(10,2))
            tk.Entry(bar_wl, textvariable=self.win_level, width=7).pack(side=tk.LEFT, padx=(4,2))
            try:
                self.nb_right.enable_traversal()
            except Exception:
                pass

            # ───────────────────────── 로그 ───────────────────────────────────────────────────
            self.log = ScrolledText(self.root, height=8)
            self.log.pack(fill=tk.BOTH, expand=False, padx=8, pady=6)

            # ───────────────────────── (기존) 추가 옵션: bar2에 이어붙는 컨트롤 ────────────────
            tk.Checkbutton(bar2, text="Resample ROI XY", variable=self.xy_resample).pack(side=tk.LEFT, padx=10)
            tk.Label(bar2, text="→ W").pack(side=tk.LEFT, padx=(4,2))
            tk.Spinbox(bar2, from_=128, to=2048, textvariable=self.xy_target, width=6).pack(side=tk.LEFT)
            ttk.Combobox(bar2, textvariable=self.xy_method, values=["linear","bspline","nearest"],
                        width=8, state="readonly").pack(side=tk.LEFT, padx=(6,0))

            # ISO 3D
            tk.Checkbutton(bar2, text="Isotropic 3D", variable=self.iso_on).pack(side=tk.LEFT, padx=10)
            tk.Label(bar2, text="Voxel(mm)").pack(side=tk.LEFT, padx=(2,2))
            tk.Spinbox(bar2, from_=0.05, to=5.00, increment=0.05, textvariable=self.iso_mm, width=6)\
                .pack(side=tk.LEFT)
            ttk.Combobox(bar2, textvariable=self.iso_method, values=["linear","bspline","nearest"],
                        width=8, state="readonly").pack(side=tk.LEFT, padx=(6,0))

            # [ADD] ───────────────────────────────────────────────────────────────────
            tk.Checkbutton(bar1, text="정합/계측 모드",
                        variable=self.mode_reg_measure,
                        command=self._on_toggle_reg_measure).pack(side=tk.LEFT, padx=6)

            # (선택) z-spacing 허용오차 입력창
            frm_eps = tk.Frame(self.root); frm_eps.pack(fill=tk.X, padx=8, pady=(0,6))
            tk.Label(frm_eps, text="z-spacing 허용오차(mm)").pack(side=tk.LEFT)
            tk.Entry(frm_eps, width=6, textvariable=self.z_eps_mm).pack(side=tk.LEFT, padx=4)


        # ------------- helpers -------------


    # [ADD] ───────────────────────────────────────────────────────────────────

    def _rescale_params(self, ds):
        # 1) 슬라이스 메타
        slope = getattr(ds, "RescaleSlope", None)
        inter = getattr(ds, "RescaleIntercept", None)

        # 2) 시리즈(클래스 보관) 레벨 폴백
        if slope is None:
            slope = getattr(self, "slope", None)
        if inter is None:
            inter = getattr(self, "inter", None)

        # 3) RealWorldValueMappingSequence 폴백(있으면 사용)
        try:
            rwm = getattr(ds, "RealWorldValueMappingSequence", None)
            if rwm and len(rwm) > 0:
                item = rwm[0]
                slope = float(getattr(item, "RealWorldValueSlope", slope if slope is not None else 1.0))
                inter = float(getattr(item, "RealWorldValueIntercept", inter if inter is not None else 0.0))
        except Exception:
            pass

        # 4) 최종 폴백
        if slope is None:
            slope = 1.0
        if inter is None:
            inter = -1024.0 if getattr(ds, "Modality", "") == "CT" else 0.0
        return float(slope), float(inter)




    def _on_right_drag(self, event):
        dx = event.x - self._drag0x
        dy = event.y - self._drag0y
        self._drag0x, self._drag0y = event.x, event.y
        self.win_width.set( max(1.0, self.win_width.get() + dx*4.0) )
        self.win_level.set( self.win_level.get() + dy*4.0 )
        self._on_wl_or_ww_changed()

    def _on_right_down(self, event):
        self._drag0x, self._drag0y = event.x, event.y

        # 바인딩
        self.canvas.bind("<ButtonPress-3>", self._on_right_down)
        self.canvas.bind("<B3-Motion>",      self._on_right_drag)




        # ── DICOM의 WindowCenter/WindowWidth를 초기값으로 셋업
    def _init_window_from_dicom(self, ds):
        try:
            wc = getattr(ds, "WindowCenter", None)
            ww = getattr(ds, "WindowWidth",  None)
            # (0028,1050/1051)은 멀티값일 수 있어서 첫 값을 사용
            if isinstance(wc, (list, tuple)): wc = wc[0]
            if isinstance(ww, (list, tuple)): ww = ww[0]
            if ww and float(ww) > 0:
                self.win_width.set(float(ww))
            if wc is not None:
                self.win_level.set(float(wc))
        except Exception:
            pass

    def _on_wl_or_ww_changed(self):
        # 옵션: WW 변경 시 WL을 ±W/2로 링크 (RadiAnt/Horos 옵션 참고)
        if self._link_ww_wl.get():
            try:
                self.win_level.set(-self.win_width.get()/2.0)
            except Exception:
                pass
        # 디바운스(빠른 드래깅에서 불필요한 과다 리프레시 방지)
        if self._wlw_debounce_id:
            try: self.root.after_cancel(self._wlw_debounce_id)
            except Exception: pass
        self._wlw_debounce_id = self.root.after(15, self._refresh_view)

    def _window_slice_to_8bit(self, arr2d):
        """
        입력: 2D numpy (원본 단위). self.slope/self.inter로 HU 환산 후 Window/Level 적용하여 8-bit로 변환.
        """
        import numpy as np
        # HU 변환: HU = raw*slope + inter
        slope_used  = float(getattr(self, "slope", 1.0))
        inter_used  = float(getattr(self, "inter", 0.0))
        hu = arr2d.astype("float32") * slope_used + inter_used

        ww = max(1.0, float(self.win_width.get()))
        wl = float(self.win_level.get())

        lo = wl - ww/2.0
        hi = wl + ww/2.0
        hu = np.clip((hu - lo) / (hi - lo), 0.0, 1.0)   # 0~1
        img8 = (hu * 255.0 + 0.5).astype("uint8")       # 8-bit
        return img8

    def _refresh_view(self):
        """
        현재 self.slice_index의 슬라이스를 Window/Level 반영하여 캔버스/라벨에 갱신.
        기존 미리보기 코드에서 8bit 변환 구간만 이 함수로 대체 호출하면 됩니다.
        """
        if not self.stack: return
        z = int(self.slice_index)
        z = max(0, min(z, len(self.stack)-1))
        arr = self.stack[z]  # 2D numpy
        img8 = self._window_slice_to_8bit(arr)

        # PIL 변환 및 Tk 이미지로 갱신 (기존 프리뷰 코드에 맞춰 바꿔주세요)
        from PIL import Image, ImageTk
        im = Image.fromarray(img8, mode="L")
        # 필요 시 리사이즈/오버레이 등 추가
        tkimg = ImageTk.PhotoImage(im)
        self._last_preview_img = tkimg
        self.canvas.create_image(0, 0, image=tkimg, anchor="nw")  # 예시

    
    def _native_rect_to_preview(self, xs:int, ys:int, ws:int, hs:int):
        """ 원해상도 사각형 → 512 프리뷰 좌표 사각형 """
        W = int(self.stack.shape[2]); H = int(self.stack.shape[1])
        fx = 512.0 / max(W, 1); fy = 512.0 / max(H, 1)
        x = int(xs * fx); y = int(ys * fy)
        w = max(1, int(round(ws * fx))); h = max(1, int(round(hs * fy)))
        
        return x, y, w, h
    def find_subject_auto(self, margin_mm: float = 10.0):
        """
        시리즈에서 몸체(hu>-800) 면적이 가장 큰 슬라이스를 찾아 이동하고,
        그 슬라이스의 몸체 bbox를 ROI로 자동 설정.
        """
        import numpy as np
        if self.stack is None: 
            return
        n = len(self.slices)
        if n == 0: 
            return

        best_z, best_area, best_bbox = 0, -1, (0, 0, 0, 0)

        for z in range(n):
            ds = self.slices[z]
            raw = ds.pixel_array.astype(np.float32)
            slope = float(getattr(ds, "RescaleSlope", 1.0))
            inter = float(getattr(ds, "RescaleIntercept", 0.0))
            hu = raw * slope + inter
            body = hu > -800  # 배경 공기 제외
            area = int(body.sum())
            if area > best_area:
                best_area = area
                ys, xs = np.where(body)
                if ys.size == 0:
                    continue
                y0, y1 = int(ys.min()), int(ys.max())
                x0, x1 = int(xs.min()), int(xs.max())
                best_z = z
                best_bbox = (x0, y0, x1 - x0 + 1, y1 - y0 + 1)

        # 못 찾으면 종료
        if best_area <= 0:
            try: self._log("[FindSubject] no body-like region detected")
            except: pass
            return

        # 슬라이스 이동
        self.slice_index = best_z
        try:
            self.s_slice.set(best_z)
        except Exception:
            pass

        # bbox에 mm 마진 추가
        x, y, w, h = best_bbox
        ds = self.slices[best_z]
        try:
            sy, sx = [float(v) for v in getattr(ds, "PixelSpacing", [1.0, 1.0])]
        except Exception:
            sy, sx = 1.0, 1.0
        pad_x = int(round(margin_mm / max(sx, 1e-6)))
        pad_y = int(round(margin_mm / max(sy, 1e-6)))
        W = int(self.stack.shape[2]); H = int(self.stack.shape[1])
        xs = max(0, x - pad_x); ys = max(0, y - pad_y)
        xe = min(W, x + w + pad_x); ye = min(H, y + h + pad_y)
        ws = max(1, xe - xs); hs = max(1, ye - ys)

        # ROI를 프리뷰 좌표로 설정
        rx, ry, rw, rh = self._native_rect_to_preview(xs, ys, ws, hs)
        self.roi = [rx, ry, rw, rh]

        try: self._log(f"[FindSubject] z={best_z}, roi(native)=({xs},{ys},{ws},{hs})")
        except: pass
        self._show(self.slice_index)

    def jump_to_max_hu(self, roi_size_mm: float = 100.0):
        """
        시리즈 전체에서 HU 최대 지점으로 점프하고 주변을 ROI로 설정.
        공기(hu<-900)는 제외하여 산란을 피함.
        """
        import numpy as np
        if self.stack is None:
            return

        best = None  # (hu, z, y, x)
        for z in range(len(self.slices)):
            ds = self.slices[z]
            raw = ds.pixel_array.astype(np.float32)
            slope = float(getattr(ds, "RescaleSlope", 1.0))
            inter = float(getattr(ds, "RescaleIntercept", 0.0))
            hu = raw * slope + inter
            hu[hu < -900] = -1e9  # 공기 제외
            idx = int(np.argmax(hu))
            val = float(hu.ravel()[idx])
            if best is None or val > best[0]:
                y, x = divmod(idx, hu.shape[1])
                best = (val, z, y, x)

        if best is None:
            return

        val, z, y, x = best
        self.slice_index = z
        try:
            self.s_slice.set(z)
        except Exception:
            pass

        # ROI 크기(mm)를 픽셀로 환산
        ds = self.slices[z]
        try:
            sy, sx = [float(v) for v in getattr(ds, "PixelSpacing", [1.0, 1.0])]
        except Exception:
            sy, sx = 1.0, 1.0
        half_w = int(round(roi_size_mm / max(sx, 1e-6) / 2.0))
        half_h = int(round(roi_size_mm / max(sy, 1e-6) / 2.0))

        W = int(self.stack.shape[2]); H = int(self.stack.shape[1])
        xs = max(0, x - half_w); ys = max(0, y - half_h)
        xe = min(W, x + half_w); ye = min(H, y + half_h)
        ws = max(1, xe - xs); hs = max(1, ye - ys)

        # ROI 설정(프리뷰)
        rx, ry, rw, rh = self._native_rect_to_preview(xs, ys, ws, hs)
        self.roi = [rx, ry, rw, rh]

        try: self._log(f"[JumpMaxHU] z={z}, maxHU={val:.1f}, roi(native)=({xs},{ys},{ws},{hs})")
        except: pass
        self._show(self.slice_index)

    def auto_wlww_from_roi(self, low_p=5.0, high_p=95.0, fallback_to_slice=True):
        """
        현재 ROI(프리뷰 좌표)를 네이티브로 변환 후, ROI 픽셀의 퍼센타일을 기준으로 WL/WW 자동 조정.
        """
        import numpy as np
        if not self.roi or self.stack is None:
            if fallback_to_slice:
                return self._auto_wlww_from_slice(self.slice_index, low_p, high_p)
            return

        xs, ys, ws, hs = self._roi_xywh_native()
        ds = self.slices[self.slice_index]
        raw = ds.pixel_array.astype(np.float32)
        slope = float(getattr(ds, "RescaleSlope", 1.0))
        inter = float(getattr(ds, "RescaleIntercept", 0.0))

        H, W = raw.shape
        xs = max(0, min(xs, W - 1)); ys = max(0, min(ys, H - 1))
        xe = max(xs + 1, min(xs + ws, W)); ye = max(ys + 1, min(ys + hs, H))
        roi = raw[ys:ye, xs:xe]
        if roi.size == 0:
            return

        hu = roi * slope + inter
        lo = float(np.percentile(hu, low_p))
        hi = float(np.percentile(hu, high_p))
        self.wl = (lo + hi) * 0.5
        self.ww = max(50.0, hi - lo)
        try: self._log(f"[AutoWL ROI] WL={self.wl:.1f}, WW={self.ww:.1f}")
        except: pass
        self._show(self.slice_index)

    def show_mips_window(self):
        """
        Axial/Coronal/Sagittal MIP를 간단 팝업으로 띄워 탐색 보조.
        """
        import numpy as np, tkinter as tk
        from PIL import Image, ImageTk

        if self.stack is None:
            return

        # HU 스택 만들기
        zs = len(self.slices)
        hu_list = []
        for z in range(zs):
            ds = self.slices[z]
            raw = ds.pixel_array.astype(np.float32)
            slope = float(getattr(ds, "RescaleSlope", 1.0))
            inter = float(getattr(ds, "RescaleIntercept", 0.0))
            hu_list.append(raw * slope + inter)
        vol = np.stack(hu_list, axis=0)  # (Z,Y,X)

        # MIPs
        ax = vol.max(axis=0)             # (Y,X)
        co = vol.max(axis=1)             # (Z,X)
        sa = vol.max(axis=2)             # (Z,Y)

        def to_img(a):
            img8, _, _ = self._hu_to_uint8(a, wl=getattr(self, "wl", None), ww=getattr(self, "ww", None))
            return Image.fromarray(img8, mode="L").resize((512, 512), Image.LANCZOS)

        win = tk.Toplevel(self.root)
        win.title("MIP (Axial / Coronal / Sagittal)")
        canv = tk.Canvas(win, width=512*3+20, height=512+10, bg="black")
        canv.pack()

        ia = to_img(ax); ic = to_img(co); isg = to_img(sa)
        ta = ImageTk.PhotoImage(ia); tc = ImageTk.PhotoImage(ic); ts = ImageTk.PhotoImage(isg)
        canv.create_image(10, 10, anchor="nw", image=ta)
        canv.create_image(10+512+5, 10, anchor="nw", image=tc)
        canv.create_image(10+512*2+10, 10, anchor="nw", image=ts)

        # 버튼: 몸체 찾기 / 최댓값 점프
        btns = tk.Frame(win); btns.pack(fill=tk.X)
        tk.Button(btns, text="Find Body", command=self.find_subject_auto).pack(side=tk.LEFT, padx=4, pady=4)
        tk.Button(btns, text="Jump Max HU", command=self.jump_to_max_hu).pack(side=tk.LEFT, padx=4, pady=4)

        # 이미지가 해제되지 않도록 보관
        win._ta = ta; win._tc = tc; win._ts = ts


    # [ADD] — 유틸: 데이터 배열에서 Z축 후보를 추정(보조)
    def _infer_axis_order_from_data(self, arr, ds_list):
        """
        arr: numpy ndarray (3D)
        ds_list: DICOM 슬라이스 리스트
        우리의 기본은 (Y, X, Z)임을 알고 있으므로, 실패 시 'YXZ'를 반환.
        """
        try:
            if arr.ndim == 3 and ds_list:
                zcand = len(ds_list)
                if zcand in arr.shape:
                    # 실제로 (Y, X, Z) 구조인지 간단히 확인
                    # Z축 길이가 ds_list 길이와 같은 축이면 그 축을 'Z'로 본다.
                    z_axis = [i for i,s in enumerate(arr.shape) if s == zcand]
                    if z_axis:
                        # arr.shape == (Y, X, Z)면 z_axis==2 인 경우가 많음
                        if z_axis[0] == 2:
                            return "YXZ"
                        # (Z, Y, X)면 z_axis==0
                        if z_axis[0] == 0:
                            return "ZYX"
                        # (Y, Z, X)면 z_axis==1
                        if z_axis[0] == 1:
                            return "YZX"
        except Exception:
            pass
        return "YXZ"   # 기본값(요청 사항)
    

    # [ADD or KEEP] — 유틸: IPP로 z방향/간격 추정
    def _estimate_slice_axis_from_ipp(self, ds_list, tol=1e-6):
        import numpy as np
        if not ds_list or len(ds_list) < 2:
            return np.array([0,0,1], float), None, None

        diffs = []
        for i in range(len(ds_list)-1):
            ipp0 = self._ipp_get(ds_list[i])
            ipp1 = self._ipp_get(ds_list[i+1])
            d = ipp1 - ipp0
            n = np.linalg.norm(d)
            if n > tol:
                diffs.append(d / n)
        if not diffs:
            return np.array([0,0,1], float), None, None

        V = np.vstack(diffs)
        s_dir = np.median(V, axis=0)
        n = np.linalg.norm(s_dir)
        s_dir = (s_dir / n) if n > 0 else np.array([0,0,1], float)

        dzs = []
        for i in range(len(ds_list)-1):
            ipp0 = self._ipp_get(ds_list[i])
            ipp1 = self._ipp_get(ds_list[i+1])
            dzs.append(np.dot((ipp1 - ipp0), s_dir))
        dzs = np.array([abs(x) for x in dzs if abs(x) > tol])
        if dzs.size == 0:
            return s_dir, None, None

        return s_dir, float(np.mean(dzs)), float(np.std(dzs))


    
    def _infer_axis_order_from_data(self, arr, ds_list):
        """
        arr: numpy ndarray (3D volume)
        ds_list: DICOM slice list (연속 슬라이스 수로 Z 후보를 추정)
        규칙:
        - Z축 후보: 슬라이스 개수와 일치하는 축
        - 나머지 두 축은 이미지의 행/열(Y,X)로 본다
        실패 시 기본 'ZYX' 반환
        """
        try:
            zcand = len(ds_list) if ds_list else None
            if arr.ndim == 3 and zcand and zcand in arr.shape:
                # Z는 ds_list 길이와 동일한 차원
                z_axis = [i for i,s in enumerate(arr.shape) if s == zcand]
                if z_axis:
                    z = z_axis[0]
                    dims = ["Y","X"]
                    rest = [i for i in range(3) if i != z]
                    # 큰쪽을 X, 작은쪽을 Y로 두는 heuristic
                    if arr.shape[rest[0]] >= arr.shape[rest[1]]:
                        order = ["Z","Y","X"] if (z,rest[0],rest[1])==(0,1,2) else None
                    # 일반화: 실제 인덱스→문자
                    idx2ch = {z:"Z", rest[0]:"X", rest[1]:"Y"}  # X,Y는 크기만으로는 확정 어려워 교환 가능
                    # 우선 Z만 확정, 나머지는 YX로 두고 정렬
                    order = "".join(idx2ch[i] for i in range(3))
                    # 보정: X/Y 순서를 전통적 (Y,X)로 맞춤
                    order = order.replace("XY","YX")
                    return order
        except Exception:
            pass
        return "ZYX"  # 기본

    
    def _apply_reg_measure_preset(self):
        """정합/계측 목적: spacing 불변 + 좌표계 정확성 보장"""
        if self.mode_reg_measure.get():
            self.xy_resample.set(False)
            self.keep_spacing_meta.set(False)

    def _on_toggle_reg_measure(self):
        self._apply_reg_measure_preset()


    def _apply_iso_guard(self):
            """ISO On이면 XY 보간을 강제로 비활성화하고 UI 위젯을 잠근다."""
            try:
                iso = bool(self.iso_on.get())
            except Exception:
                iso = False
            # ISO가 켜져 있으면 XY 체크 해제
            try:
                if iso and getattr(self, "xy_resample", None) and self.xy_resample.get():
                    self.xy_resample.set(False)
                    try:
                        self._log("ISO priority guard: XY disabled")
                    except Exception:
                        pass
            except Exception:
                pass
            # XY 관련 위젯 상태 동기화
            for name in ("cb_xy", "spn_xyw", "cbo_xym"):
                w = getattr(self, name, None)
                if w is None:
                    continue
                try:
                    w.configure(state=("disabled" if iso else "normal"))
                except Exception:
                    pass


    def _apply_xy_guard(self):
            """ISO가 이미 켜져 있으면 XY 체크를 되돌린다."""
            try:
                if bool(self.iso_on.get()) and getattr(self, "xy_resample", None) and bool(self.xy_resample.get()):
                    self.xy_resample.set(False)
                    try:
                        self._log("ISO priority guard: ignoring XY because ISO is ON")
                    except Exception:
                        pass
            except Exception:
                pass


            # initialize guards
            self._apply_iso_guard()
            self._apply_xy_guard()

    def _resample_audit_log(self, out_dir, context, roi_name,
                                src_shape, src_spacing, dst_shape, dst_spacing,
                                method, note=""):
            """Write/append audit CSV with spacing/size/method and note."""
            import os
            from datetime import datetime
            os.makedirs(out_dir, exist_ok=True)
            path = os.path.join(out_dir, "resample_audit.csv")
            new_file = not os.path.exists(path)

            ds0 = self.slices[0] if getattr(self, "slices", None) else None
            def getm(tag, default=""):
                return getattr(ds0, tag, default) if ds0 is not None else default

            row = [
                datetime.now().isoformat(timespec="seconds"), context, roi_name,
                f"{src_shape[2]}x{src_shape[1]}x{src_shape[0]}",
                f"{src_spacing[0]:.6f},{src_spacing[1]:.6f},{src_spacing[2]:.6f}",
                f"{dst_shape[2]}x{dst_shape[1]}x{dst_shape[0]}",
                f"{dst_spacing[0]:.6f},{dst_spacing[1]:.6f},{dst_spacing[2]:.6f}",
                method,
                str(getm("PatientID","")),
                str(getm("StudyInstanceUID","")),
                str(getm("SeriesInstanceUID","")),
                str(getm("FrameOfReferenceUID","")),
                note or ""
            ]
            if new_file:
                hdr = ["timestamp","context","roi_name",
                       "src_size(XxYxZ)","src_spacing(sx,sy,sz)",
                       "dst_size(XxYxZ)","dst_spacing(sx,sy,sz)",
                       "method","PatientID","StudyUID","SeriesUID","FORUID","note"]
                with open(path, "w", encoding="utf-8") as f:
                    f.write(",".join(hdr) + "\n")
            with open(path, "a", encoding="utf-8") as f:
                f.write(",".join(row) + "\n")

            self._log(f"[Audit] {roi_name} {src_shape}->{dst_shape}  spacing {src_spacing}->{dst_spacing}  ({method})  → {path}")



    def _dtype_and_range_from_ds(self, ds0):

            bits   = int(getattr(ds0, "BitsStored", getattr(ds0, "BitsAllocated", 16)))
            signed = int(getattr(ds0, "PixelRepresentation", 1))
            if bits <= 8:
                return (np.int8 if signed else np.uint8,  -128 if signed else 0, 127 if signed else 255)
            if bits <= 16:
                return (np.int16 if signed else np.uint16, -32768 if signed else 0, 32767 if signed else 65535)
            return (np.int32 if signed else np.uint32, -2147483648 if signed else 0, 2147483647 if signed else 4294967295)


    def _resample_xy_to(self, vol, sx, sy, target_w=None, target_h=None, method="linear"):
            """
            vol: (Z,Y,X) ndarray
            XY만 새 크기로 보간. 물리 크기 보존을 위해 새 spacing을 반환.
            """
            import numpy as np
            from PIL import Image
            z, y, x = vol.shape
            if target_w is None and target_h is None:
                return vol, (sx, sy)

            if target_w is None:
                target_w = int(round(x * (target_h / y)))
            if target_h is None:
                target_h = int(round(y * (target_w / x)))
            target_w = max(1, int(target_w)); target_h = max(1, int(target_h))

            # 새 spacing
            scale_x = target_w / float(x)
            scale_y = target_h / float(y)
            sx_new = float(sx) / max(scale_x, 1e-12)
            sy_new = float(sy) / max(scale_y, 1e-12)

            # 보간
            resample = Image.NEAREST if method=="nearest" else (Image.BICUBIC if method=="bspline" else Image.BILINEAR)
            tmp = np.empty((z, target_h, target_w), dtype=np.float32)
            for k in range(z):
                im  = Image.fromarray(vol[k].astype(np.float32), mode="F")
                imr = im.resize((target_w, target_h), resample=resample)
                tmp[k] = np.asarray(imr, dtype=np.float32)

            # dtype/범위 복원
            dst_dtype, lo, hi = self._dtype_and_range_from_ds(self.slices[0])
            out = np.clip(np.rint(tmp), lo, hi).astype(dst_dtype, copy=False)
            return out, (sx_new, sy_new)

    def _resample_iso_3d(self, vol, spacing_xyz, iso_mm=1.0, method="linear", direction=None):
        """
        3D 등방성 리샘플 (메모리 가드 포함)
        입력:
        - vol: (Z,Y,X) ndarray
        - spacing_xyz: (sx, sy, sz)  [mm/px]
        - iso_mm: 목표 등방성 스페이싱 [mm/px]
        - method: 'nearest' | 'linear' | 'bspline'
        - direction: 3x3 방향 코사인 행렬(선택)
        반환:
        - out: (Z,Y,X) ndarray (원본 DICOM 픽셀 dtype으로 복원)
        - (iso, iso, iso): 새 spacing 튜플
        """
        import math
        import numpy as np

        # -------- 메모리 상한 추정 --------
        try:
            import psutil
            cap_bytes = int(psutil.virtual_memory().available * 0.60)
        except Exception:
            cap_bytes = 2 * 1024**3  # 2 GiB 기본

        sx, sy, sz = map(float, spacing_xyz)
        nz, ny, nx = map(int, vol.shape)       # vol is z,y,x
        bytes_per_voxel = 4                    # float32 중간 연산 가정

        # 물리 크기(mm)
        phys_x = nx * sx
        phys_y = ny * sy
        phys_z = nz * sz
        iso = max(1e-6, float(iso_mm))

        # 목표 출력 크기(등방성)
        def dst_shape_for(iso_len):
            dx = max(1, int(round(phys_x / iso_len)))
            dy = max(1, int(round(phys_y / iso_len)))
            dz = max(1, int(round(phys_z / iso_len)))
            return dx, dy, dz

        dx, dy, dz = dst_shape_for(iso)
        est_bytes = dx * dy * dz * bytes_per_voxel

        if est_bytes > cap_bytes:
            # 물리부피/허용 보xel 수를 이용해 iso를 완화 (해석적 계산)
            max_vox = max(1, cap_bytes // bytes_per_voxel)
            phys_vol = phys_x * phys_y * phys_z
            iso_req = (phys_vol / max_vox) ** (1.0/3.0)
            new_iso = max(iso, iso_req)
            if new_iso > iso + 1e-9:
                self._log(f"[ISO] {iso:.4f} mm → {new_iso:.4f} mm (메모리 적합)")
            iso = new_iso
            dx, dy, dz = dst_shape_for(iso)

        # -------- 보간 수행: SimpleITK 우선, 실패 시 SciPy 폴백 --------
        out_f32 = None
        try:
            import SimpleITK as sitk
            img = sitk.GetImageFromArray(vol.astype(np.float32, copy=False))  # (z,y,x) → SITK 내부는 (x,y,z)
            img.SetSpacing((sx, sy, sz))
            if direction is not None:
                # direction: 3x3 (열: x=row, y=col, z=normal)로 만들어둔 행렬을 가정
                # SITK는 행 우선 플랫 벡터를 기대함
                dir_mat = np.asarray(direction, dtype=float)
                if dir_mat.shape == (3,3):
                    img.SetDirection(tuple(dir_mat.flatten(order="C")))

            ref = sitk.Image(int(dx), int(dy), int(dz), sitk.sitkFloat32)
            ref.SetSpacing((iso, iso, iso))
            try:
                ref.SetDirection(img.GetDirection())
            except Exception:
                pass

            interp = {
                "nearest": sitk.sitkNearestNeighbor,
                "linear":  sitk.sitkLinear,
                "bspline": sitk.sitkBSpline,
            }.get(str(method).lower(), sitk.sitkLinear)

            rf = sitk.ResampleImageFilter()
            rf.SetReferenceImage(ref)
            rf.SetInterpolator(interp)
            rf.SetDefaultPixelValue(0.0)
            out_img = rf.Execute(img)
            out_f32 = sitk.GetArrayFromImage(out_img)  # (z,y,x)
        except Exception as e:
            # SciPy 폴백
            try:
                import scipy.ndimage as ndi
                zoom_z = dz / float(nz)
                zoom_y = dy / float(ny)
                zoom_x = dx / float(nx)
                order = {"nearest":0, "linear":1, "bspline":3}.get(str(method).lower(), 1)
                out_f32 = ndi.zoom(vol.astype(np.float32, copy=False),
                                (zoom_z, zoom_y, zoom_x),
                                order=order, mode="nearest", prefilter=(order>1))
            except Exception as e2:
                raise RuntimeError(f"Isotropic resample failed: SITK[{e}] / SciPy[{e2}]")

        # -------- dtype/범위 복원 --------
        dst_dtype, lo, hi = self._dtype_and_range_from_ds(self.slices[0])
        out = np.clip(np.rint(out_f32), lo, hi).astype(dst_dtype, copy=False)
        return out, (iso, iso, iso)

    def _log(self, msg):
            self.log.insert(tk.END, datetime.now().strftime("[%H:%M:%S] ") + msg + "\n")
            self.log.see(tk.END)

    def _set_contrast(self, v):
            self.contrast = v
            self._show(self.slice_index)

        # === CSV 메타 수집 & 헤더 타이틀 매핑 ===
    def _slice_meta(self, ds):
            """
            단일 슬라이스 DICOM 메타를 dict로 반환(문자열 직렬화).
            """
            def num(v):
                try:
                    return float(v)
                except Exception:
                    return ""
            def arr_str(v):
                """
                DICOM MultiValue/tuple/리스트를 공백 구분 문자열로 직렬화.
                - 숫자형이면 float로 표준화, 실패하면 str로 폴백
                - None/스칼라에도 안전
                """
                try:
                    # 이터러블인데 문자열/바이트가 아니면 각 원소 직렬화
                    if hasattr(v, "__iter__") and not isinstance(v, (str, bytes)):
                        try:
                            return " ".join(str(float(x)) for x in v)
                        except Exception:
                            return " ".join(str(x) for x in v)
                    # 스칼라 하나만 들어온 경우
                    try:
                        return str(float(v))
                    except Exception:
                        return str(v) if v is not None else ""
                except Exception:
                    return ""


            px  = getattr(ds, "PixelSpacing", None)
            iop = getattr(ds, "ImageOrientationPatient", None)
            ipp = getattr(ds, "ImagePositionPatient", None)

            meta = {
                "PatientID": getattr(ds, "PatientID", ""),
                "StudyInstanceUID": getattr(ds, "StudyInstanceUID", ""),
                "SeriesInstanceUID": getattr(ds, "SeriesInstanceUID", ""),
                "FrameOfReferenceUID": getattr(ds, "FrameOfReferenceUID", ""),
                "SOPInstanceUID": getattr(ds, "SOPInstanceUID", ""),
                "InstanceNumber": getattr(ds, "InstanceNumber", ""),
                "Modality": getattr(ds, "Modality", ""),
                "Rows": getattr(ds, "Rows", ""),
                "Columns": getattr(ds, "Columns", ""),
                "PixelSpacingX": (num(px[0]) if (px is not None and len(px) > 0) else ""),
                "PixelSpacingY": (num(px[1]) if (px is not None and len(px) > 1) else ""),
                "SliceThickness": num(getattr(ds, "SliceThickness", "")),
                "SpacingBetweenSlices": num(getattr(ds, "SpacingBetweenSlices", "")),
                "ImageOrientationPatient": arr_str(iop),
                "ImagePositionPatient": arr_str(ipp),
                "RescaleSlope": getattr(ds, "RescaleSlope", ""),
                "RescaleIntercept": getattr(ds, "RescaleIntercept", ""),
                "BitsAllocated": getattr(ds, "BitsAllocated", ""),
                "BitsStored": getattr(ds, "BitsStored", ""),
                "HighBit": getattr(ds, "HighBit", ""),
                "PixelRepresentation": getattr(ds, "PixelRepresentation", ""),
                "PhotometricInterpretation": getattr(ds, "PhotometricInterpretation", ""),
            }
            return meta

    def _csv_header_titles_points(self):
            """
            포인트 CSV 헤더(사람이 읽기 쉬운 제목). 순서가 중요하므로 리스트 반환.
            """
            base = [
                ("idx", "Index"),
                ("slice", "Slice(Z)"),
                ("x", "X(px)"),
                ("y", "Y(px)"),
                ("gray", "GrayValue"),
                ("hu", "HU"),
                ("disp", "DispGray(0-255)"),
                ("slope", "RescaleSlope"),
                ("intercept", "RescaleIntercept"),
                ("timestamp", "Timestamp"),
            ]
            meta_titles = [
                ("PatientID","PatientID"),
                ("StudyInstanceUID","StudyInstanceUID"),
                ("SeriesInstanceUID","SeriesInstanceUID"),
                ("FrameOfReferenceUID","FrameOfReferenceUID"),
                ("SOPInstanceUID","SOPInstanceUID"),
                ("InstanceNumber","InstanceNumber"),
                ("Modality","Modality"),
                ("Rows","Rows(pixels)"),
                ("Columns","Columns(pixels)"),
                ("PixelSpacingX","PixelSpacingX(mm)"),
                ("PixelSpacingY","PixelSpacingY(mm)"),
                ("SliceThickness","SliceThickness(mm)"),
                ("SpacingBetweenSlices","SpacingBetweenSlices(mm)"),
                ("ImageOrientationPatient","ImageOrientationPatient"),
                ("ImagePositionPatient","ImagePositionPatient"),
                ("BitsAllocated","BitsAllocated"),
                ("BitsStored","BitsStored"),
                ("HighBit","HighBit"),
                ("PixelRepresentation","PixelRepresentation"),
                ("PhotometricInterpretation","PhotometricInterpretation"),
            ]
            return base, meta_titles

    def _csv_header_titles_roi(self):
            """
            ROI CSV 헤더(사람이 읽기 쉬운 제목). 순서가 중요하므로 리스트 반환.
            """
            base = [
                ("slice","Slice(Z)"),
                ("xs","ROI_X(px)"),
                ("ys","ROI_Y(px)"),
                ("ws","ROI_W(px)"),
                ("hs","ROI_H(px)"),
                ("ws_mm","ROI_W(mm)"),
                ("hs_mm","ROI_H(mm)"),
                ("mean","HU_mean"),
                ("std","HU_std"),
                ("min","HU_min"),
                ("max","HU_max"),
                ("gray_mean","Gray_mean"), ("gray_std","Gray_std"),
                ("gray_min","Gray_min"),   ("gray_max","Gray_max"),
                ("disp_mean","Disp_mean"), ("disp_std","Disp_std"),
                ("disp_min","Disp_min"),   ("disp_max","Disp_max"),
                ("slope","RescaleSlope"),
                ("intercept","RescaleIntercept"),
                ("timestamp","Timestamp"),
            ]
            # 메타는 포인트와 동일
            _, meta_titles = self._csv_header_titles_points()
            return base, meta_titles

    def _dtype_and_range_from_ds(self, ds0):
            bits = int(getattr(ds0, "BitsStored", getattr(ds0, "BitsAllocated", 16)))
            signed = int(getattr(ds0, "PixelRepresentation", 1))
            if bits <= 8:
                return (np.int8 if signed else np.uint8, -128 if signed else 0, 127 if signed else 255)
            elif bits <= 16:
                return (np.int16 if signed else np.uint16, -32768 if signed else 0, 32767 if signed else 65535)
            else:
                return (np.int32 if signed else np.uint32, -2147483648 if signed else 0, 2147483647 if signed else 4294967295)


        # removed duplicate _resample_xy_to definition
    def prepare_roi(self):
            """ROI 드래그 시작 준비: 기존 ROI 초기화하고 현재 슬라이스를 갱신."""
            self.roi = []
            self.start_pt = None
            self._show(self.slice_index)
            self._log("Drag on the preview (512×512) to set an XY ROI. Then set Z Start/End and save.")

    def load_dicom(self):
            d = filedialog.askdirectory(title="Select DICOM folder")
            self.dicom_dir = d

            if not d: return
            files = [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.lower().endswith(".dcm")]
            if not files:
                messagebox.showerror("Error","No DICOM files"); return

            # 1) 모두 로드(멀티프레임은 일단 제외: NumberOfFrames 있으면 건너뜀)
            raw = []
            for p in files:
                try:
                    ds = pydicom.dcmread(p, force=True)
                    if hasattr(ds, "NumberOfFrames") and int(ds.NumberOfFrames) > 1:
                        # 필요시 멀티프레임 확장 로직 추가 가능(이번엔 제외)
                        continue
                    arr = ds.pixel_array  # 여기서 shape 확인
                    H, W = arr.shape[-2], arr.shape[-1]
                    sx, sy = None, None
                    try:
                        sx, sy = [float(v) for v in getattr(ds, "PixelSpacing", [np.nan, np.nan])]
                    except Exception:
                        sx, sy = np.nan, np.nan
                    iop = tuple([float(v) for v in getattr(ds, "ImageOrientationPatient", [1,0,0,0,1,0])])
                    key = (
                        getattr(ds, "SeriesInstanceUID", "NA"),
                        tuple(round(v, 6) for v in iop),
                        W, H,
                        round(float(sx), 6) if sx==sx else None,  # NaN 처리
                        round(float(sy), 6) if sy==sy else None,
                        int(getattr(ds, "BitsAllocated", 16)),
                        int(getattr(ds, "PixelRepresentation", 1)),
                    )
                    raw.append((key, ds, arr))
                except Exception as e:
                    self._log(f"drop (load failed): {os.path.basename(p)} → {e}")

            if not raw:
                messagebox.showerror("Error","No loadable single-frame DICOMs"); return

            # 2) 그룹핑: (SeriesUID, IOP, Rows/Cols, Spacing, Bits, Representation)
            from collections import defaultdict
            buckets = defaultdict(list)
            for key, ds, arr in raw:
                buckets[key].append((ds, arr))

            # 3) 시리즈 선택: CT/16bit 우선 → 없으면 최대 그룹
            def _group_score(k, lst):
                # k = (SeriesUID, IOP, W, H, sx, sy, BitsAllocated, PixelRepresentation)
                ds0 = lst[0][0]
                modality = str(getattr(ds0, "Modality", "")).upper()
                bits_stored = int(getattr(ds0, "BitsStored", getattr(ds0, "BitsAllocated", 16)))
                score = 0
                if modality == "CT": score += 1000         # CT 최우선
                score += min(bits_stored, 32) * 10         # 비트가 높을수록 가점
                if hasattr(ds0, "RescaleSlope") and hasattr(ds0, "RescaleIntercept"):
                    score += 50                             # HU 메타 존재 가점
                score += len(lst)                           # 동률이면 파일 수로 보정
                return score

            key_main = max(buckets.keys(), key=lambda k: _group_score(k, buckets[k]))
            grp = buckets[key_main]

            dropped = sum(len(v) for k, v in buckets.items() if k != key_main)
            if dropped:
                self._log(f"filtered out {dropped} files (kept best series by Modality/Bits/size; dropped {dropped})")

            # 선택된 시리즈 요약 로그
            ds0 = grp[0][0]
            bits_alloc  = int(getattr(ds0, "BitsAllocated", ""))
            bits_stored = int(getattr(ds0, "BitsStored", bits_alloc or 0))
            pix_repr    = int(getattr(ds0, "PixelRepresentation", -1))
            mod         = str(getattr(ds0, "Modality", ""))
            rows, cols  = int(getattr(ds0, "Rows", 0)), int(getattr(ds0, "Columns", 0))
            self._log(f"Selected series: Modality={mod}, Size={cols}x{rows}, BitsStored={bits_stored}, PixelRep={pix_repr}")


            # 4) IPP·IOP 기반으로 Z 정렬(IPP가 없으면 InstanceNumber 백업)
            def _sort_key(ds):
                # normal = row×col, proj = (IPP·normal)
                try:
                    iop = [float(v) for v in ds.ImageOrientationPatient]
                    row = np.array(iop[:3]); col = np.array(iop[3:6])
                    row /= (np.linalg.norm(row)+1e-12); col /= (np.linalg.norm(col)+1e-12)
                    normal = np.cross(row, col); normal /= (np.linalg.norm(normal)+1e-12)
                    ipp = np.array([float(v) for v in ds.ImagePositionPatient])
                    return float(np.dot(ipp, normal))
                except Exception:
                    return float(getattr(ds, "InstanceNumber", 0))
            grp.sort(key=lambda it: _sort_key(it[0]))  # (ds, arr)

            # 5) 스택 구성
            self.slices = [ds for ds, arr in grp]
            self.stack  = np.stack([arr for ds, arr in grp])  # (z,y,x)

            # ---- 효과상 8비트 탐지: 값 범위가 0–255에 갇혀 있는지 확인 ----
            try:
                first = self.slices[0]
                arr0 = first.pixel_array
                gmin, gmax = int(arr0.min()), int(arr0.max())
                bits_stored = int(getattr(first, "BitsStored", getattr(first, "BitsAllocated", 16)))

                # gmax가 255 이하면, 16비트 컨테이너라도 사실상 8비트로 쓰인 파생본일 수 있음
                self.is_effectively_8bit = bool(gmax <= 255)

                self._log(
                    f"Selected series value range [{gmin},{gmax}] with BitsStored={bits_stored} → "
                    f"{'EFFECTIVE-8BIT' if self.is_effectively_8bit else 'OK-16BIT'}"
                )

                if self.is_effectively_8bit and bits_stored > 8:
                    self._log(
                        "[WARN] Pixels use only 0–255 although BitsStored>8. "
                        "Likely a derived/tone-mapped series. Load the original 16-bit CT series if you need true 16-bit Gray."
                    )
            except Exception:
                self.is_effectively_8bit = False


            # 6) 메타 초기화
            first = self.slices[0]
            self.orig_sx, self.orig_sy = [float(v) for v in getattr(first, "PixelSpacing", [1.0, 1.0])]
            self.orig_th = float(getattr(first, "SliceThickness", 1.0))
            self.orig_slope = float(getattr(first, "RescaleSlope", 1.0))
            self.orig_inter = float(getattr(first, "RescaleIntercept", -1024.0))

            self.sx, self.sy, self.th = self.orig_sx, self.orig_sy, self.orig_th
            self.slope, self.inter   = self.orig_slope, self.orig_inter
            for e, v in ((self.e_sx, self.sx),(self.e_sy,self.sy),(self.e_th,self.th),
                        (self.e_k,self.slope),(self.e_b,self.inter)):
                e.delete(0, tk.END); e.insert(0, f"{v:.6f}")

            # WindowCenter/Width 보관 (없으면 안전 기본값)
            try:
                first = self.slices[0]
                def _first_float(x):
                    if hasattr(x, "__len__") and not isinstance(x, (str, bytes)):
                        return float(x[0])
                    return float(x)
                self.wl = _first_float(getattr(first, "WindowCenter", 40.0))
                self.ww = _first_float(getattr(first, "WindowWidth", 400.0))
            except Exception:
                self.wl, self.ww = 40.0, 400.0

            n = len(self.stack); mid = n//2
            self.s_slice.config(to=n-1); self.s_slice.set(mid)
            self.z_start, self.z_end = 0, n-1
            self.sb_z0.config(from_=0, to=n-1); self.sb_z1.config(from_=0, to=n-1)
            self.sb_z0.delete(0,tk.END); self.sb_z0.insert(0, str(self.z_start))
            self.sb_z1.delete(0,tk.END); self.sb_z1.insert(0, str(self.z_end))
            self.roi = []
            self._show(mid)
            self._auto_wlww_on_load(mid)
            self.find_subject_auto()       # 몸체 중심 슬라이스 + ROI 자동 설정


                        # 8-bit 경고 (Gray가 0–255로 고정됨)
            try:
                bits_stored = int(getattr(first, "BitsStored", getattr(first, "BitsAllocated", 16)))
                if bits_stored <= 8:
                    self._log("[WARN] This series is 8-bit (BitsStored<=8). Gray stats are 0–255. If you need 16-bit, load the 16-bit CT series (or place only that series in the folder).")
            except Exception:
                pass


            # 7) IPP 기반 간격 보고
            mean_gap, std_gap = _infer_slice_spacing_from_ipp(self.slices)
            if mean_gap:
                self._log(f"IPP-based slice gap = {mean_gap:.6f} mm (std {std_gap:.6f})")
            else:
                self._log("IPP-based gap not available; using SliceThickness as-is")
    # [ADD] ───────────────────────────────────────────────────────────────────
    def _iop_row_col(self, ds):
        iop = getattr(ds, "ImageOrientationPatient", None)
        if iop is None:
            r = np.array([1,0,0], float); c = np.array([0,1,0], float)
            return r, c
        v = [float(x) for x in (list(iop) if isinstance(iop,(list,tuple))
                                else str(iop).replace('[','').replace(']','').split(','))]
        r = np.array(v[:3], float); c = np.array(v[3:6], float)
        r /= (np.linalg.norm(r) or 1.0); c /= (np.linalg.norm(c) or 1.0)
        return r, c

    def _ipp_get(self, ds):
        ipp = getattr(ds, "ImagePositionPatient", None)
        if ipp is None: return np.array([0.0,0.0,0.0], float)
        return np.array([float(x) for x in (ipp if isinstance(ipp,(list,tuple))
                        else str(ipp).split('\\'))], float)

    def _ipp_set(self, ds, v3):
        ds.ImagePositionPatient = [f"{float(x):.6f}" for x in list(v3)]

    def _shift_ipp_by_crop(self, ds, dx_px:int, dy_px:int, sx:float, sy:float):
        """크롭으로 좌상단이 (dx,dy)픽셀 이동 → IPP 보정(mm)"""
        r_dir, c_dir = self._iop_row_col(ds)
        ipp0 = self._ipp_get(ds)
        delta = r_dir * (dy_px * sy) + c_dir * (dx_px * sx)
        self._ipp_set(ds, ipp0 + delta)

    def _derive_base_name(self, in_path):
        """입력 폴더/파일 경로에서 출력 접두(base_name) 생성"""
        p = Path(in_path)
        name = p.stem if p.is_file() else p.name
        name = re.sub(r"[^0-9A-Za-z가-힣_\-]+", "_", name).strip("_")
        return name or "output"

    def estimate_z_spacing_from_ipp(self, ds_list, tol=1e-6):
        """IOP 법선 방향으로 IPP 투영 → 연속 슬라이스 간 거리 평균/표준편차(mm)"""
        if not ds_list: return None, None
        r_dir, c_dir = self._iop_row_col(ds_list[0])
        s_dir = np.cross(r_dir, c_dir); s_dir /= (np.linalg.norm(s_dir) or 1.0)

        pos = []
        for ds in ds_list:
            ipp = self._ipp_get(ds)
            pos.append(float(np.dot(ipp, s_dir)))
        pos = np.sort(np.array(pos))
        diffs = np.diff(pos)
        diffs = np.abs(diffs[diffs > tol])
        if diffs.size == 0: return None, None
        return float(np.mean(diffs)), float(np.std(diffs))

    def _check_z_spacing_consistency(self, ds_list):
        """SliceThickness vs IPP-mean 비교 → 임계 초과 시 경고/로그"""
        if not ds_list:
            return
        try:
            sz_st = float(getattr(ds_list[0], "SliceThickness", 0))
        except Exception:
            sz_st = 0.0
        mean_dz, std_dz = self.estimate_z_spacing_from_ipp(ds_list)
        if mean_dz is None:
            return
        diff = abs((sz_st or 0.0) - mean_dz)
        if diff > float(self.z_eps_mm.get()):
            msg = (f"z-spacing 불일치: SliceThickness={sz_st:.6f} mm, "
                f"IPP-avg={mean_dz:.6f} mm, Δ={diff:.6f} mm "
                f"(허용 {self.z_eps_mm.get():.3f} mm 초과)")
            try:
                self.LOG.warning(msg)
            except Exception:
                pass
            try:
                messagebox.showwarning("z-spacing 경고", msg)
            except Exception:
                print("[z-spacing 경고]", msg)



    # [ADD] ───────────────────────────────────────────────────────────────────
    def _on_dicom_loaded(self, ds):
        try:
            self.orig_sx, self.orig_sy = map(float, ds.PixelSpacing)
        except Exception:
            pass
        try:
            self.orig_sz = float(getattr(ds, "SliceThickness", 0))
        except Exception:
            pass

    # 로드 시점에서 위 함수를 1회 호출하도록 연결:
    # 예) ds = ...  # 로드된 레퍼런스 슬라이스
    # self._on_dicom_loaded(ds)


    def _draw_text_with_outline(self, draw, xy, text, fill="yellow"):
            x, y = xy
            for dx, dy in [(-1,0),(1,0),(0,-1),(0,1)]:
                draw.text((x+dx, y+dy), text, fill="black")
            draw.text((x, y), text, fill=fill)

    def _draw_box(self, draw, x, y, w, h, color="yellow"):
            # 가시성 강화: 검정 굵은 테두리 위에 색상선
            draw.rectangle([x, y, x+w, y+h], outline="black", width=4)
            draw.rectangle([x, y, x+w, y+h], outline=color,    width=2)

        # ── Polygon 드로잉(프리뷰 512) ───────────────────────────────────────
    def _draw_polygon(self, draw, pts, hover=None, closed=False, color="yellow"):
        if not pts: return
        # 선분
        for i in range(1, len(pts)):
            draw.line([pts[i-1], pts[i]], fill=color, width=2)
        # 미닫힘 상태에서 마지막 점 → hover 임시선
        if hover and not closed:
            draw.line([pts[-1], hover], fill=color, width=1)
        # 닫힘이면 마지막 → 첫점 연결
        if closed and len(pts) >= 3:
            draw.line([pts[-1], pts[0]], fill=color, width=2)
        # 꼭짓점 마커
        for (x, y) in pts:
            r = 3
            draw.ellipse([x-r, y-r, x+r, y+r], fill=color)

    # ── 폴리곤 좌표 변환(프리뷰 → 원해상도) ───────────────────────────────
    def _poly_native_pts(self):
        if self.stack is None or not self.poly_pts: return []
        W = int(self.stack.shape[2]); H = int(self.stack.shape[1])
        fx = W / 512.0; fy = H / 512.0
        return [(int(round(x * fx)), int(round(y * fy))) for (x, y) in self.poly_pts]

    def _poly_bbox_native(self):
        pts = self._poly_native_pts()
        if len(pts) < 3: return (0, 0, 0, 0)
        xs = min(p[0] for p in pts); ys = min(p[1] for p in pts)
        xe = max(p[0] for p in pts); ye = max(p[1] for p in pts)
        return xs, ys, xe, ye  # (좌상, 우하)

    def _poly_mask_native_local(self, xs, ys, xe, ye):
        """원해상도 기준 폴리곤 마스크(크기: hs×ws)를 반환. 외부=0, 내부=1"""
        from PIL import Image, ImageDraw
        ws, hs = max(1, xe - xs), max(1, ye - ys)
        mask = Image.new("1", (ws, hs), 0)
        draw = ImageDraw.Draw(mask)
        pts = [(x - xs, y - ys) for (x, y) in self._poly_native_pts()]
        if len(pts) >= 3:
            draw.polygon(pts, outline=1, fill=1)  # 내부 채움
        return np.array(mask, dtype=bool)
        

    def _draw_marker(self, draw, x, y, color="yellow"):
            # 원 + 크로스헤어
            r = 4
            draw.ellipse([x-r, y-r, x+r, y+r], fill=color)
            draw.line([x-8, y, x+8, y], fill=color, width=1)
            draw.line([x, y-8, x, y+8], fill=color, width=1)


    def _render_slice_img(self, idx, overlay=True):
                        # 슬라이스별 HU 변환 사용
            ds  = self.slices[idx]
            raw = ds.pixel_array.astype(np.float32)
            slope_used = float(getattr(ds, "RescaleSlope",  getattr(self, "slope", 1.0)))
            inter_used = float(getattr(ds, "RescaleIntercept", getattr(self, "inter", 0.0)))
            hu  = raw * slope_used + inter_used

            img8, used_wl, used_ww = self._hu_to_uint8(
                hu,
                wl=getattr(self, "wl", None),
                ww=getattr(self, "ww", None)
            )
            img = Image.fromarray(img8, mode="L").resize((512, 512), Image.LANCZOS)


            # 안전 기본값(해당 속성이 없더라도 동작)
            brightness = float(getattr(self, "brightness", 1.0))
            contrast   = float(getattr(self, "contrast",   1.0))
            gamma      = float(getattr(self, "gamma",      1.0))

            # ① Brightness → ② Contrast
            img = ImageEnhance.Brightness(img).enhance(max(0.10, min(3.00, brightness)))
            img = ImageEnhance.Contrast(img).enhance(  max(0.10, min(5.00, contrast)))

            # ③ Gamma (LUT 적용; gamma==1이면 스킵)
            if abs(gamma - 1.0) > 1e-3:
                inv = 1.0 / max(gamma, 1e-6)  # 0 분모 방지
                lut = [int(min(255, max(0, round((i/255.0)**inv * 255.0)))) for i in range(256)]
                img = img.point(lut)

            # 화면 Gray 측정을 위해 보관
            try:
                self._last_preview_img = img.copy()
            except Exception:
                self._last_preview_img = None

            if not overlay:
                   return img

            draw = ImageDraw.Draw(img)

            # 안전 기본값(설정 변수 없을 때 대비)
            show_vals = bool(getattr(self, "overlay_show_values", True))
            # draw = ImageDraw.Draw(img) 바로 위/아래에서 img는 이미 512x512 L 모드로 준비됨
            roi_col = self._choose_roi_color(img)  # ← 자동/수동 통합
            pt_col  = getattr(self, "point_color", None)
            pt_col  = pt_col.get() if hasattr(pt_col, "get") else "yellow"
            pt_col    = getattr(self, "point_color", None)
            pt_col    = pt_col.get() if hasattr(pt_col, "get") else "yellow"

            # ROI 박스 + 라벨
            if self.roi:
                x, y, w, h = self.roi
                self._draw_box(draw, x, y, w, h, color=roi_col)
                if show_vals and getattr(self, "last_roi_stats", None) and self.last_roi_stats["slice"] == idx:
                    m = self.last_roi_stats
                    label = f"ROI HU: {m['mean']:.2f}±{m['std']:.2f}  min {m['min']:.0f} / max {m['max']:.0f}"
                    self._draw_text_with_outline(draw, (x+3, y+3), label, fill="yellow")
                        # Polygon ROI 오버레이
            if getattr(self, "poly_pts", None):
                self._draw_polygon(draw,
                                   self.poly_pts,
                                   hover=(self.poly_hover if not self.poly_closed else None),
                                   closed=bool(self.poly_closed),
                                   color=roi_col)
        
            # 포인트 마커/라벨
            W = int(self.stack.shape[2]); H = int(self.stack.shape[1])
            fx = 512.0 / W; fy = 512.0 / H
            last_on_slice = None
            for p in self.points:
                if p["slice"] != idx: 
                    continue
                px, py = int(p["x"] * fx), int(p["y"] * fy)
                # invert_z는 Z 순서 반전 옵션이므로 X를 뒤집지 않는다
                self._draw_marker(draw, px, py, color=pt_col)
                draw.text((px+8, py-10), str(p["idx"]), fill=pt_col)
                last_on_slice = (px, py, p)

            # 마지막 포인트 값 라벨(좌표/HU)
            if show_vals and last_on_slice:
                px, py, p = last_on_slice
                disp_txt = f" D={p.get('disp','')}" if p.get('disp','') != '' else ""
                self._draw_text_with_outline(
                    draw, (px+8, py+12),
                    f"({p['x']},{p['y']}) HU={p['hu']:.2f}  G={p.get('gray','')}{disp_txt}",
                    fill="yellow"
                )
            return img

    def _auto_wlww_from_slice(self, idx: int, low_p=0.5, high_p=99.5):
        """
        단일 슬라이스에서 robust percentile 기반으로 WL/WW 산출.
        - HU를 기준으로 계산
        - 몸체 마스크(hu>-800)를 우선 적용하여 배경 공기 제외
        """
        import numpy as np
        ds = self.slices[idx]
        raw = ds.pixel_array.astype(np.float32)
        slope = float(getattr(ds, "RescaleSlope", 1.0))
        inter = float(getattr(ds, "RescaleIntercept", 0.0))
        hu = raw * slope + inter

        # 몸체 마스크(배경 공기 제외)
        body = hu > -800
        arr = hu[body] if np.count_nonzero(body) > hu.size * 0.05 else hu  # 몸체 픽셀이 너무 적으면 전체 사용

        # 퍼센타일 계산
        lo = float(np.percentile(arr, low_p))
        hi = float(np.percentile(arr, high_p))
        ww = max(50.0, hi - lo)            # 지나치게 좁지 않게 최소 폭 보장
        wl = (hi + lo) * 0.5

        # 분포가 비정상적으로 한쪽으로 몰렸으면 CT 프리셋으로 백업
        air_frac  = float((hu < -900).mean())
        bone_frac = float((hu > 300).mean())
        if ww < 80 and (air_frac > 0.6 or bone_frac > 0.2):
            # 아주 드문 실패 케이스: 강제 프리셋
            if air_frac > bone_frac:
                wl, ww = -600.0, 1500.0   # Lung
            elif bone_frac > 0.2:
                wl, ww = 300.0, 1500.0    # Bone
            else:
                wl, ww = 40.0, 400.0      # Soft-tissue

        self.wl, self.ww = float(wl), float(ww)
        try: self._log(f"[AutoWL] slice {idx} → WL={self.wl:.1f}, WW={self.ww:.1f}")
        except Exception: pass
        self._show(self.slice_index)


    def _auto_wlww_from_series(self, low_p=0.5, high_p=99.5):
        """
        시리즈 전체(가운데 1/3 슬라이스, 격자 샘플)에서 robust WL/WW 산출.
        - 배경 공기 제외
        - 속도 위해 격자 샘플링(step=4)
        """
        import numpy as np
        n = len(self.slices)
        if n == 0:
            return
        z0 = n // 3
        z1 = n - n // 3
        samples = []
        step = 4
        for z in range(z0, z1):
            ds = self.slices[z]
            raw = ds.pixel_array.astype(np.float32)
            slope = float(getattr(ds, "RescaleSlope", 1.0))
            inter = float(getattr(ds, "RescaleIntercept", 0.0))
            hu = raw * slope + inter
            body = hu > -800
            sub = hu[::step, ::step]
            msk = body[::step, ::step]
            if np.count_nonzero(msk) > sub.size * 0.05:
                samples.append(sub[msk].ravel())
            else:
                samples.append(sub.ravel())
        arr = np.concatenate(samples) if samples else None
        if arr is None or arr.size == 0:
            return self._auto_wlww_from_slice(self.slice_index, low_p, high_p)

        lo = float(np.percentile(arr, low_p))
        hi = float(np.percentile(arr, high_p))
        ww = max(50.0, hi - lo)
        wl = (hi + lo) * 0.5

        # 백업 프리셋
        air_frac  = float((arr < -900).mean()) if arr.size else 0.0
        bone_frac = float((arr > 300).mean())  if arr.size else 0.0
        if ww < 80 and (air_frac > 0.6 or bone_frac > 0.2):
            if air_frac > bone_frac:
                wl, ww = -600.0, 1500.0
            elif bone_frac > 0.2:
                wl, ww = 300.0, 1500.0
            else:
                wl, ww = 40.0, 400.0

        self.wl, self.ww = float(wl), float(ww)
        try: self._log(f"[AutoWL] series(mid⅓) → WL={self.wl:.1f}, WW={self.ww:.1f}")
        except Exception: pass
        self._show(self.slice_index)


    def _auto_wlww_on_load(self, mid_idx: int):
        """
        로드 직후 자동 판단:
        - DICOM의 WindowCenter/Width로 먼저 렌더
        - 프리뷰가 너무 어둡거나(평균<15) 밝거나(>240) 평탄(표준편차<12)하면 자동으로 재조정
        """
        try:
            img = self._render_slice_img(mid_idx, overlay=False)
            arr = np.asarray(img.convert("L"))
            meanv = float(arr.mean()); stdv = float(arr.std())
            if meanv < 15.0 or meanv > 240.0 or stdv < 12.0:
                # 시리즈 기반으로 한 번에 잡는 게 안정적
                self._auto_wlww_from_series()
            else:
                # 현재 값 유지(원태그가 적절)
                self._log(f"[AutoWL] keep DICOM WL/WW (mean={meanv:.1f}, std={stdv:.1f})")
        except Exception:
            # 실패 시 슬라이스 기반으로라도 시도
            self._auto_wlww_from_slice(mid_idx)


    def _preview_pt_to_native(self, px:int, py:int):
        """
        프리뷰(512×512) 좌표 → 원본 좌표. ROI 변환 경로 재사용으로 오차 제거.
        """
        keep = self.roi[:] if self.roi else []
        try:
            self.roi = [int(px), int(py), 1, 1]
            xs, ys, ws, hs = self._roi_xywh_native()
            return int(xs), int(ys)
        finally:
            self.roi = keep

    def _display_gray_from_preview(self, px:int, py:int):
        """
        마지막 프리뷰 이미지에서 화면 Gray(0–255) 읽기.
        """
        try:
            if self._last_preview_img is None:
                _ = self._render_slice_img(self.slice_index, overlay=False)
            if self._last_preview_img is None:
                return ""
            if px < 0 or py < 0 or px >= 512 or py >= 512:
                return ""
            return int(self._last_preview_img.getpixel((int(px), int(py))))
        except Exception:
            return ""


    def _choose_roi_color(self, img_gray_512):
        """
        프리뷰(512, L모드) 이미지 밝기를 보고 ROI 라인 색을 자동 선택.
        - auto가 꺼져 있으면 ROI color 설정값을 그대로 사용
        """
        try:
            if hasattr(self, "roi_color_auto") and self.roi_color_auto.get():
                import numpy as np
                arr = np.asarray(img_gray_512.convert("L"))
                meanv = float(arr.mean())
                # 어두운 배경(CT 뼈/금속 대비)에는 노랑, 밝은 배경에는 빨강
                return "yellow" if meanv < 110 else "red"
        except Exception:
            pass
        rc = getattr(self, "roi_color", None)
        return rc.get() if hasattr(rc, "get") else "yellow"


    def _set_brightness(self, v: float):
            self.brightness = max(0.10, min(3.00, float(v)))
            self._show(self.slice_index)
            self._maybe_autosave_preview(reason="brightness")

    def _set_contrast(self, v: float):
            self.contrast = max(0.10, min(5.00, float(v)))
            self._show(self.slice_index)
            self._maybe_autosave_preview(reason="contrast")

    def _set_gamma(self, v: float):
            self.gamma = max(0.20, min(5.00, float(v)))
            self._show(self.slice_index)
            self._maybe_autosave_preview(reason="gamma")

    def _set_wl(self, v: float):
        self.wl = float(v)
        self._show(self.slice_index)
        self._maybe_autosave_preview(reason="wl")

    def _set_ww(self, v: float):
        self.ww = max(1.0, float(v))
        self._show(self.slice_index)
        self._maybe_autosave_preview(reason="ww")


    def _maybe_autosave_preview(self, reason="tone"):
            """
            Auto-save Preview 토글이 켜져 있을 때만 512 프리뷰 PNG를 떨어뜨림.
            슬라이더 드래그 중 과도 저장 방지를 위해 디바운스.
            파일명에 톤 파라미터와 slice index, 이유를 표기.
            """
            try:
                if not self.auto_save_preview.get():
                    return
            except Exception:
                return

            import time
            now = time.time()
            if now - self._last_preview_save_ts < getattr(self, "_preview_autosave_debounce_sec", 0.7):
                return
            self._last_preview_save_ts = now

            out_dir = self._measure_root("Preview")
            os.makedirs(out_dir, exist_ok=True)

            img = self._render_slice_img(self.slice_index, overlay=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            fname = (f"slice_{self.slice_index:04}_preview_{reason}"
                    f"_B{self.brightness:.2f}_C{self.contrast:.2f}_G{self.gamma:.2f}_{ts}.png")
            img.save(os.path.join(out_dir, fname))
            self._log(f"Auto-saved preview → {os.path.join(out_dir, fname)}")


    def _show(self, idx):
            if self.stack is None: return
            idx = max(0, min(idx, len(self.stack)-1))
            self.slice_index = idx
            self.tk_img = ImageTk.PhotoImage(self._render_slice_img(idx))
            self.preview.configure(image=self.tk_img)




    def _mouse(self, e):
        if self.stack is None:
            return

        # ─────────────────────────────────────────────────────────
        # 0) 포인트 HU 모드가 최우선
        # ─────────────────────────────────────────────────────────
        if self.point_mode.get():
            if e.type == tk.EventType.ButtonPress and e.num == 1:
                self.add_point(e.x, e.y)
            return

        # ─────────────────────────────────────────────────────────
        # 1) 폴리곤 ROI 모드 (좌클릭=정점 추가, 우클릭/더블클릭/Enter=닫기)
        # 필요한 상태변수: self.poly_mode (BooleanVar), self.poly_pts(list),
        #                 self.poly_closed(bool), self.poly_hover(tuple|None)
        # ─────────────────────────────────────────────────────────
        if getattr(self, "poly_mode", None) and self.poly_mode.get():
            # 안전 가드: 속성 없으면 초기화
            if not hasattr(self, "poly_pts"):    self.poly_pts = []
            if not hasattr(self, "poly_closed"): self.poly_closed = False
            if not hasattr(self, "poly_hover"):  self.poly_hover = None

            # 좌표를 512 프리뷰 경계로 클램프
            cx = max(0, min(511, int(e.x)))
            cy = max(0, min(511, int(e.y)))

            # 좌클릭: 정점 추가
            if e.type == tk.EventType.ButtonPress and e.num == 1:
                # 닫힌 상태에서 다시 클릭하면 새 폴리곤 시작
                if self.poly_closed:
                    self.poly_pts, self.poly_closed = [], False
                self.poly_pts.append((cx, cy))
                self.poly_hover = None
                self._show(self.slice_index)
                return

            # 마우스 이동: 마지막 정점 → hover 임시선
            if e.type == tk.EventType.Motion and self.poly_pts and not self.poly_closed:
                self.poly_hover = (cx, cy)
                self._show(self.slice_index)
                return

            # 우클릭 또는 더블클릭: 폴리곤 닫기
            if (e.type == tk.EventType.ButtonPress and e.num == 3) or str(e.type).endswith("Double"):
                # 별도 메서드가 있으면 사용
                if hasattr(self, "_poly_close"):
                    self._poly_close()
                else:
                    if len(self.poly_pts) >= 3:
                        self.poly_closed = True
                        xs = min(p[0] for p in self.poly_pts); ys = min(p[1] for p in self.poly_pts)
                        xe = max(p[0] for p in self.poly_pts); ye = max(p[1] for p in self.poly_pts)
                        self.roi = [xs, ys, xe - xs, ye - ys]  # 프리뷰 bbox 동기화
                        # 좌표 입력칸과 버튼 활성화 동기화
                        if hasattr(self, "_sync_roi_entries"): self._sync_roi_entries()
                        try: self.btn_save_single.config(state="normal")
                        except Exception: pass
                        if hasattr(self, "_log"):
                            self._log(f"Polygon ROI set: {len(self.poly_pts)} vertices, bbox={self.roi}")
                    self._show(self.slice_index)
                return

            # 폴리곤 모드에서는 여기서 종료 (직사각형 드래그 분기 진입 금지)
            return

        # ─────────────────────────────────────────────────────────
        # 2) 기본(직사각형) ROI 드래그 — 기존 코드 유지
        # ─────────────────────────────────────────────────────────
        x, y = e.x, e.y
        if e.type == tk.EventType.ButtonPress and e.num == 1:
            self.start_pt = (x, y)

        elif e.type == tk.EventType.Motion and self.start_pt:
            x0, y0 = self.start_pt

            # ① 고정 크기 모드
            if self.fixed_size_mode.get():
                # 단위 변환
                if self.fixed_unit.get() == "mm":
                    w = self._mm_to_preview_px(self.fixed_w.get(), axis="x")
                    h = self._mm_to_preview_px(self.fixed_h.get(), axis="y")
                else:
                    w = int(self.fixed_w.get()); h = int(self.fixed_h.get())

                # 중심 기준 or 모서리 기준
                if self.center_draw.get():
                    x_tl = int(round(x - w/2)); y_tl = int(round(y - h/2))
                else:
                    x_tl = x0; y_tl = y0
                self.roi = self._clamp_roi_preview(x_tl, y_tl, w, h)

            else:
                # ② 일반 드래그 (정사각형 잠금 지원)
                dx, dy = x - x0, y - y0
                if self.lock_square.get():
                    side = max(abs(dx), abs(dy))
                    x1 = x0 + (side if dx >= 0 else -side)
                    y1 = y0 + (side if dy >= 0 else -side)
                else:
                    x1, y1 = x, y
                x_tl, y_tl = min(x0, x1), min(y0, y1)
                w, h = abs(x1 - x0), abs(y1 - y0)
                self.roi = self._clamp_roi_preview(x_tl, y_tl, w, h)

            if hasattr(self, "_sync_roi_entries"): self._sync_roi_entries()
            self._show(self.slice_index)

        elif e.type == tk.EventType.ButtonRelease and self.start_pt:
            # 드래그 종료
            self.start_pt = None
            if self.roi and (self.roi[2] == 0 or self.roi[3] == 0):
                self.roi = []
            if hasattr(self, "_sync_roi_entries"): self._sync_roi_entries()
            self._show(self.slice_index)
            if self.roi:
                if hasattr(self, "_log"):
                    self._log(f"ROI set: {self.roi}")
                try: self.btn_save_single.config(state="normal")
                except Exception: pass



    def _set_z(self, sp="start"):
            cur = int(self.slice_index)
            if sp=="start":
                self.z_start = cur; self.sb_z0.delete(0,tk.END); self.sb_z0.insert(0,str(cur))
            else:
                self.z_end   = cur; self.sb_z1.delete(0,tk.END); self.sb_z1.insert(0,str(cur))
            self._log(f"Z {sp} = {cur}")

    def _on_key(self, e):
        if not self.roi: return
        x, y, w, h = self.roi
        step = 10 if (e.state & 0x0004) else 1   # Ctrl 가속(Windows: 0x0004)
        changed = False

        if e.keysym in ("Left","Right","Up","Down"):
            if e.keysym == "Left":  x -= step
            if e.keysym == "Right": x += step
            if e.keysym == "Up":    y -= step
            if e.keysym == "Down":  y += step
            changed = True

        elif e.char in "+=":  # 확대
            w += step; h += step; changed = True
        elif e.char in "-_":  # 축소
            w = max(1, w - step); h = max(1, h - step); changed = True

        elif e.char.lower() == "s":  # 정사각형 토글
            self.lock_square.set(not self.lock_square.get())
        elif e.char.lower() == "c":  # 중심 드로우 토글
            self.center_draw.set(not self.center_draw.get())

        if changed:
            self.roi = self._clamp_roi_preview(x, y, w, h)
            self._sync_roi_entries()
            self._show(self.slice_index)


        # ------------- ROI utils -------------
    def add_point(self, px, py):
        """
        512×512 프리뷰 좌표(px,py) → 원 해상도(ix,iy)로 역변환 후 HU 계산.
        - 원본 DICOM 16-bit 픽셀(raw gray)과 슬라이스별 slope/intercept로 HU 산출
        - 디스플레이 gray는 프리뷰(8-bit)에서 직접 추출
        """
        if self.stack is None:
            return

        # 1) 프리뷰 좌표 정규화 + 범위 체크
        px = int(round(px)); py = int(round(py))
        if px < 0 or py < 0 or px >= 512 or py >= 512:
            self._log(f"Point ignored: preview outside ({px},{py})"); 
            return

        # 2) 프리뷰→원본 좌표 변환(ROI와 동일 경로 재사용)
        ix, iy = self._preview_pt_to_native(px, py)

        # 3) 원본 DICOM 메타/픽셀에서 값 취득
        ds  = self.slices[self.slice_index]
        raw = ds.pixel_array  # 원본 16-bit (signed/unsigned)
        H, W = raw.shape
        if ix < 0 or iy < 0 or ix >= W or iy >= H:
            self._log(f"Point ignored: mapped outside native ({ix},{iy})")
            return

        # 4) 실제 HU 계산(슬라이스별 slope/intercept)
        slope_used, inter_used = self._rescale_params(ds)
        gval = int(raw[iy, ix])                    # 원본 gray(16-bit)
        hu   = float(gval) * slope_used + inter_used

        # 5) 디스플레이 gray(화면 8-bit) 추출
        disp = self._display_gray_from_preview(px, py)

        # 6) 메모리에 저장(필요 시 export_*에서 CSV로 기록)
        self.point_idx += 1
        self.points.append(dict(
            idx=self.point_idx, slice=self.slice_index,
            x=ix, y=iy,
            gray=gval,             # 원본 16-bit 픽셀값
            hu=hu,                 # 원본 메타 기준 HU
            disp=disp,             # 화면 8-bit gray
            slope=slope_used, intercept=inter_used
        ))

        # 7) 로그 + 화면 갱신
        if disp != "":
            self._log(f"Point {self.point_idx}: slice {self.slice_index}, (x={ix}, y={iy}) "
                    f"HU={hu:.2f}, Gray={gval}, Disp={disp}")
        else:
            self._log(f"Point {self.point_idx}: slice {self.slice_index}, (x={ix}, y={iy}) "
                    f"HU={hu:.2f}, Gray={gval}")

        self._show(self.slice_index)





    def save_point_measurement(self, idx, slc, x, y, hu):
            """
            포인트 HU를 CSV + 프리뷰 PNG로 저장.
            CSV 헤더에 환자/UID/행열/픽셀간격/IOP/IPP 등 메타 포함.
            """
            out_dir = self._measure_root("Point")
            os.makedirs(out_dir, exist_ok=True)

            ds = self.slices[slc]
            meta = self._slice_meta(ds)
            csv_path = os.path.join(out_dir, "points.csv")
            new_file = not os.path.exists(csv_path)

            # CSV 스키마: gray(원본), hu(원본 메타 기준), disp(화면), slope/intercept(슬라이스별)
            base_cols = ["idx","slice","x","y","gray","hu","disp","slope","intercept","timestamp"]
            meta_cols = list(meta.keys())

            with open(csv_path, "a", encoding="utf-8") as f:
                if new_file:
                    f.write(",".join(base_cols + meta_cols) + "\n")
                # 원본 픽셀/슬라이스 메타로 재계산
                ds  = self.slices[slc]
                raw = ds.pixel_array
                gval = int(raw[y, x])
                slope_used, inter_used = self._rescale_params(ds)
                hu_val = float(gval) * slope_used + inter_used
                # 화면 그레이는 프리뷰에서 읽기(원본→프리뷰 좌표 변환)
                W, H = int(self.stack.shape[2]), int(self.stack.shape[1])
                px = int(round(x * 512.0 / max(W,1))); py = int(round(y * 512.0 / max(H,1)))
                disp = self._display_gray_from_preview(px, py)
                row_vals = [
                    str(idx), str(slc), str(x), str(y),
                    str(gval), f"{hu_val:.6f}", str(disp),
                    f"{slope_used}", f"{inter_used}",
                    datetime.now().isoformat()
                ] + [str(meta[k]) for k in meta_cols]
                f.write(",".join(row_vals) + "\n")

            # 프리뷰 PNG(512 오버레이)
            img = self._render_slice_img(slc, overlay=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            img.save(os.path.join(out_dir, f"slice_{slc:04}_HU_points_{ts}.png"))

    def _toggle_poly_mode(self):
        if self.poly_mode.get():
            # 폴리곤 시작 → 직사각형 ROI 혼동 방지
            self.roi = []
            self.poly_pts, self.poly_hover, self.poly_closed = [], None, False
            self._log("Polygon ROI: click to add vertices; right-click / double-click / Enter to close.")
        else:
            self.poly_pts, self.poly_hover, self.poly_closed = [], None, False
        self._show(self.slice_index)

    def _poly_close(self):
        if len(self.poly_pts) >= 3:
            self.poly_closed = True
            # 라벨 표시에 쓰는 bounding box를 self.roi로 동기화(프리뷰 좌표계)
            xs = min(p[0] for p in self.poly_pts); ys = min(p[1] for p in self.poly_pts)
            xe = max(p[0] for p in self.poly_pts); ye = max(p[1] for p in self.poly_pts)
            self.roi = [xs, ys, xe - xs, ye - ys]
            self._show(self.slice_index)

    def _poly_undo(self):
        if self.poly_closed:
            self.poly_closed = False
        if self.poly_pts:
            self.poly_pts.pop()
        self._show(self.slice_index)

    def _poly_clear(self):
        self.poly_pts, self.poly_hover, self.poly_closed = [], None, False
        self._show(self.slice_index)


    def measure_roi_hu(self):
        """
        현재 ROI의 HU/Gray 통계 계산.
        - 폴리곤 ROI가 닫혀 있으면 폴리곤을 우선 처리하고 return
        - 사각형 ROI는 self.roi(프리뷰 좌표) → 네이티브로 변환해 처리
        - 결과는 self.last_roi_stats 에만 저장(Export에서 CSV 기록)
        """
        import numpy as np
        if self.stack is None:
            messagebox.showerror("Error", "No stack")
            return

        # ── 슬라이스별 원본 메타 사용 ─────────────────────────────
        ds = self.slices[self.slice_index]
        slope_used, inter_used = self._rescale_params(ds)

        # ── 1) Polygon ROI 우선 처리 ─────────────────────────────
        if self.poly_mode.get() and self.poly_closed and len(self.poly_pts) >= 3:
            # 네이티브 공간에서 선택
            raw_native = ds.pixel_array.astype(np.float32)
            xs, ys, xe, ye = self._poly_bbox_native()
            if xe <= xs or ye <= ys:
                self._log("Polygon ROI bbox invalid"); return
            mask = self._poly_mask_native_local(xs, ys, xe, ye)
            roi  = raw_native[ys:ye, xs:xe]
            sel  = roi[mask]
            if sel.size == 0:
                self._log("Polygon ROI: empty selection"); return

            # HU/Gray 통계
            hu   = sel * slope_used + inter_used
            mean = float(hu.mean()); std = float(hu.std())
            mn   = float(hu.min());  mx  = float(hu.max())
            gmean = float(sel.mean()); gstd = float(sel.std())
            gmin  = float(sel.min());  gmax = float(sel.max())

            # 디스플레이(프리뷰 8-bit) 통계: 폴리곤 마스크를 프리뷰에 생성
            disp_mean = disp_std = disp_min = disp_max = ""
            try:
                if self._last_preview_img is None:
                    _ = self._render_slice_img(self.slice_index, overlay=False)
                if self._last_preview_img is not None and len(self.poly_pts) >= 3:
                    # 프리뷰 좌표의 폴리곤 bbox로 자르고, 로컬 좌표로 마스크 생성
                    import PIL.Image, PIL.ImageDraw
                    pxs = [p[0] for p in self.poly_pts]; pys = [p[1] for p in self.poly_pts]
                    x0, y0 = int(min(pxs)), int(min(pys))
                    x1, y1 = int(max(pxs)), int(max(pys))
                    x0 = max(0, min(x0, 511)); y0 = max(0, min(y0, 511))
                    x1 = max(1, min(x1, 512)); y1 = max(1, min(y1, 512))
                    if x1 > x0 and y1 > y0:
                        crop = self._last_preview_img.crop((x0, y0, x1, y1)).convert("L")
                        w_p, h_p = crop.size
                        # 로컬 좌표로 이동한 폴리곤
                        poly_local = [(int(px - x0), int(py - y0)) for (px, py) in self.poly_pts]
                        # 마스크 만들기
                        mimg = PIL.Image.new("L", (w_p, h_p), 0)
                        draw = PIL.ImageDraw.Draw(mimg)
                        draw.polygon(poly_local, fill=1, outline=1)
                        arr_d = np.asarray(crop, dtype=np.uint8)
                        msk   = np.asarray(mimg, dtype=np.uint8).astype(bool)
                        if msk.any():
                            vals = arr_d[msk]
                            disp_mean = float(vals.mean()); disp_std = float(vals.std())
                            disp_min  = int(vals.min());   disp_max = int(vals.max())
            except Exception:
                pass

            self.last_roi_stats = dict(
                slice=self.slice_index,
                xs=int(xs), ys=int(ys), ws=int(xe-xs), hs=int(ye-ys),
                mean=mean, std=std, min=mn, max=mx,
                gray_mean=gmean, gray_std=gstd, gray_min=gmin, gray_max=gmax,
                disp_mean=disp_mean, disp_std=disp_std, disp_min=disp_min, disp_max=disp_max,
                slope=slope_used, intercept=inter_used,
                poly=True, n_vertices=len(self.poly_pts)
            )
            self._log(f"[Poly] HU {mean:.2f}±{std:.2f} (min {mn:.0f}, max {mx:.0f})")
            self._show(self.slice_index)
            return

        # ── 2) Rectangle ROI 처리 ────────────────────────────────
        if not self.roi:
            messagebox.showerror("Error", "ROI not set")
            return

        xs, ys, ws, hs = self._roi_xywh_native()
        if ws <= 0 or hs <= 0:
            self._log("ROI HU: zero-size mapping; skip."); return

        raw = ds.pixel_array
        H, W = raw.shape
        xs = max(0, min(xs, W - 1)); ys = max(0, min(ys, H - 1))
        xe = max(xs + 1, min(xs + ws, W)); ye = max(ys + 1, min(ys + hs, H))
        roi = raw[ys:ye, xs:xe]
                    # Gray 0–255 경고 보조(실제 비트와 값 범위가 어긋나면 알림)
                    # Gray 값이 0–255에 갇힌 경우 경고 보조
            # Gray 값이 0–255에 갇힌 경우 경고 보조
        try:
            rmin, rmax = int(roi.min()), int(roi.max())
            bits_stored = int(getattr(ds, "BitsStored", getattr(ds, "BitsAllocated", 16)))
            if rmax <= 255 and bits_stored > 8:
                self._log(f"[WARN] ROI gray range [{rmin},{rmax}] with BitsStored={bits_stored} → likely derived 8-bit content.")
        except Exception:
            pass

        try:
            bits_stored = int(getattr(ds, "BitsStored", getattr(ds, "BitsAllocated", 16)))
            rmin = int(roi.min()); rmax = int(roi.max())
            if rmax <= 255 and bits_stored > 8:
                self._log(f"[WARN] Gray range <=255 but BitsStored={bits_stored}. Check if this is a derived/secondary 8-bit series.")
        except Exception:
            pass

        if roi.size == 0:
            self._log("ROI HU: empty after bounds clamp; skip."); return

        hu = roi.astype("float32") * slope_used + inter_used
        mean = float(hu.mean()); std = float(hu.std())
        mn = float(hu.min());   mx = float(hu.max())
        gmean = float(roi.mean()); gstd = float(roi.std())
        gmin  = float(roi.min());  gmax = float(roi.max())

        # 디스플레이(프리뷰 8-bit) 통계(사각형)
        disp_mean = disp_std = disp_min = disp_max = ""
        try:
            if self._last_preview_img is None:
                _ = self._render_slice_img(self.slice_index, overlay=False)
            if self._last_preview_img is not None:
                x_p, y_p, w_p, h_p = self.roi
                x_p = max(0, min(int(x_p), 511)); y_p = max(0, min(int(y_p), 511))
                w_p = max(1, int(w_p)); h_p = max(1, int(h_p))
                xe_p = max(1, min(x_p + w_p, 512)); ye_p = max(1, min(y_p + h_p, 512))
                crop = self._last_preview_img.crop((x_p, y_p, xe_p, ye_p)).convert("L")
                arr_d = np.asarray(crop, dtype=np.uint8)
                disp_mean = float(arr_d.mean()); disp_std = float(arr_d.std())
                disp_min  = int(arr_d.min());   disp_max = int(arr_d.max())
        except Exception:
            pass

        self._log(f"HU mean {mean:.2f} ± {std:.2f} (min {mn:.0f}, max {mx:.0f})")
        self.last_roi_stats = dict(
            slice=self.slice_index,
            xs=int(xs), ys=int(ys), ws=int(xe-xs), hs=int(ye-ys),
            mean=mean, std=std, min=mn, max=mx,
            gray_mean=gmean, gray_std=gstd, gray_min=gmin, gray_max=gmax,
            disp_mean=disp_mean, disp_std=disp_std, disp_min=disp_min, disp_max=disp_max,
            slope=slope_used, intercept=inter_used,
            poly=False
        )
        self._show(self.slice_index)




    def _roi_xywh_native(self):
            """512 프리뷰 좌표 → 원 해상도 좌표로 변환한 (xs,ys,ws,hs)"""
            x,y,w,h = self.roi
            fx = self.stack.shape[2] / 512.0
            fy = self.stack.shape[1] / 512.0
            xs, ys = int(round(x*fx)), int(round(y*fy))
            ws, hs = int(round(w*fx)), int(round(h*fy))
            ws = max(1, min(ws, self.stack.shape[2]-xs))
            hs = max(1, min(hs, self.stack.shape[1]-ys))
            return xs, ys, ws, hs

    # --- ROI/단위 변환 유틸 ---
    def _W(self): return int(self.stack.shape[2]) if self.stack is not None else 512
    def _H(self): return int(self.stack.shape[1]) if self.stack is not None else 512

    def _mm_to_preview_px(self, mm, axis="x"):
        """mm → 원해상도 px → 512 프리뷰 px"""
        W, H = self._W(), self._H()
        sx = float(self.e_sx.get() or 1.0)
        sy = float(self.e_sy.get() or 1.0)
        if axis == "x":
            px_native = max(1, int(round(mm / max(sx, 1e-9))))
            return int(round(px_native * 512.0 / max(W,1)))
        else:
            px_native = max(1, int(round(mm / max(sy, 1e-9))))
            return int(round(px_native * 512.0 / max(H,1)))

    def _clamp_roi_preview(self, x, y, w, h):
        """512 프리뷰 좌표계에서 화면 경계로 클램프"""
        x = max(0, min(int(x), 511))
        y = max(0, min(int(y), 511))
        w = max(0, min(int(w), 512 - x))
        h = max(0, min(int(h), 512 - y))
        return [x, y, w, h]

    def _sync_roi_entries(self):
        """self.roi → (roi_x, roi_y, roi_w, roi_h) 동기화"""
        if not self.roi: return
        x, y, w, h = self.roi
        self.roi_x.set(int(x)); self.roi_y.set(int(y))
        self.roi_w.set(int(w)); self.roi_h.set(int(h))

    def _apply_numeric_roi(self):
        """숫자 입력 → self.roi 세팅(프리뷰 좌표계)"""
        x = self.roi_x.get(); y = self.roi_y.get(); w = self.roi_w.get(); h = self.roi_h.get()
        self.roi = self._clamp_roi_preview(x, y, w, h)
        self._show(self.slice_index)

    def _apply_mm_roi(self):
        """고정 단위를 mm로 입력한 값을 현재 입력창에 적용(프리뷰 좌표계)"""
        W, H = self._W(), self._H()
        w_px = self._mm_to_preview_px(self.fixed_w.get(), axis="x") if self.fixed_unit.get()=="mm" else int(self.fixed_w.get())
        h_px = self._mm_to_preview_px(self.fixed_h.get(), axis="y") if self.fixed_unit.get()=="mm" else int(self.fixed_h.get())
        # 현재 ROI의 좌상단 유지
        x = int(self.roi_x.get() if self.roi else 0)
        y = int(self.roi_y.get() if self.roi else 0)
        self.roi = self._clamp_roi_preview(x, y, w_px, h_px)
        self._sync_roi_entries()
        self._show(self.slice_index)


    def _z_range(self):
            try:
                z0 = int(self.sb_z0.get()); z1 = int(self.sb_z1.get())
            except ValueError:
                raise
            if z0 > z1: z0, z1 = z1, z0
            idxs = list(range(z0, z1+1))
            if self.invert_z.get(): idxs = idxs[::-1]
            return idxs

        # ------------- Save (single) -------------

    def _hu_to_uint8(self, hu_arr, wl=None, ww=None, p_lo=1.0, p_hi=99.0):
            """
            HU 배열을 8-bit로 윈도잉. wl/ww가 없으면 ROI 내 분위(기본 1~99%)로 자동 설정.
            """
            import numpy as np
            hu = np.asarray(hu_arr, dtype=np.float32)
            if wl is None or ww is None or ww <= 0:
                lo = np.percentile(hu, p_lo); hi = np.percentile(hu, p_hi)
                wl = float((lo + hi) * 0.5)
                ww = float(max(hi - lo, 1.0))
            lo = wl - ww/2.0; hi = wl + ww/2.0
            hu = np.clip(hu, lo, hi)
            out = (hu - lo) * (255.0 / max(hi - lo, 1.0))
            return out.astype("uint8"), wl, ww


    def _measure_root(self, kind=""):
            """
            HU 산출물 저장 루트.
            1) last_save_dir(최근 저장 폴더) → 2) dicom_dir → 3) 사용자가 선택 → 4) CWD
            kind: "", "Point", "ROI" 등
            """
            base = getattr(self, "last_save_dir", "") or getattr(self, "dicom_dir", "")
            if not base:
                try:
                    base = filedialog.askdirectory(title="Choose output folder for HU results")
                except Exception:
                    base = ""
            if not base:
                base = os.getcwd()
            # HU 네이밍 폴더(루트 아래 고정)
            sub = f"HU_{kind}" if kind else "HU"
            path = os.path.join(base, sub)
            os.makedirs(path, exist_ok=True)
            return path



    def _save_roi_measurement(self, m: dict):
            """
            ROI_HU 폴더 → CSV + 프리뷰 오버레이 + 원해상도 ROI 크롭(8/16)
            """
            out_dir = self._measure_root("ROI")
            os.makedirs(out_dir, exist_ok=True)

            # --- CSV: 헤더 확장 ---
            slope_used = float(getattr(ds, "RescaleSlope",  getattr(self, "slope", 1.0)))
            inter_used = float(getattr(ds, "RescaleIntercept", getattr(self, "inter", 0.0)))

            slc = int(m["slice"])
            ds  = self.slices[slc]
            meta = self._slice_meta(ds)
            px  = getattr(ds, "PixelSpacing", None)
            sx  = float(px[0]) if (px is not None and len(px) > 0) else None
            sy  = float(px[1]) if (px is not None and len(px) > 1) else None
            ws_mm = (m["ws"] * sx) if sx else ""
            hs_mm = (m["hs"] * sy) if sy else ""

            csv_path = os.path.join(out_dir, "roi_stats.csv")
            new_file = not os.path.exists(csv_path)
            base_cols = ["slice","xs","ys","ws","hs","ws_mm","hs_mm",
                 "mean","std","min","max",
                 "gray_mean","gray_std","gray_min","gray_max",
                 "disp_mean","disp_std","disp_min","disp_max",
                 "slope","intercept","timestamp"]
            meta_cols = list(meta.keys())

            with open(csv_path, "a", encoding="utf-8") as f:
                if new_file:
                    f.write(",".join(base_cols + meta_cols) + "\n")
                row_vals = [
                    str(m["slice"]), str(m["xs"]), str(m["ys"]),
                    str(m["ws"]), str(m["hs"]),
                    (f"{ws_mm:.6f}" if ws_mm != "" else ""), (f"{hs_mm:.6f}" if hs_mm != "" else ""),
                    f"{m['mean']:.6f}", f"{m['std']:.6f}", f"{m['min']:.6f}", f"{m['max']:.6f}",
                    f"{m.get('gray_mean',''):.6f}", f"{m.get('gray_std',''):.6f}",
                    f"{m.get('gray_min',''):.6f}",  f"{m.get('gray_max',''):.6f}",
                    (f"{m.get('disp_mean',''):.6f}" if isinstance(m.get('disp_mean',''), float) else ""),
                    (f"{m.get('disp_std',''):.6f}"  if isinstance(m.get('disp_std',''),  float) else ""),
                    (str(m.get('disp_min','')) if m.get('disp_min','') != "" else ""),
                    (str(m.get('disp_max','')) if m.get('disp_max','') != "" else ""),
                    str(m.get("slope", slope_used)), str(m.get("intercept", inter_used)),
                    datetime.now().isoformat()
                ] + [str(meta[k]) for k in meta_cols]
                f.write(",".join(row_vals) + "\n")

            # 프리뷰(512) 오버레이
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            img = self._render_slice_img(m["slice"], overlay=True)
            img.save(os.path.join(out_dir, f"slice_{m['slice']:04}_HU_roi_{ts}.png"))

            # 원해상도 ROI 크롭(8bit/16bit)
            raw = self.slices[m["slice"]].pixel_array
            xs, ys, xe, ye = m["xs"], m["ys"], m["xs"]+m["ws"], m["ys"]+m["hs"]
            crop_raw = raw[ys:ye, xs:xe].copy()
            crop_hu  = crop_raw.astype("float32") * self.slope + self.inter

            crop8, _, _ = self._hu_to_uint8(crop_hu)
            Image.fromarray(crop8, mode="L").save(os.path.join(out_dir, f"slice_{m['slice']:04}_HU_roi_crop8_{ts}.png"))
            Image.fromarray(crop_raw.astype(np.uint16)).save(os.path.join(out_dir, f"slice_{m['slice']:04}_HU_roi_crop16_{ts}.png"))



    def save_point_measurement(self, idx, slc, x, y, hu):
            """
            포인트 HU → CSV + 프리뷰 PNG (512 오버레이). 저장 위치: <last_save_dir>/HU_Point/
            """
            out_dir = self._measure_root("Point")
            os.makedirs(out_dir, exist_ok=True)

            # CSV
            csv_path = os.path.join(out_dir, "points.csv")
            new_file = not os.path.exists(csv_path)
            with open(csv_path, "a", encoding="utf-8") as f:
                if new_file:
                    f.write("idx,slice,x,y,hu,slope,intercept,timestamp\n")
                gval = int(self.slices[slc].pixel_array[y, x])
                f.write(f"{idx},{slc},{x},{y},{gval},{hu:.6f},{self.slope},{self.inter},{datetime.now().isoformat()}\n")

            # 프리뷰 PNG 파일명에 HU 포함
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            img = self._render_slice_img(slc, overlay=True)
            img.save(os.path.join(out_dir, f"slice_{slc:04}_HU_points_{ts}.png"))


    def save_single(self):
            if self.stack is None or not self.slices:
                messagebox.showerror("Error", "No DICOM loaded"); return
            if not self.roi:
                H, W = self.stack.shape[1], self.stack.shape[2]
                self.roi = [0, 0, W, H]
                self.used_full_image = True
                self._log(f"ROI not set → use full image ({W}x{H})")

            out_root = filedialog.askdirectory(title="Choose output folder")
            if not out_root:
                return

            # ---- 파라미터 동기화 ----
            self.sx = float(self.e_sx.get()); self.sy = float(self.e_sy.get()); self.th = float(self.e_th.get())
            self.slope = float(self.e_k.get()); self.inter = float(self.e_b.get())

            xs, ys, ws, hs = self._roi_xywh_native()
                        # ---- XY 범위 계산 ----
            if self.poly_mode.get() and self.poly_closed and len(self.poly_pts) >= 3:
                x0, y0, x1, y1 = self._poly_bbox_native()
                xs, ys, ws, hs = x0, y0, int(x1 - x0), int(y1 - y0)
            else:
                xs, ys, ws, hs = self._roi_xywh_native()

            zidx = self._z_range()

            # ---- Z 간격: IPP 기반 우선 ----
            ipp_gap, gap_std = _infer_slice_spacing_from_ipp([self.slices[i] for i in zidx]) if len(zidx) > 1 else (None, None)
            if ipp_gap:
                sbz = float(ipp_gap)
                self._log(f"Using IPP-based SpacingBetweenSlices = {sbz:.6f} (std {gap_std:.6f})")
            else:
                sbz = float(self.th)
                self._log("IPP gap unavailable → SpacingBetweenSlices = SliceThickness")

            # ---- 출력 루트(영문 고정) ----
            ds_name = os.path.basename(os.path.dirname(self.slices[0].filename)) if hasattr(self.slices[0], 'filename') else "Dataset"
            prefix  = "NRRD" if self.out_kind.get() == "NRRD" else "DICOM"
            base = make_ascii_subdir(out_root, ds_name, prefix=prefix)
            self.last_save_dir = base

            # ---- ROI 3D crop (Z,Y,X) ----
            vol, zidx_eff, z_stride = _build_crop_volume(self, zidx, ys, xs, hs, ws)
                        # ---- Polygon 마스크 적용(있을 때) ----
            if self.poly_mode.get() and self.poly_closed and len(self.poly_pts) >= 3:
                mask = self._poly_mask_native_local(xs, ys, xs+ws, ys+hs)   # (hs, ws) bool
                # 각 Z 슬라이스에 마스크 적용(바깥=0)
                for zi in range(vol.shape[0]):
                    sl = vol[zi]
                    sl[~mask] = 0

            zidx = zidx_eff
            src_shape   = vol.shape
            src_spacing = (self.sx, self.sy, sbz)

            # ---- 방향 코사인(있으면) ----
            direction = None
            try:
                iop = _safe_float_list(self.slices[zidx[0]].ImageOrientationPatient, 6)
                row, col, normal = _row_col_normal_from_iop(iop)
                direction = np.stack([row, col, normal], axis=1)   # 3x3
            except Exception:
                pass

            # ---- 리샘플 (ISO 3D 우선, 아니면 XY만) ----
            method_tag = "none"
            sx2, sy2, sz2 = self.sx, self.sy, sbz
            if getattr(self, "iso_on", None) and self.iso_on.get():
                iso  = float(self.iso_mm.get())
                meth = self.iso_method.get().lower()
                vol, (sx2, sy2, sz2) = self._resample_iso_3d(vol, (self.sx, self.sy, sbz), iso_mm=iso, method=meth, direction=direction)
                method_tag = ("SITK-" if 'HAS_SITK' in globals() and HAS_SITK else "NP-") + meth
            elif getattr(self, "xy_resample", None) and self.xy_resample.get():
                target_w = int(self.xy_target.get()); meth = self.xy_method.get()
                vol, (sx2, sy2) = self._resample_xy_to(vol, self.sx, self.sy, target_w=target_w, method=meth)
                sz2 = sbz
                method_tag = ("XY-" + meth)

            dst_shape   = vol.shape
            dst_spacing = (sx2, sy2, sz2)

            # ---- NRRD 저장 ----
            if self.out_kind.get() == "NRRD":
                # origin: 첫 슬라이스 IPP에 (xs,ys)만큼의 물리 이동을 sx2,sy2로 반영
                ds0 = self.slices[zidx[0]]
                origin = [float(v) for v in _phys_shift_ipp(ds0, xs, ys, sx2, sy2)]
                mode = "nrrd" if self.nrrd_mode.get() == "nrrd" else "nhdr+raw.gz"
                outpath = _save_nrrd(vol, (sx2, sy2, sz2), origin, direction, base, "roi", mode=mode)
                self._log(f"NRRD saved → {outpath}")
                # 감사 로그
                self._resample_audit_log(base, "single", "roi", src_shape, src_spacing, dst_shape, dst_spacing, method_tag,
                                        note=f"z[{zidx[0]}..{zidx[-1]}]")
                messagebox.showinfo("Done", f"Saved NRRD to {outpath}")
                return

            # ---- DICOM 저장 (슬라이스별) ----
            saved = 0
            # ISO인 경우 슬라이스 개수/간격이 변했을 수 있음 → 새 Z축 기준으로 IPP 재산출
            base_ipp = _phys_shift_ipp(self.slices[zidx[0]], xs, ys, sx2, sy2)
            if direction is not None:
                n = direction[:, 2].tolist()
            else:
                try:
                    iop = _safe_float_list(self.slices[zidx[0]].ImageOrientationPatient, 6)
                    _, _, n = _row_col_normal_from_iop(iop)
                except Exception:
                    n = [0.0, 0.0, 1.0]

            for new_i in range(dst_shape[0]):
                src = self.slices[zidx[min(new_i, len(zidx)-1)]]  # ISO 후엔 길이가 달라질 수 있어 방어
                crop = vol[new_i]  # (H',W')

                ds = src.copy()
                ds.Rows, ds.Columns = crop.shape
                ds.PixelData = crop.tobytes()
                ds.BitsAllocated = src.BitsAllocated
                ds.BitsStored    = src.BitsStored
                ds.HighBit       = src.HighBit
                ds.PixelRepresentation = src.PixelRepresentation
                ds.PhotometricInterpretation = src.PhotometricInterpretation
                ds.SamplesPerPixel = getattr(src, "SamplesPerPixel", 1)

                # Spacing/Thickness
                ds.PixelSpacing = [_fmt(sx2), _fmt(sy2)]
                ds.SliceThickness = _fmt(self.th)
                if dst_shape[0] > 1:
                    ds.SpacingBetweenSlices = _fmt(sz2)  # 뷰어는 보통 IOP/IPP로 재구성하지만, 참고 표기로 기록

                # IOP/IPP
                if direction is not None:
                    ds.ImageOrientationPatient = [DS(str(v)) for v in (direction[:,0].tolist() + direction[:,1].tolist())]
                # 새 슬라이스 k의 IPP = base_ipp + k*sz2*normal
                ipp_k = _ipp_for_iso_slice(base_ipp, n, sz2, new_i)
                ds.ImagePositionPatient = [DS(str(v)) for v in ipp_k]

                # HU 파라미터
                ds.RescaleSlope     = str(self.slope)
                ds.RescaleIntercept = str(self.inter)

                # UID/메타 정합
                ds.SOPInstanceUID = ensure_valid_uid(getattr(src, "SOPInstanceUID", ""))
                if hasattr(ds, "file_meta"):
                    ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID

                ds.InstanceNumber = int(new_i + 1)

                fname = ascii_sanitize(f"slice_{new_i+1:04}") + ".dcm"
                out_path = os.path.join(base, fname)
                if self.ts_jpegls.get():
                    ok = _save_dicom_jpegls(ds, out_path)
                    if not ok:
                        self._log("GDCM not available or JPEG-LS encode failed → saved uncompressed instead")
                        _save_dicom_uncompressed(ds, out_path)
                else:
                    _save_dicom_uncompressed(ds, out_path)
                saved += 1

            self._resample_audit_log(base, "single", "roi", src_shape, src_spacing, dst_shape, dst_spacing, method_tag,
                                    note=f"DICOM slices={saved}")
            self._log(f"Saved {saved} DICOM slices to {base}")
            messagebox.showinfo("Done", f"Saved {saved} DICOM slices.\n{base}")

    # [ADD] ───────────────────────────────────────────────────────────────────
    def _next_outpath_nrrd(self, base_name):
        return self.out_dir / (base_name + self.output_suffix + ".nrrd")

    def save_nrrd_3d_cropped(self, ds_list, vol_array, crop_dx_px:int, crop_dy_px:int,
                            use_slice_thickness=True, space='right-anterior-superior',
                            base_name=None, axis_order=None):
        import numpy as np
        assert nrrd is not None, "pynrrd가 필요합니다."
        assert isinstance(vol_array, np.ndarray) and vol_array.ndim == 3, "3D 배열 필요"
        assert ds_list and len(ds_list) >= 1, "ds_list가 비었습니다."  # NEW

        ref = ds_list[0]
        sx, sy = map(float, ref.PixelSpacing)
        r_dir, c_dir = self._iop_row_col(ref)

        # origin = IPP + (r*dy*sy) + (c*dx*sx)
        ipp0 = self._ipp_get(ref)
        origin = (ipp0 + r_dir * (crop_dy_px * sy) + c_dir * (crop_dx_px * sx)).tolist()

        # z방향·간격
        s_dir_ipp, dz_mean, dz_std = self._estimate_slice_axis_from_ipp(ds_list)

        # 기본은 SliceThickness 신뢰, 없거나 0이면 IPP 평균 사용  # NEW (0 보호)
        use_st = bool(use_slice_thickness and (self.orig_sz is not None) and (float(self.orig_sz) > 0))
        if use_st:
            sz_used = float(self.orig_sz)
            if dz_mean is not None and abs(sz_used - dz_mean) > float(self.z_eps_mm.get()):
                try:
                    self.LOG.warning(
                        f"z-spacing Δ>eps: ST={sz_used:.6f}, IPP={dz_mean:.6f}, eps={self.z_eps_mm.get():.3f}"
                    )
                except Exception:
                    pass
            s_dir = s_dir_ipp
        else:
            # SliceThickness가 없거나 0이면 IPP 평균을 사용(최소 양수 보정)  # NEW
            if dz_mean is None or dz_mean <= 0:
                raise ValueError("유효한 z-spacing을 추정할 수 없습니다. SliceThickness 또는 IPP 기반 간격 확인 필요.")
            sz_used = float(dz_mean)
            s_dir = s_dir_ipp

        # 축 순서 결정: 기본 'YXZ' (요청 사항), 미지정 시 자동 추정
        if axis_order is None:
            axis_order = self._infer_axis_order_from_data(vol_array, ds_list)
        axis_order = axis_order.upper()
        if axis_order not in ("ZYX","YZX","XYZ","YXZ","XZY","ZXY"):
            axis_order = "YXZ"  # 기본

        # NEW: 데이터 Z축 길이와 ds_list 슬라이스 수 일치 검증 (YXZ 기준 Z는 마지막 축)
        #      자동추정이나 다른 axis_order인 경우에도 해당 축의 길이를 검증
        axis_to_idx = {"X": axis_order.index("X"),
                    "Y": axis_order.index("Y"),
                    "Z": axis_order.index("Z")}
        z_len_data = vol_array.shape[axis_to_idx["Z"]]
        if len(ds_list) != z_len_data:
            # 여기서 바로 실패시키면 디버깅이 쉬움. 필요하면 warning으로 완화 가능.
            raise ValueError(
                f"데이터 Z 길이({z_len_data})와 ds_list 길이({len(ds_list)}) 불일치. "
                f"axis_order='{axis_order}', vol.shape={vol_array.shape}"
            )

        # 각 축 벡터 (길이=간격, 방향=코사인)
        vec_Z = (s_dir * sz_used).tolist()  # 슬라이스 축
        vec_Y = (r_dir * sy).tolist()       # row
        vec_X = (c_dir * sx).tolist()       # col

        # NEW: 직교성 점검(선택적 경고)
        try:
            def _dot(a,b): return float(np.dot(np.array(a,float)/ (np.linalg.norm(a)+1e-12),
                                            np.array(b,float)/ (np.linalg.norm(b)+1e-12)))
            dZY = abs(_dot(vec_Z, vec_Y))
            dZX = abs(_dot(vec_Z, vec_X))
            dYX = abs(_dot(vec_Y, vec_X))
            if max(dZY, dZX, dYX) > 1e-3:
                self.LOG.warning(f"space_directions 직교성 약함: dotZY={dZY:.4f}, dotZX={dZX:.4f}, dotYX={dYX:.4f}")
        except Exception:
            pass

        mapping = {"Z": vec_Z, "Y": vec_Y, "X": vec_X}
        space_directions = [mapping[ch] for ch in axis_order]

        header = {
            'space': space,
            'space directions': space_directions,
            'space origin': origin,
            'encoding': 'gzip'
        }

        # NEW: kinds 힌트(선택) — 일부 툴에서 플레인 축 해석을 돕는다
        # header['kinds'] = ['domain','domain','domain']

        base = base_name or self._derive_base_name("output")
        outpath = self._next_outpath_nrrd(base)
        nrrd.write(outpath, vol_array, header)

        # 감사 로그(항상 기록)
        try:
            self.audit_writer.writerow({
                "axis_order": axis_order,
                "dz_mean_ipp": dz_mean,
                "dz_std_ipp": dz_std,
                "sx": sx, "sy": sy, "sz_used": sz_used,
            })
        except Exception:
            pass

        return outpath, header, sz_used, dz_mean, dz_std





        # ------------- Batch -------------
    # [ADD] ───────────────────────────────────────────────────────────────────
    # [REPLACE] — DICOM+NRRD 동시 저장 (Y,X,Z)
    def save_cropped_both(self,
                        ds_template,
                        slice_pixel_arrays,
                        crop_dx_px,
                        crop_dy_px,
                        in_path,
                        zsel=None,
                        use_slice_thickness=True):
        """
        ds_template        : 기준 DICOM 메타
        slice_pixel_arrays : 3D (Y, X, Z) 또는 2D (Y, X)
        crop_dx_px, crop_dy_px : 좌상단 오프셋(픽셀)
        in_path            : GUI에서 선택한 입력 경로(폴더/파일)
        zsel               : (선택) 실제 사용된 Z 인덱스 리스트.
                            None이면 전달 배열의 Z 전범위 사용
        use_slice_thickness: True면 z-spacing은 SliceThickness 우선(권장)
        """
        base = self._derive_base_name(in_path)

        # --- 0) Z 인덱스 확정 (정합/계측 모드에서는 stride=1이므로 전체 Z 사용) ---
        if slice_pixel_arrays.ndim == 3:
            Z = slice_pixel_arrays.shape[2]
            if zsel is None:
                zsel = list(range(Z))  # 전체 Z 사용
            else:
                # 유효 범위 보정
                zsel = [int(z) for z in zsel if 0 <= int(z) < Z]
            if not zsel:
                raise ValueError("zsel이 비어 있습니다. 유효한 Z 인덱스가 필요합니다.")
        else:
            zsel = [0]  # 2D 단일 슬라이스 취급

        # --- 1) NRRD 3D 저장: (Y,X,Z) → axis_order="YXZ"로 명시 ---
        #     ds_list 길이는 실제 사용 슬라이스 수와 동일하게
        if slice_pixel_arrays.ndim == 3:
            ds_list = [ds_template] * len(zsel)
            # 전달 배열이 이미 (Y,X,Z) Sub-volume이라면 그대로 저장
            # (만약 원본 풀스택에서 부분 선택했다면, vol을 미리 (Y,X,len(zsel))로 만들어 넘겨주세요)
            nrrd_path, nrrd_header, sz_used, dz_mean, dz_std = self.save_nrrd_3d_cropped(
                ds_list=ds_list,
                vol_array=slice_pixel_arrays,        # (Y,X,Z)
                crop_dx_px=crop_dx_px,
                crop_dy_px=crop_dy_px,
                use_slice_thickness=use_slice_thickness,
                base_name=base,
                axis_order="YXZ"
            )
        else:
            # 2D라면 NRRD는 생략(필요 시 2D NRRD도 가능)
            sz_used = float(self.orig_sz or 0.0)
            dz_mean = dz_std = None
            ds_list = [ds_template]

        # --- 2) DICOM 슬라이스 저장: 반드시 zsel 기반으로 순회 ---
        if slice_pixel_arrays.ndim == 3:
            for i, z in enumerate(zsel):
                zname = f"{base}_z{i:04d}"           # 슬라이스 번호는 zsel 순서 기준
                yx_slice = slice_pixel_arrays[:, :, z]  # (Y,X) 단면
                self.save_dicom_cropped(
                    ds_template=ds_template,
                    pixel_array=yx_slice,
                    crop_dx_px=crop_dx_px,
                    crop_dy_px=crop_dy_px,
                    base_name=zname,
                    z_spacing_used=sz_used  # → DICOM에 SpacingBetweenSlices=sz_used 기록
                )
        else:
            # 2D 단일 슬라이스
            self.save_dicom_cropped(
                ds_template=ds_template,
                pixel_array=slice_pixel_arrays,
                crop_dx_px=crop_dx_px,
                crop_dy_px=crop_dy_px,
                base_name=base,
                z_spacing_used=sz_used
            )

        # --- 3) z-spacing 일관성 점검(경고 배너/로그) ---
        self._check_z_spacing_consistency(ds_list)

        # --- 4) 감사 로그(추가 필드가 없다면 이 함수 수준에선 생략 가능) ---
        try:
            self.audit_writer.writerow({
                "axis_order": "YXZ" if slice_pixel_arrays.ndim == 3 else "N/A",
                "dz_mean_ipp": dz_mean if dz_mean is not None else "",
                "dz_std_ipp": dz_std if dz_std is not None else "",
            })
        except Exception:
            pass





    def add_roi_to_list(self):
            if not self.roi:
                messagebox.showerror("Error","ROI not set"); return
            zidx = self._z_range()
            name = f"roi_{len(self.batch)+1}"
            x,y,w,h = self.roi
            rec = dict(name=name, xywh=(x,y,w,h), z0=zidx[0], z1=zidx[-1])
            self.batch.append(rec)
            self.tree.insert("", "end", values=(name, x,y,w,h, zidx[0], zidx[-1]))
            self._log(f"Added to batch: {rec}")

    def remove_selected(self):
            for item in self.tree.selection():
                idx = self.tree.index(item)
                self.tree.delete(item)
                if 0 <= idx < len(self.batch):
                    self._log(f"Removed: {self.batch[idx]['name']}")
                    self.batch.pop(idx)

    def save_batch(self):
            if not self.batch:
                messagebox.showerror("Error", "Batch list is empty")
                return

            out_root = filedialog.askdirectory(title="Choose output folder")
            if not out_root:
                return

            # 메타 파라미터 동기화
            self.sx = float(self.e_sx.get()); self.sy = float(self.e_sy.get()); self.th = float(self.e_th.get())
            self.slope = float(self.e_k.get()); self.inter = float(self.e_b.get())

            # 루트 출력 폴더(영문 고정)
            ds_name  = os.path.basename(os.path.dirname(self.slices[0].filename)) if hasattr(self.slices[0], 'filename') else "Dataset"
            prefix   = "NRRD" if self.out_kind.get() == "NRRD" else "DICOM"
            root_out = make_ascii_subdir(out_root, ds_name, prefix=prefix)
            self.last_save_dir = root_out  # 이후 HU/프리뷰 저장 루트

            # 각 ROI 항목 처리
            for rec in self.batch:
                x, y, w, h = rec["xywh"]
                self.roi = [x, y, w, h]                  # 좌표 변환 유틸 재사용
                xs, ys, ws, hs = self._roi_xywh_native()

                z0, z1 = rec["z0"], rec["z1"]
                zidx = list(range(min(z0, z1), max(z0, z1) + 1))
                if self.invert_z.get():
                    zidx = zidx[::-1]

                # Z 간격: IPP 기반(가능하면) → 없으면 SliceThickness
                ipp_gap, gap_std = _infer_slice_spacing_from_ipp([self.slices[i] for i in zidx]) if len(zidx) > 1 else (None, None)
                sbz = float(ipp_gap) if ipp_gap else float(self.th)
                if ipp_gap:
                    self._log(f"[{rec['name']}] IPP-based Z-gap = {sbz:.6f} (std {gap_std:.6f})")
                else:
                    self._log(f"[{rec['name']}] IPP gap unavailable → use SliceThickness ({sbz:.6f})")

                # 항목별 하위 폴더
                out_base = os.path.join(root_out, ascii_sanitize(rec["name"]))
                os.makedirs(out_base, exist_ok=True)

                # --- ROI 3D crop (Z,Y,X) ---
                vol, zidx_eff, z_stride = _build_crop_volume(self, zidx, ys, xs, hs, ws)
                zidx = zidx_eff
                src_shape   = vol.shape
                src_spacing = (self.sx, self.sy, sbz)

                # 방향 코사인(있으면) → direction(3x3)
                direction = None
                try:
                    iop = _safe_float_list(self.slices[zidx[0]].ImageOrientationPatient, 6)
                    row, col, normal = _row_col_normal_from_iop(iop)
                    direction = np.stack([row, col, normal], axis=1)
                except Exception:
                    pass

                # --- 리샘플: ISO 3D 우선, 아니면 XY만 ---
                method_tag = "none"
                sx2, sy2, sz2 = self.sx, self.sy, sbz
                if getattr(self, "iso_on", None) and self.iso_on.get():
                    iso  = float(self.iso_mm.get())
                    meth = self.iso_method.get().lower()
                    vol, (sx2, sy2, sz2) = self._resample_iso_3d(vol, (self.sx, self.sy, sbz),
                                                                iso_mm=iso, method=meth, direction=direction)
                    method_tag = ("SITK-" if 'HAS_SITK' in globals() and HAS_SITK else "NP-") + meth
                elif getattr(self, "xy_resample", None) and self.xy_resample.get():
                    target_w = int(self.xy_target.get()); meth = self.xy_method.get()
                    vol, (sx2, sy2) = self._resample_xy_to(vol, self.sx, self.sy, target_w=target_w, method=meth)
                    sz2 = sbz
                    method_tag = "XY-" + meth

                dst_shape   = vol.shape
                dst_spacing = (sx2, sy2, sz2)

                # ===================== NRRD =====================
                # ===================== NRRD =====================
                if self.out_kind.get() == "NRRD":
                    ds0 = self.slices[zidx[0]]
                    origin = [float(v) for v in _phys_shift_ipp(ds0, xs, ys, sx2, sy2)]
                    mode = "nrrd" if self.nrrd_mode.get() == "nrrd" else "nhdr+raw.gz"
                    try:
                        path = _save_nrrd(vol, (sx2, sy2, sz2), origin, direction,
                                        out_base, ascii_sanitize(rec["name"]), mode=mode)
                        self._log(f"[{rec['name']}] NRRD saved → {path}")
                    except Exception as e:
                        self._log(f"[{rec['name']}] NRRD save failed: {e}")
                        raise


                # ===================== DICOM =====================
                saved = 0
                # ISO면 슬라이스 수/간격이 바뀔 수 있음 → 새 Z축 IPP 재산출
                base_ipp = _phys_shift_ipp(self.slices[zidx[0]], xs, ys, sx2, sy2)
                if direction is not None:
                    n = direction[:, 2].tolist()
                else:
                    try:
                        iop = _safe_float_list(self.slices[zidx[0]].ImageOrientationPatient, 6)
                        _, _, n = _row_col_normal_from_iop(iop)
                    except Exception:
                        n = [0.0, 0.0, 1.0]

                for new_i in range(dst_shape[0]):
                    # ISO 후 길이가 달라졌을 수 있어 인덱스 방어
                    src  = self.slices[zidx[min(new_i, len(zidx)-1)]]
                    crop = vol[new_i]  # (H', W')

                    ds = src.copy()
                    ds.Rows, ds.Columns = crop.shape
                    ds.PixelData = crop.tobytes()
                    ds.BitsAllocated = src.BitsAllocated
                    ds.BitsStored    = src.BitsStored
                    ds.HighBit       = src.HighBit
                    ds.PixelRepresentation = src.PixelRepresentation
                    ds.PhotometricInterpretation = src.PhotometricInterpretation
                    ds.SamplesPerPixel = getattr(src, "SamplesPerPixel", 1)

                    # Spacing/Thickness
                    ds.PixelSpacing = [_fmt(sx2), _fmt(sy2)]
                    ds.SliceThickness = _fmt(self.th)
                    if dst_shape[0] > 1:
                        ds.SpacingBetweenSlices = _fmt(sz2)

                    # IOP/IPP
                    if direction is not None:
                        ds.ImageOrientationPatient = [DS(str(v)) for v in (direction[:,0].tolist() + direction[:,1].tolist())]
                    ipp_k = _ipp_for_iso_slice(base_ipp, n, sz2, new_i)
                    ds.ImagePositionPatient = [DS(str(v)) for v in ipp_k]

                    # HU 파라미터
                    ds.RescaleSlope     = str(self.slope)
                    ds.RescaleIntercept = str(self.inter)

                    # UID 정합
                    ds.SOPInstanceUID = ensure_valid_uid(getattr(src, "SOPInstanceUID", ""))
                    if hasattr(ds, "file_meta"):
                        ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID

                    ds.InstanceNumber = int(new_i + 1)

                    fname = ascii_sanitize(f"{rec['name']}_{new_i+1:04}") + ".dcm"
                    out_path = os.path.join(out_base, fname)
                    if self.ts_jpegls.get():
                        ok = _save_dicom_jpegls(ds, out_path)
                        if not ok:
                            self._log(f"[{rec['name']}] JPEG-LS encode not available → uncompressed saved")
                            _save_dicom_uncompressed(ds, out_path)
                    else:
                        _save_dicom_uncompressed(ds, out_path)
                    saved += 1

                self._resample_audit_log(out_base, "batch", rec["name"],
                                        src_shape, src_spacing, dst_shape, dst_spacing, method_tag,
                                        note=f"DICOM slices={saved}")
                self._log(f"[{rec['name']}] Saved {saved} DICOM slices → {out_base}")

            messagebox.showinfo("Done", f"Batch saved to {root_out}")





def _read_info_text(self, lang: str, md_filename: str | None = None, default_text: str | None = None) -> str:
        import os
        here = os.path.dirname(os.path.abspath(__file__))

        # 1) 요청이 오면 md_filename 최우선
        candidates = []
        if md_filename:
            candidates.append(md_filename)

        # 2) 기본 우선순위(요청하신 파일명 → 과거 호환 이름 → 일반 이름)
        if lang == "ko":
            candidates += [
                "README_DICOM_ROI_Cropper_ko.md",
                "README_설명.md",
                "README_ko.md",
                "README.md",
            ]
        else:
            candidates += [
                "README_DICOM_ROI_Cropper_en.md",
                "README_EN.md",
                "README_en.md",
                "README.md",
            ]

        # 3) 실제로 존재하는 첫 번째 파일을 읽음
        for name in candidates:
            path = os.path.join(here, name)
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        return f.read()
                except Exception:
                    # 다음 후보로 넘어감
                    pass

        # 4) 모두 실패하면 기본 텍스트
        if default_text is not None:
            return default_text

        return "# 안내\n설명 파일을 찾지 못했습니다. README를 프로젝트 폴더에 배치해 주세요.\n"
# removed global _resample_iso_3d (use class method instead)
# ----------------------- main -----------------------
if __name__ == "__main__":
    root = tk.Tk()
    app  = DICOMRoiBatchApp(root)
    root.mainloop()


# =========================
# Assistant Appended Fixups
# =========================

def _infer_slice_spacing_from_ipp(slices):
    """
    Infer average SpacingBetweenSlices from ImagePositionPatient (0020,0032)
    and Image Orientation (0020,0037) when available.
    Returns (mean_gap_mm, std_gap_mm) or (None, None) if not computable.
    """
    import math
    try:
        # Sort by InstanceNumber if present, otherwise by z-component of IPP
        def ipp_of(ds):
            try:
                return [float(x) for x in getattr(ds, "ImagePositionPatient")]
            except Exception:
                return None

        pairs = []
        for ds in slices:
            ipp = ipp_of(ds)
            if ipp is not None:
                inst = getattr(ds, "InstanceNumber", None)
                pairs.append((int(inst) if inst is not None else None, ipp, ds))

        if not pairs:
            return (None, None)

        # Sort: InstanceNumber first, else z
        if any(p[0] is not None for p in pairs):
            pairs.sort(key=lambda t: (t[0] is None, t[0]))
        else:
            pairs.sort(key=lambda t: float(t[1][2]))

        ipps = [p[1] for p in pairs]

        # Try to use normal from IOP if consistent; else fallback to Euclidean z differences
        normal = None
        try:
            # pick first ds with IOP
            for _, _, ds in pairs:
                if hasattr(ds, "ImageOrientationPatient"):
                    iop = [float(v) for v in ds.ImageOrientationPatient][:6]
                    if len(iop) == 6:
                        r = iop[:3]; c = iop[3:6]
                        n = [r[1]*c[2]-r[2]*c[1], r[2]*c[0]-r[0]*c[2], r[0]*c[1]-r[1]*c[0]]
                        nlen = math.sqrt(n[0]**2+n[1]**2+n[2]**2) or 1.0
                        normal = [n[0]/nlen, n[1]/nlen, n[2]/nlen]
                        break
        except Exception:
            normal = None

        gaps = []
        for a, b in zip(ipps, ipps[1:]):
            dx, dy, dz = (b[0]-a[0], b[1]-a[1], b[2]-a[2])
            if normal is not None:
                g = abs(dx*normal[0] + dy*normal[1] + dz*normal[2])
            else:
                g = abs(dz)
            if g > 0:
                gaps.append(g)

        if not gaps:
            return (None, None)

        mean_gap = sum(gaps) / len(gaps)
        var = sum((g-mean_gap)**2 for g in gaps) / len(gaps)
        std_gap = math.sqrt(var)
        return (mean_gap, std_gap)
    except Exception:
        return (None, None)


def _ipp_for_iso_slice(base_ipp, normal_vec, sz_mm, k):
    """
    Compute ImagePositionPatient for the k-th slice of an ISO-resampled stack.
    base_ipp: sequence of 3 floats (first slice origin)
    normal_vec: sequence of 3 floats (slice normal, unit length preferred)
    sz_mm: slice spacing in mm
    k: integer slice index (0-based)
    """
    try:
        bx, by, bz = [float(v) for v in base_ipp]
        nx, ny, nz = [float(v) for v in normal_vec]
        step = float(sz_mm) * float(k)
        return [bx + nx*step, by + ny*step, bz + nz*step]
    except Exception:
        # Fallback: assume axial (z-only)
        try:
            return [float(base_ipp[0]), float(base_ipp[1]), float(base_ipp[2]) + float(sz_mm)*float(k)]
        except Exception:
            return [0.0, 0.0, float(sz_mm)*float(k)]


# Bind missing instance methods to the class if they were defined at module scope
try:
    if "DICOMRoiBatchApp" in globals():
        _cls = DICOMRoiBatchApp
        if ("load_dicom" in globals()) and not hasattr(_cls, "load_dicom"):
            _cls.load_dicom = globals()["load_dicom"]
        if ("prepare_roi" in globals()) and not hasattr(_cls, "prepare_roi"):
            _cls.prepare_roi = globals()["prepare_roi"]
except Exception:
    pass

