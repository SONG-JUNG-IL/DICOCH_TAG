# DICOM ROI Cropper — Final User Guide (FINAL, 2025-08-22)

> **For research/education only**. Not for medical diagnosis or treatment.
> Place this file next to the executable; the app’s **Help (KR)** tab can display it. *(Open with F1/F2, search with Ctrl+F)*

---

## About

* **Author**: SONG JUNG IL (송정일)
* **Affiliation**: NRICH, Cultural Heritage Conservation Science Center
* **Role**: Nondestructive testing of cultural heritage (X-ray / X-ray CT, Reconstruction)
* **Email**: [ssong85@korea.kr](mailto:ssong85@korea.kr)
* **Suggested citation**: “DICOM ROI Cropper (FINAL 2025-08-22), for research/education.”

---

## What can it do?

Load a **DICOM series** and:

* 📌 **Crop 3D ROIs**, **Isotropic (ISO) 3D resampling**, **ROI XY resampling**
* 📌 Save as **DICOM** (optional **JPEG-LS lossless**) or **NRRD**
* 📌 Export **HU reports** for points/ROIs (HU/Gray from original pixels + display Gray)
* 🆕 **Visibility & navigation tools**: Auto WL/WW (slice/series/ROI), auto “Find Body,” jump to max HU, 3-view MIP viewer

> Note: If ISO and XY resampling are both enabled, **ISO takes precedence** to avoid double interpolation. Without an ROI, the **entire frame** is processed.

---

## Install & Run

* **Recommended**: Windows 10/11 · Python 3.10–3.12
* **Required**: `pydicom`, `numpy`, `Pillow`, `tkinter`
* **Recommended**: `SimpleITK` (NRRD/resampling), `gdcm` (JPEG-LS lossless)

```powershell
# (Optional) Initialize PowerShell
conda init powershell

# Packages
pip install pydicom numpy pillow SimpleITK gdcm

# Run
python "DICOM_ROI_Cropper_FINAL.py"
```

* If DLL issues occur, install the latest **VC++ Redistributable** or use `gdcm` from `conda-forge`.
* To silence UTF-8 warnings (ISO\_IR\_192), update pydicom or add an encoding map (see Troubleshooting).

---

## Quick Start

1. **Load DICOM Folder** → choose a **single-frame** series folder.
2. On load, the app **checks WL/WW automatically** and adjusts if needed.
3. In the 512×512 preview, **drag an ROI** → set **Z Start/End**.
4. Verify **Spacing X/Y/Th** and **HU Slope/Intercept** if needed.
5. Choose resampling:

   * **Isotropic 3D**: target voxel size (mm) and method
   * **Resample ROI XY**: target width (px) and method *(if both on, ISO wins)*
6. Click **Save ROI (Single)** / **Save All (Batch)**.
7. Output folder includes `resample_audit.csv`, preview/report PNGs, and HU CSVs.

---

## UI Overview

* **Top Bar 1**: Load DICOM, Select ROI, Point HU, ROI HU, Save HU (Points/ROI), Show values on image
* **Top Bar 2**: Output (DICOM/NRRD), JPEG-LS, NRRD mode (`.nrrd` / `.nhdr + .raw.gz`), ASCII-only path, ROI/Point colors
* **Top Bar 3**: Spacing X/Y/Th, HU Slope/Intercept (k/b), Auto-save Preview/HU
* **Top Bar 4 (tone & automation)**:

  * Brightness · Contrast · Gamma
  * 🆕 **Auto WL/WW (slice/series)**
  * 🆕 **Auto WL from ROI**
  * 🆕 **Find Body**, **Jump Max HU**, **MIPs**
* **Z controls**: Z Start/End, Invert Z on save
* **Main**: Left—512 preview & ROI, Right—Batch/ROI list / Help tabs

**Hotkeys (examples)**:
`F` Find Body · `M` Jump Max HU · `W` Auto WL from ROI · `V` MIPs

---

## What’s New / Changed (FINAL 2025-08-22)

### Guaranteed consistency between display and measurement

* **One rule for HU**: always compute from **original pixels** (`ds.pixel_array`) using **per-slice** `RescaleSlope/Intercept`.
* **Gray (original) vs Disp (display)**:

  * **Gray** = original **16-bit** stored pixel value
  * **Disp** = **8-bit** screen value after WL/WW (+brightness/contrast/gamma)
* **CSV schema**: store `gray_*`, `hu_*`, `disp_*`, `slope`, `intercept` as **actual per-slice values**.
* **PixelSpacing axis mapping fixed**: `RowSpacing` (1st) → height (H), `ColSpacing` (2nd) → width (W).

### Series selection & safety

* **Prefer CT/16-bit** when multiple series exist in the same folder.
* **“Effectively 8-bit” detection**: even if `BitsStored>8`, if the value range is `0–255`, emit warnings.

  * Load the **true 16-bit CT** series to get the real Gray range.

### Consistent preview/overlay

* Both preview and full-res overlay use the same **HU → WL/WW → 8-bit** pipeline.
* **Auto WL/WW on load**: if too dark/bright/flat, adjust based on series statistics.

### Smart navigation

* **Find Body**: jump to the slice with the largest `hu>-800` region, **set ROI automatically**.
* **Jump Max HU**: jump to the global HU maximum and auto-set a neighborhood ROI.
* **Auto WL from ROI**: set WL/WW from ROI percentiles for instant visibility.
* **MIPs**: 3-view Axial/Coronal/Sagittal MIP popup for quick orientation.

### Saving & deliverables

* **Derived CT DICOM (16-bit)**: save original pixels (or ROI crop) as **CT Image Storage**.

  * When cropping, **offset IPP (ImagePositionPatient)** to keep geometry consistent.
* **Secondary Capture (optional)**: save the **current display state (8-bit)** for review/sharing.
* **Safer PNG crops**: boundary-safe crops to avoid `tile cannot extend outside image`.

---

## Resampling Options

* **Isotropic 3D**: resample to equal voxel size (mm). If IOP is valid, rebuild IPP to preserve geometry.
* **Resample ROI XY**: resample only XY of the ROI to target width (px).
* **When both enabled**: **ISO takes precedence** (prevents double interpolation).

---

## Outputs & Audit

* **DICOM**: prefer JPEG-LS lossless (if available), otherwise uncompressed.
* **NRRD**: save `.nrrd` (via SimpleITK); fallback to `.nhdr + .raw.gz` on failure.
* **HU Reports**:

  * **Points**: `idx, slice, x, y, gray, hu, disp, slope, intercept, timestamp, …meta`
  * **ROI**: `slice, xs, ys, ws, hs, ws_mm, hs_mm, hu_mean, hu_std, … gray_*, disp_*, slope, intercept, …meta`
* **`resample_audit.csv`**: records input/output shape, spacing, method, UIDs, notes.

---

## Tips: Display vs Original

* **There is only one HU**: `HU = Gray × Slope + Intercept`.
* There is **no separate “display HU”**. The screen shows HU through WL/WW as **8-bit** values.
* Reports store both **original Gray** and **display Disp** separately to avoid confusion.

---

## Troubleshooting

* **Preview too dark/bright/flat** → use **Auto WL/WW (series)**; then fine-tune with **Auto WL from ROI**.

* **Hard to find the subject** → use **Find Body**, **Jump Max HU**, and **MIPs**; then **Auto WL from ROI**.

* **`tile cannot extend outside image` while saving PNG** → boundary-safe cropping is applied; ensure ROI/points stay within bounds.

* **Warning: `Unknown encoding 'ISO_IR_192'`** → update pydicom or add:

  ```python
  from pydicom.charset import python_encoding
  python_encoding.update({'ISO_IR 192': 'utf-8', 'ISO_IR_192': 'utf-8'})
  ```

* **Gray sticks to 0–255** → likely a **derived (tone-mapped) series**. Reload a folder containing **only the true 16-bit CT** series.

* **JPEG-LS fails** → the app falls back to uncompressed. Verify viewer compatibility.

---

## FAQ

**Q. What if I don’t set an ROI?**
A. The **entire frame** is processed.

**Q. What happens if ISO and XY resampling are both enabled?**
A. **ISO wins**; XY is skipped to avoid double interpolation.

**Q. Why save as 16-bit CT DICOM?**
A. To keep original pixels, ensuring **stable HU/Gray reproducibility**. 8-bit SC is for viewing/sharing only.

---

## Changelog

* **2025-08-22 (FINAL)**

  * Per-slice HU conversion; CSV schema uses actual slope/intercept; PixelSpacing axis mapping fixed
  * Auto WL/WW (slice/series/ROI), Find Body, Jump Max HU, MIPs
  * CT/16-bit-first series selection + “effectively 8-bit” detection & warnings
  * 16-bit Derived CT saving (IPP offset on ROI crop), optional 8-bit SC
  * Safer PNG crops; polygon stats overwrite bug fixed
* **2025-08-16 (patched\_2)**: ISO/XY guard; full-frame fallback; audit log; JPEG-LS stability & fallback; manual NRRD route tidy
* **2025-08-14 (patched\_1)**: initial patch

---

## License / Disclaimer

Software is provided **as is**. **For research/education only.**
Handle DICOM **PHI** responsibly and follow your organization’s security policies.

---
