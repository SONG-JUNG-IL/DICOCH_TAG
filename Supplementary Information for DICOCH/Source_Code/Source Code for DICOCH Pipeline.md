# Source Code for DICOCH Pipeline

**Manuscript Title:** The DICOCH Pipeline: A Verifiable, Standards-Based Workflow for Integrating Cultural Heritage CT/X-ray Data from DICOM Generation to IIIF Publication
**Authors:** Il-Jung Song (Corresponding Author) et al.
**Institution:** National Research Institute of Cultural Heritage (NRICH)

---

## 1. Overview
This package contains the Python implementation of the DICOCH (Digital Imaging and Communication in Cultural Heritage) pipeline. The scripts cover the full lifecycle of data handling described in the manuscript: Generation (G), Validation (V), Publication (P), and supplementary quantitative analysis (ROI cropping).

## 2. File Manifest & Description

The source code is organized into three main modules corresponding to the workflow steps:

### **a. DICOCH DICOM Converter.py**
* **Role:** [Generation] Module
* **Function:** Converts raw X-ray/CT images (TIFF, PNG) into DICOM Part 10 files.
* **Key Features:**
    * Parses the `Tag Input Template` (Excel/CSV).
    * Normalizes pixel data and geometry.
    * Injects DICOCH private tags for heritage context.
* **Reference:** Corresponds to Section 2.5.1 of the manuscript.

### **b. DICOCH - JPEG - Manifest - Validation.py**
* **Role:** [Validation & Publication] Module
* **Function:**
    1.  Validates the generated DICOM files using `dciodvfy` (External tool).
    2.  Generates 8-bit JPEG derivatives for web viewing.
    3.  Creates IIIF Presentation 3.0 Manifests (JSON).
* **Key Features:** Automated verification loop ensuring "Errors=0" compliance.
* **Reference:** Corresponds to Section 2.5.2 (Validation) and 2.5.3 (Publication).

### **c. DICOM ROI Cropper.py**
* **Role:** [Supplementary Analysis] Tool
* **Function:** A GUI-based tool for analyzing HU (Hounsfield Units) and defining ROIs (Regions of Interest).
* **Key Features:**
    * Calculates statistics (Mean, SD, Min, Max) for specific heritage materials.
    * Embeds these statistics into the DICOCH Private Group `(0013,xxxx)`.
* **Reference:** Corresponds to Section 2.5.4 and the Case Study analysis.

### **Other Files**
* `README_DICOM_ROI_Cropper_en/ko.md`: Specific user manuals for the ROI Cropper tool.
* `tools/`: Directory containing necessary binaries or auxiliary scripts (e.g., `dciodvfy` or helper utils).

---

## 3. System Requirements & Installation

To run these scripts, the following environment is required:

* **Python:** Version 3.9 or higher
* **Dependencies:** Please install the required libraries using pip:
    ```bash
    pip install pydicom numpy pandas openpyxl Pillow tifffile chardet SimpleITK python-gdcm pynrrd scipy psutil
    ```
    *(Note: the GUI elements shown in Fig. 2-4 are implemented with the standard-library `tkinter`.
    No Qt binding is required. On Linux, install `tkinter` separately: `sudo apt install python3-tk`.)*

    Per-script dependencies:

    | Script | Additional imports |
    |---|---|
    | `a.DICOCH DICOM Converter.py` | `tifffile`, `openpyxl`, `pandas` |
    | `b.DICOCH - JPEG - Manifest - Validation.py` | `chardet`, `openpyxl`, `pandas` |
    | `c.DICOM ROI Cropper.py` | `SimpleITK`, `python-gdcm`, `pynrrd`, `scipy`, `psutil` |

* **External Validator:**
    * The `dciodvfy` executable (from `dicom3tools`) must be present.
    * Path configuration may be required inside the script or via the GUI settings.

## 4. Usage Guide

All tools operate via a Graphical User Interface (GUI) as described in the manuscript.

1.  **Generation:** Run `python "a.DICOCH DICOM Converter.py"`. Select your input image folder and tag spreadsheet.
2.  **Validation/Publishing:** Run `python "b.DICOCH - JPEG - Manifest - Validation.py"`. Point it to the output folder from Step 1.
3.  **Analysis:** Run `python "c.DICOM ROI Cropper.py"` to load the DICOM dataset and perform ROI analysis.

---

## 5. License
These scripts are provided for peer-review and reproducibility verification.
Copyright (c) 2025 National Research Institute of Cultural Heritage (NRICH).