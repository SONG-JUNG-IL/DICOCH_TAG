# **DICOCH _TAG**  
*DICOCH* is a DICOM-based format created to document and preserve cultural heritage by attaching standardized metadata to X-ray images.  
**DICOCH = Digital Communication for Cultural Heritage**

---

# **DICOCH DICOM Converter – GUI Edition**

| Item | Value |
|------|-------|
| **Version** | **3.1 · 2025-06-24** |
| **Author**  | **Song Jung-il** (National Research Institute of Cultural Heritage, Korea) |
| **Contact** | **ssong85@korea.kr** |

---

## 📌 **Overview**

The converter transforms 16-bit TIFF images (single files or stacks) into fully compliant `.dcm` files and injects a rich set of **DICOCH private tags** (group `0013`) describing provenance, imaging conditions, and IIIF links—all through an intuitive Tkinter GUI. No command-line skills or external libraries required.

---

## ✨ **What’s New in v3.1**

| Area | Enhancement | Details |
|------|-------------|---------|
| **HU calibration** | **GUI-priority checkbox** | One click forces the GUI Slope / Intercept values to override those in the Excel template. |
| **Tag export** | **JSON support** | The complete tag table is now saved as `tag_info_*.json` (besides TXT & XLSX). |
| **Stability** | **NameError fix** | The tag-viewer widget is reused, eliminating intermittent `NameError` exceptions. |
| **Changelog panel** | **Info tab** | A new Info pane shows feature history and author credits. |

---

## 📂 **Repository Layout**

