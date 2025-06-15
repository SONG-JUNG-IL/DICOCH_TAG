# DICOCH_TAG
DICOCH is a DICOM-based format designed to support the documentation and preservation of cultural heritage through standardized metadata for X-ray images. DICOCH: Digital Imaging and Communication for Cultural Heritage


# DICOCH DICOM Converter – GUI Edition

**Version**: 2.9m · 2025-06-15  
**Author**: Song Jung-il (National Research Institute of Cultural Heritage, Korea)  
**Contact**: ssong85@korea.kr

---

## 📌 Overview

**DICOCH** (Digital Imaging and Communication for Cultural Heritage) is a metadata specification and conversion system that extends the DICOM standard for use in cultural heritage X-ray and CT imaging.  
This tool converts 16-bit TIFF image stacks into valid `.dcm` files with customized private tags under group `0013`, supporting digital archiving, preservation, and automated metadata integration.

---

## ✨ Key Features

- 🧠 DICOCH Private Tag Format (0013,xxxx) fully supported
- 🗂️ Excel-based tag template with hierarchical `ParentTag` and `ItemIndex`
- ♻️ Nested `SQ` (Sequence) tag builder with cycle pruning (depth ≤ 20)
- 🏷️ RescaleSlope / Intercept auto-assigned unless overridden
- 🧪 Bio-Formats-compatible: `UR → UT` auto-conversion
- 💾 Output DICOM files + `dicom.dic` dictionary + conversion log saved
- 🌐 Fully GUI-based, portable, with no external dependencies

---

## 📂 Folder Structure

```
📁 DICOCH_Converter/
├── 1.DICOCH_converter20250614 10.py             # Main GUI converter
├── 2.Modified_Excel_Tag_Template_ParentItem.xlsx # Editable tag template
├── 3.raw_TIFF_ViewImage0770_slice0300.tif       # Sample input TIFF
├── 3.raw_TIFF_ViewImage0770_slice0300.dcm       # Output DICOM (with tags)
├── dicom.dic                                     # Generated tag dictionary
├── log_20250615_180318.txt                       # Conversion result log
```

---

## 🚀 How to Use

1. Run the script:
   ```bash
   python 1.DICOCH_converter20250614\ 10.py
   ```

2. In the GUI:
   - Select the **TIFF folder** (must contain 16-bit `.tif`)
   - Select the **Excel tag template** (e.g. `2.Modified_Excel_Tag_Template_ParentItem.xlsx`)
   - Choose **output folder** (auto-suffix with date/time)

3. Optionally:
   - Adjust **Rescale Slope/Intercept** if HU correction is needed
   - Click **"태그 검사"** to validate tag structure
   - Click **"변환 시작"** to generate `.dcm` files

---

## 🔖 DICOCH Private Tags (0013,xxxx)

DICOCH defines structured private metadata to describe heritage object imaging context. For example:

```
(0013,1001) LO "Heritage Name"
(0013,1002) LO "Heritage ID"
(0013,2001) DS "Mean Gray Value"
(0013,4003) DS "CH-HUCalibration Rescale Slope"
(0013,5005) LO "Mean HU"
```

📄 Full tag dictionary → see [`dicom.dic`](./dicom.dic)

---

## ✅ Example Output

✔ `3.raw_TIFF_ViewImage0770_slice0300.tif`  
✔ `3.raw_TIFF_ViewImage0770_slice0300_roi.tif`  
→ Converted successfully to `.dcm` files with nested private metadata sequences.  
Log saved to [`log_20250615_180318.txt`](./log_20250615_180318.txt)

---

## 📘 License

This project is distributed under the **Creative Commons BY-NC-SA 4.0 License**.  
Use permitted for research and cultural heritage preservation with proper attribution.

---

## 📞 Contact

**Song Jung-il (송정일)**  
Center for Conservation Science  
National Research Institute of Cultural Heritage, Republic of Korea  
📧 ssong85@korea.kr

---

## 🔖 Citation

```bibtex
@misc{DICOCH2025,
  author    = {Jung-il Song},
  title     = {DICOCH DICOM Converter: Metadata Embedding Tool for Cultural Heritage Imaging},
  year      = {2025},
  howpublished = {\url{https://github.com/YOUR_ORG/DICOCH-Converter}},
  note      = {National Research Institute of Cultural Heritage}
}
```
