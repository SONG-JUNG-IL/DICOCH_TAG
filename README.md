
---

# **1. DICOCH\_converter\_v3.2**

*DICOCH* is a DICOM-based format designed for documenting and preserving cultural heritage using standardized metadata attached to X-ray images.
**DICOCH = Digital Communication for Cultural Heritage**

---

# **DICOCH DICOM Converter – GUI Edition**

| Item        | Value                                                                      |
| ----------- | -------------------------------------------------------------------------- |
| **Version** | **3.2 · 2025-07-25**                                                       |
| **Author**  | **Song Jung-il** (National Research Institute of Cultural Heritage, Korea) |
| **Contact** | **[ssong85@korea.kr](mailto:ssong85@korea.kr)**                            |

---

## 📌 Overview

This GUI-based converter transforms 16-bit TIFF images (single files or stacks) into `.dcm` files compliant with the DICOM standard. It auto-injects an extensive set of **DICOCH private tags** (Group `0013`) that capture object metadata, imaging parameters, provenance, and IIIF links—enabling long-term digital preservation and interoperability with PACS, viewers, and IIIF platforms.

---

## ✨ What’s New in v3.2

| Area               | Enhancement                             | Details                                                                               |
| ------------------ | --------------------------------------- | ------------------------------------------------------------------------------------- |
| **dicom.dic name** | Auto-renaming                           | Saved as `Dic_DICOCH_DICOM_PravateTag_YYYYMMDD_HHMMSS.txt`.                           |
| **Tag validation** | Integrated into export                  | Any errors (e.g. bad DA/TM formats) now appear in an `error` column in the tag files. |
| **IIIF support**   | Auto-detection of manifest URLs         | Excel cells containing "IIIF" + HTTP links are automatically used for viewer launch.  |
| **Logging**        | Multithreaded conversion logs saved     | All processing messages go into `log_*.txt` in the output folder.                     |
| **GUI automation** | Post-conversion folder + viewer opening | Automatically opens output folder and Mirador viewer if enabled.                      |

---

## 📂 Repository Layout

```
DICOCH_TAG/
├── 1.DICOCH_converter_v3.2.py     # Main GUI application
├── 2.tag_template_base.xlsx       # Editable tag template (0013,xxxx)
├── 3.example_dicoch.tif           # Sample 16-bit X-ray image
└── README.md                      # This file
```

> **Tip**: Duplicate the tag template file and customize it for each heritage object before conversion.

---

## 🚀 Quick Start

```bash
python 1.DICOCH_converter_v3.2.py
```

1. **TIFF Folder** – Choose a folder containing one or more `.tif` images.
2. **Tag Excel** – Select your (possibly edited) Excel tag file.
3. **Output Folder** – Accept the auto-created folder name or set a custom path.
4. *(Optional)* Set Slope / Intercept manually.
5. *(Optional)* Tick “Apply Slope/Intercept in GUI” to override Excel values.
6. Click **\[변환 시작]** to begin batch conversion.
7. After processing:

---

## 📁 Output Structure

```
output_YYYYMMDD_HHMMSS/
├── *.dcm                          # DICOM files (1 per slice)
├── Dic_DICOCH_DICOM_PravateTag_*.txt  # Private tag dictionary
├── log_YYYYMMDD_HHMMSS.txt        # Conversion log
├── tag_info_*.txt/.xlsx/.json     # Saved tag metadata (multi-format)
└── (optional) Mirador URL         # Opens if a manifest is detected
```

---

## 🖥️ GUI Walk-through

| GUI Element                      | Description                                            |
| -------------------------------- | ------------------------------------------------------ |
| **Slope / Intercept**            | Default = 1 / -1024. Use for HU calibration.           |
| **Apply Slope/Intercept in GUI** | Overrides Excel values with GUI input.                 |
| **태그 검사**                        | Detects invalid values (e.g. bad DA/TM, orphan SQ).    |
| **태그 결과 저장**                     | Saves current tag table in TXT, XLSX, and JSON format. |
| **IIIF 뷰어 열기**                   | Opens Mirador with manifest URL (if available).        |
| **Info Tab**                     | Displays version history, license, author contact.     |

---

## 🔖 DICOCH Private Tag Map (0013,xxxx)

See `dicom.dic` for the full list. Highlights include:

| Tag         | VR | Description                |
| ----------- | -- | -------------------------- |
| (0013,0010) | LO | "DICOCH" (Root Creator)    |
| (0013,1001) | LO | Heritage Name              |
| (0013,1200) | DS | Mean Gray Value            |
| (0013,1300) | DS | Rescale Slope              |
| (0013,1400) | DS | Rescale Intercept          |
| (0013,1700) | UT | IIIF Manifest URL          |
| (0013,1100) | SQ | Heritage Metadata Sequence |

SQ items inherit both a root and block-specific private creator tag, maintaining full DICOM compliance.

---

## ✅ Sample Output

`3.example_dicoch.tif` → `3.example_dicoch.dcm`
→ Includes 37 private tags and 2 nested sequences
→ Successfully validated in RadiAnt, Horos, and Myrian

---

## 🔄 Change History

| Date       | Version | Highlights                                                             |
| ---------- | ------- | ---------------------------------------------------------------------- |
| 2025-07-25 | **3.2** | IIIF auto-detection, error column in tag export, log saving, UI polish |
| 2025-06-24 | 3.1     | GUI-priority slope/intercept, JSON export                              |
| 2025-06-23 | 3.0     | Info tab UI overhaul, creator de-duplication                           |
| 2025-06-22 | 2.9u    | IIIF viewer toggle, rewritten `dicom.dic`                              |
| 2025-06-20 | 2.9s    | Excel slope/intercept support, tag viewer + export                     |

---

## 📘 License

Creative Commons BY-SA 4.0
Free for academic and cultural heritage use with attribution.

---

## 📞 Contact

**Song Jung-il (송정일)**
Center for Conservation Science, NRICH
📧 [ssong85@korea.kr](mailto:ssong85@korea.kr)
🔗 [GitHub – DICOCH\_TAG](https://github.com/SONG-JUNG-IL/DICOCH_TAG)

---

## 🔖 Citation

```bibtex
@misc{Song2025_DICOCH,
  author       = {Jung-il Song},
  title        = {DICOCH DICOM Converter v3.2: Metadata Embedding Tool for Cultural Heritage Imaging},
  year         = {2025},
  howpublished = {\url{https://github.com/SONG-JUNG-IL/DICOCH_TAG}},
  note         = {National Research Institute of Cultural Heritage}
}
```

---

필요 시 위 내용을 `README.md` 파일로 변환하거나, 한글 병기 버전도 추가 제공해드릴 수 있습니다. Notion 문서화나 PDF 배포용 디자인도 지원 가능합니다.
