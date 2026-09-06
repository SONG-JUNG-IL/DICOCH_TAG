# Supplementary Information for DICOCH Pipeline

**Manuscript Title:** The DICOCH Pipeline: A Verifiable, Standards-Based Workflow for Integrating Cultural Heritage CT/X-ray Data from DICOM Generation to IIIF Publication
**Authors:** Il-Jung Song (Corresponding Author) et al.
**Institution:** National Research Institute of Cultural Heritage (NRICH)

---

## 1. Introduction
This Supplementary Information (SI) package contains all necessary resources to reproduce the findings presented in the manuscript. The material is organized into **Source Code** (implementation of the GVP workflow) and **Data Resources** (templates, dictionaries, and validation logs for the Case Study).

## 2. Directory Structure & Contents

The package is organized into three main categories based on the workflow and modality.

### **Part A. Source Code (Software Implementation)**
*Directory: Root / Tools*

* **`a.DICOCH DICOM Converter.py`**: The main script for generating DICOM Part 10 files from raw images (TIFF/PNG) and tag spreadsheets.
* **`b.DICOCH - JPEG - Manifest - Validation.py`**: The automation script that validates DICOM outputs (`dciodvfy`), generates 8-bit derivatives, and creates IIIF Manifests.
* **`c.DICOM ROI Cropper.py`**: A GUI tool for quantitative HU/ROI analysis and statistics embedding.
* **`tools/`**: Contains auxiliary binaries or helper scripts.
* **`README_DICOM_ROI_Cropper_*.md`**: User manuals for the ROI analysis tool.

### **Part B. CT Resources (Computed Tomography Case)**
*Directory: CT_Data_Resources*

1.  **`DICOCH Private Dictionary_CT_DICOCH_dict`**:
    * Contains the private tag definitions for CT (e.g., FrameOfReference, SliceThickness handling in Private groups).
2.  **`Tag Template Spreadsheet_CT_TAG`**:
    * The metadata input sheet used to generate the Andong Hahoe Mask CT dataset.
3.  **`Image Template Spreadsheet_CT_image`**:
    * The mapping sheet for raw slice images.
4.  **`Validation Logs_CT_dciodvfy_output_...`**:
    * **Crucial:** The `dciodvfy` log confirming **"Errors: 0 / Warnings: 0"** for the DICOCH-generated CT files.
5.  **`Validation Logs_CT_dciodvfy_VENDER_...`**:
    * **Benchmark:** The validation log for the original Vendor DICOM files, demonstrating the errors/warnings cited in **Table 9** of the manuscript.
6.  **`Sample Datasets & Manifests_CT_...`**:
    * Contains the generated IIIF Manifest (JSON) and sample outputs.

### **Part C. CR Resources (Computed Radiography Case)**
*Directory: CR_Data_Resources*

1.  **`DICOCH Private Dictionary_CR_DICOCH_dict`**:
    * Contains the private tag definitions for CR (e.g., ImagerPixelSpacing, PlateType).
2.  **`Tag Template Spreadsheet_CR_TAG`**:
    * The metadata input sheet for the CR dataset.
3.  **`Validation Logs_CR_dciodvfy_output_...`**:
    * The compliance log confirming **"Errors: 0 / Warnings: 0"** for DICOCH CR files.
4.  **`Validation Logs_CR_dciodvfy_VENDER_...`**:
    * **Benchmark:** Validation log showing errors in the original Vendor CR files (Reference for Table 9).

---

## 3. How to Reproduce (Audit Trail)

To verify the results reported in the manuscript:

1.  **Environment:** Install Python 3.9+ and the dependencies actually imported by the scripts:
    ```bash
    pip install pydicom numpy pandas openpyxl Pillow tifffile chardet SimpleITK python-gdcm pynrrd scipy psutil
    ```
    * The GUIs are built on the standard-library `tkinter`; no Qt binding (PySide6/PyQt) is required.
      On Linux, install it separately (`sudo apt install python3-tk`).
    * Script `b.` additionally requires the `dciodvfy` executable from
      [dicom3tools](https://www.dclunie.com/dicom3tools.html) on `PATH`.
    * `ctypes.windll` is used in script `b.` for Windows 8.3 short-path conversion, so the
      validation stage is currently Windows-only.
2.  **Generation:** Run script `a.` using the provided `Tag Template` and `Image Template` from the CT/CR directories.
3.  **Validation:** Run script `b.` targeting the output folder.
    * *Verification:* Compare the newly generated log with the provided `Validation Logs_..._output` to confirm they match.
4.  **Comparison:** Review `Validation Logs_..._VENDER` to observe the non-compliance issues in standard vendor outputs, as discussed in the "Results" section.

---

## 
