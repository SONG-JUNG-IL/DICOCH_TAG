
---

# **1.DICOCH_converter_v3.1**
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


## 📌 Overview
The converter transforms 16-bit TIFF images (single files *or* stacks) into standard-compliant `.dcm` files while injecting an extensive set of **DICOCH private tags** (group `0013`) capturing provenance, imaging conditions and IIIF links.  
Everything happens through an intuitive Tkinter GUI—no command-line skills or external dependencies required.


---


## ✨ What’s New in v3.1

| Area | Enhancement | Details |
|------|-------------|---------|
| **HU calibration** | **GUI-priority checkbox** | One click forces the GUI **Slope / Intercept** values to override those in the Excel template. |
| **Tag export** | **JSON support** | The complete tag table is now also saved as `tag_info_*.json`. |
| **Stability** | **NameError fix** | Re-using the tag-viewer widget eliminates intermittent `NameError` exceptions. |
| **Changelog panel** | **Info tab** | A new *Info* pane summarises feature history and author credits. |


---


## 📂 Repository Layout
DICOCH_TAG

├── 1.DICOCH_converter_v3.1.py # Main GUI application

├── 2.tag_template_base.xlsx # Editable tag template (0013,xxxx hierarchy)

├── 3.example_dicoch.tif # Sample 16-bit X-ray slice

└── README.md # (this file)

> **Tip **Duplicate **`2.tag_template_base.xlsx`** and fill it with project-specific metadata before the first run.


---


## 🚀 Quick Start

python 1.DICOCH_converter_v3.1.py

1.TIFF 폴더 – choose a folder containing one or more 16-bit .tif images.

2.태그 엑셀 – select your (possibly edited) tag template.

3.출력 폴더 – accept the auto-generated path or specify another location.

4.(Optional) edit Slope / Intercept; tick “Slope/Intercept GUI 우선 적용” to override Excel values.

5.Click [변환 시작] – a progress bar tracks multithreaded conversion.

6.When finished you will find:


---


## output_YYYYMMDD_HHMMSS/

├─ *.dcm                       # One file per input slice

├─ dicom.dic                   # Private-tag dictionary

├─ log_YYYYMMDD_HHMMSS.txt     # Conversion log

├─ tag_info_*.txt/.xlsx/.json  # Saved tag table

└─ (optional) Mirador link     # Auto-opens if IIIF URL present


---


## 🖥️ GUI Walk-through
| Element                            | Purpose                                                               |
| -----------------------------      | --------------------------------------------------------------------- |
| **Slope / Intercept**              | Provide calibration (defaults `1` / `-1024`).                         |
| **Slope/Intercept GUI 우선 적용**  | Ensures GUI values override Excel.                                    |
| **태그 검사**                      | Pre-flight check for invalid DA/TM formats and orphan sequences.      |
| **태그 결과 저장**                 | Export the current tag table to TXT, XLSX *and* JSON.                 |
| **IIIF 뷰어 열기**                 | Opens *Mirador* with the detected (or manually entered) manifest URL. |
| **Info 탭**                        | Displays license, contact information and a condensed changelog.      |


---


## 🔖 DICOCH Private Tag Map (0013,xxxx)
(See the generated dicom.dic for the full list.)

(0013,0010) LO "DICOCH"              # Private Creator

(0013,1001) LO "HeritageName"

(0013,1002) LO "HeritageID"

(0013,1100) SQ "HeritageMetaSeq"     → Item 0…n

(0013,1200) DS "MeanGrayValue"

(0013,1300) DS "RescaleSlope"

(0013,1400) DS "RescaleIntercept"
(
0013,1700) UT "IIIFManifestURL"

Nested SQ items automatically inherit both a block-specific creator and the root “DICOCH” creator, maintaining full DICOM compliance.


---


## ✅ Example Output
3.example_dicoch.tif → 3.example_dicoch.dcm-37 private tags, 2 nested sequences
-Successfully validated in RadiAnt, Horos and Myrian using pydicom


---


## 🔄 Change History (excerpt)
| Date       | Ver.    | Highlights                                              |
| ---------- | ------- | ------------------------------------------------------- |
| 2025-06-24 | **3.1** | GUI-priority Slope / Intercept, JSON export, widget fix |
| 2025-06-23 | 3.0     | Improved Info tab UI, creator de-duplication            |
| 2025-06-22 | 2.9u    | Automatic IIIF viewer link, rewritten `dicom.dic`       |




---

# **2.DICOM to JPEG _ tags_IIIF manifest converter_GUI**
*A one-stop desktop tool that turns DICOM studies into IIIF-ready assets—manifest, JPEGs, and a tag spreadsheet—while respecting the private-tag conventions of DICOCH.*


---


# **IIIF Manifest Generator – GUI Edition**

| Item   | Value                                                                                           |
|--------|-------------------------------------------------------------------------------------------------|
| **Version** | **1.3 · 2025-07-04** |
| **Author**  | **Song Jung-il** (National Research Institute of Cultural Heritage, Korea) |
| **Contact** | **ssong85@korea.kr** |


---


## 📌 Overview
The GUI ingests an entire **DICOM folder** and produces—without any command-line work—

* 8-bit **JPEG slices** (`/images/*.jpg`)

* A **tag table** (`tags_*.xlsx`)  

* A fully compliant **IIIF Presentation 3.0 manifest** (`manifest_*.json`)

The manifest uses **Canvas**+**Range** structure, auto-excludes binary VR (Pixel Data, OB/OW/…),
and embeds public URLs so Mirador or any IIIF viewer can open it instantly.


---


## ✨ What’s New in v1.3

| Area                    | Enhancement                                     | Detail |
|-------------------------|-------------------------------------------------|--------|
| **URL control**         | **Image Base URL** field                        | JPEGs can live on any CDN / GitHub Raw path. |
| **Auto-naming**         | Timestamped manifest / Excel                    | No overwrite, no manual typing needed. |
| **Size reduction**      | Pixel Data & binary VR filtered                 | Manifest stays in KB, never MB. |
| **Choice support**      | Multiple JPEGs with same prefix → 1 Canvas      | Mirador shows ☆ icon to switch Raw / Enhanced views. |
| **Thread safety**       | All Tk calls wrapped in `root.after()`          | No more random `TclError` when converting large sets. |


---


## 📂 Repository Layout
IIIF_MANIFEST_GUI/

├── gui_manifest_v1.3.py # Main Tkinter application

├── example_private_tags.txt # Editable 0013,xxxx dictionary

├── demo_series/ # Sample DICOM set (optional)

└── README.md # This file
> **Tip** – Duplicate **example_private_tags.txt** and edit it to match your project metadata (Heritage Name, HU calibration, etc.) before the first run.


---


1.DICOM folder – pick the folder with your .dcm files

2.Output folder – choose where results will land

3.Base URL – public root (e.g. https://song-jung-il.github.io/Public_image)

4. Image Base URL – raw JPEG host (e.g. GitHub Raw)

(Optional) Tag dictionary – load a CSV / XLSX / TXT with private tag keywords

Hit [Run] – multi-threaded conversion starts; a progress bar shows status


---


## When finished you’ll get

output_YYYYMMDD_HHMMSS/

├─ images/    # JPEGs

├─ manifest_YYYYMMDD_HHMMSS.json

└─ tags_YYYYMMDD_HHMMSS.xlsx


---


## GUI Walk-through

| Element            | Purpose                                             |
| ------------------ | --------------------------------------------------- |
| **Base URL**       | Root used for Manifest & Canvas IDs                 |
| **Image Base URL** | Absolute path for JPEGs (GitHub Raw, S3, etc.)      |
| **Tag dictionary** | Maps `(0013,xxxx)` to friendly keywords             |
| **Progress bar**   | Non-blocking; GUI never freezes                     |
| **Log window**     | Shows `[ok]`, `[warn]`, `[error]` messages in color |


---


## 🔖 DICOCH Private Tag Map (excerpt)
| Tag         | VR | Keyword             |
| ----------- | -- | ------------------- |
| (0013,0010) | LO | `_PrivateCreator`   |
| (0013,1101) | LO | `Heritage Name`     |
| (0013,1201) | DS | `Mean Gray Value`   |
| (0013,1700) | SQ | `Security Sequence` |
| (0013,1705) | LO | `Data Checksum`     |
(Full list is loaded from your dictionary file and written to tags.xlsx.)


---


## ✅ Example Output
*Input  3.example_dicoch.dcm + 3.example_2_dicoch.dcm  →  1 Canvas with Choice body
* Manifest loads in Mirador, slice toggles via ☆ icon.


---
📘 License
Creative Commons BY-NC-SA 4.0 – free for non-commercial cultural-heritage use with attribution.


---


📞 Contact
Song Jung-il (송정일)
Center for Conservation Science, National Research Institute of Cultural Heritage, Republic of Korea
📧 ssong85@korea.kr | GitHub https://github.com/SONG-JUNG-IL/DICOCH_TAG

---

🔖 Citation
@misc{Song2025_DICOCH,
  author       = {Jung-il Song},
  title        = {DICOCH DICOM Converter v3.1: Metadata Embedding Tool for Cultural Heritage Imaging}, {IIIF Manifest Generator v1.3: A DICOM-to-IIIF Toolkit for Cultural Heritage},
  year         = {2025},
  howpublished = {\url{https://github.com/SONG-JUNG-IL/DICOCH_TAG}},
  note         = {National Research Institute of Cultural Heritage}
}

---
