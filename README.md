#DICOCH_TAG
DICOCH is a DICOM-based format designed to support the documentation and preservation of cultural heritage through standardized metadata for X-ray images.
DICOCH = Digital Communication for Cultural Heritage

#DICOCH DICOM Converter – GUI Edition
Version: 3.1 · 2025-06-24
Author: Song Jung-il (National Research Institute of Cultural Heritage, Korea)
Contact: ssong85@korea.kr

📌 Overview
The converter turns 16-bit TIFF (or TIFF stacks) into standard-compliant .dcm files enriched with a rich set of DICOCH private tags (group 0013) that capture provenance, imaging conditions and IIIF links.
Everything is performed through an intuitive Tkinter GUI—no command line knowledge or external dependencies required.

✨ What’s new in v3.1
Area	Enhancement	Details
HU calibration	GUI-priority checkbox	A single click forces the GUI Slope / Intercept to override values coming from the tag template.
Tag export	JSON support	In addition to TXT & XLSX, the full tag table is now exported as pretty-printed tag_info_*.json.
Stability	NameError fix	Internal tag-viewer widget is reused instead of being rebuilt, eliminating occasional NameError on repeated conversions.
Changelog panel	Up-to-date Info tab	The GUI “Info” tab summarises feature history and author credits.

📂 Repository Layout

📁 DICOCH_TAG/
├── 1.DICOCH_converter_v3.1.py        # Main GUI application
├── 2.tag_template_base.xlsx          # Editable tag template (0013,xxxx hierarchy)
├── 3.example_dicoch.tif              # Sample 16-bit X-ray slice
├── README.md                         # (you are here)

Tip Start by duplicating 2.tag_template_base.xlsx and inserting project-specific metadata before your first run.

🚀 Quick Start

python 1.DICOCH_converter_v3.1.py
1.TIFF 폴더 – pick a folder that contains one or more 16-bit .tif images.
2.태그 엑셀 – select your (possibly edited) tag template.
3.출력 폴더 – accept the auto-generated path or choose another location.
4.(Optional) set Slope & Intercept; tick GUI 우선 적용 if these must override the Excel values.
5.Click [변환 시작] – a progress bar tracks multithreaded conversion.
6. When finished you will find:

output_YYYYMMDD_HHMMSS/
 ├─ *.dcm                         # One file per input slice
 ├─ dicom.dic                     # Private-tag dictionary
 ├─ log_YYYYMMDD_HHMMSS.txt       # Conversion log
 ├─ tag_info_*.txt / .xlsx / .json# Saved tag table
 └─ (optional) Mirador link       # Auto-opens if IIIF URL present

 
🖥️ GUI Walk-through
Element	                         Purpose
Slope / Intercept fields	       Provide calibration values (defaults 1 / -1024).
Slope/Intercept GUI 우선 적용	    Ensures those values always win over Excel.
태그 검사	                         Runs a pre-flight check for invalid DA / TM formats and missing sequences.
태그 결과 저장	                   Exports the in-memory tag DataFrame to TXT / XLSX / JSON.
IIIF 뷰어 열기	                   Launches Mirador with the detected (or manually entered) manifest.

The Info tab (second notebook page) lists the full contact information, CC-BY-SA license notice, and a condensed changelog.

🔖 DICOCH Private Tag Map (0013,xxxx)
A condensed sample (see generated dicom.dic for the full list):

(0013,0010) LO “DICOCH”                     # Private Creator
(0013,1001) LO “HeritageName”
(0013,1002) LO “HeritageID”
(0013,1100) SQ “HeritageMetaSeq”  → Item 0 … n
(0013,1200) DS “MeanGrayValue”
(0013,1300) DS “RescaleSlope”
(0013,1400) DS “RescaleIntercept”
(0013,1700) UT “IIIFManifestURL”
Each nested SQ item automatically receives a block-specific creator plus the top-level “DICOCH” creator, keeping the dataset perfectly legal under the DICOM private-tag rules.

✅ Example Output
3.example_dicoch.tif → 3.example_dicoch.dcm (37 private tags, 2 nested sequences).
All tags validated with pydicom and load correctly in RadiAnt, Horos and Myrian.

🔄 Change History (excerpt)
Date	          Ver.	            Highlights
2025-06-24	    3.1	            GUI-priority Slope / Intercept, JSON export, widget fix
2025-06-23	    3.0	            Improved Info tab UI, creator de-duplication
2025-06-22	    2.9u	            Automatic IIIF viewer link, rewritten dicom.dic
see CHANGELOG_legacy.md for earlier versions.		

📘 License
Creative Commons BY-NC-SA 4.0 – free to use, modify and redistribute for non-commercial cultural-heritage purposes with attribution.

📞 Contact
Song Jung-il (송정일)
Center for Conservation Science, National Research Institute of Cultural Heritage, Republic of Korea
📧 ssong85@korea.kr | GitHub https://github.com/SONG-JUNG-IL/DICOCH_TAG

🔖 Citation
@misc{Song2025_DICOCH,
  author       = {Jung-il Song},
  title        = {DICOCH DICOM Converter v3.1: Metadata Embedding Tool for Cultural Heritage Imaging},
  year         = {2025},
  howpublished = {\url{https://github.com/SONG-JUNG-IL/DICOCH_TAG}},
  note         = {National Research Institute of Cultural Heritage}
