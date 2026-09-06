# CLAUDE.md — DICOCH_TAG

## 1. 이 저장소의 성격

논문 **"The DICOCH Pipeline: A Verifiable, Standards-Based Workflow for Integrating Cultural Heritage CT/X-ray Data from DICOM Generation to IIIF Publication"** 의 **Supplementary Information(SI) 패키지**다.

- 주저자: Il-Jung Song (교신저자) 외 / 국립문화유산연구원(NRICH)
- **일반 소프트웨어 저장소가 아니라 심사·재현성 검증 대상 자료다.** 따라서 최우선 품질 기준은 "동작"이 아니라 **"제3자가 문서만 보고 그대로 재현할 수 있는가"** 이다.
- 리팩터링·기능 추가보다 **재현 절차와 실제 코드의 일치**를 우선한다.

## 2. 구조

```
README.md                                   # SI 패키지 개요 (= Supplementary Information for DICOCH.md 와 동일 내용)
CR_AndongHahoeMask.{jpg,json}               # CR 대표 산출물 (IIIF manifest + 파생 이미지)
CT_slice1177_AndongHahoeMask.{jpg,json}     # CT 대표 산출물
Supplementary Information for DICOCH/
  ├─ Source_Code/
  │   ├─ a.DICOCH DICOM Converter.py            (1,273행) 원본 이미지 + 태그 시트 → DICOM Part 10 생성
  │   ├─ b.DICOCH - JPEG - Manifest - Validation.py (1,651행) dciodvfy 검증 → 8bit 파생 → IIIF Manifest
  │   ├─ c.DICOM ROI Cropper.py                 (4,282행) HU/ROI 정량분석 + 통계값 태그 임베딩
  │   ├─ tools.zip                              (21 MB) 보조 바이너리
  │   └─ README_DICOM_ROI_Cropper_{en,ko}_.md
  ├─ Tag Template Spreadsheet_{CT,CR}_TAG/      # 메타데이터 입력 시트 (xlsx/csv)
  ├─ Sample Datasets & Manifests_{CT,CR}_...    # 생성 결과 예시
  └─ report_*_dciodvfy_*.html                   # 규격 검증 로그 (논문 Table 9 근거)
```

**파이프라인 순서: `a.` → `b.` → (`c.` 는 정량분석 분기)**

## 3. 실행 환경 (실제 import 기준)

세 스크립트 모두 **표준 라이브러리 `tkinter` 기반 GUI**다. PySide6/PyQt는 코드 어디에도 사용되지 않는다.

```bash
pip install pydicom numpy pandas openpyxl Pillow tifffile chardet SimpleITK python-gdcm pynrrd scipy psutil
# Linux 에서는 tkinter 별도 설치 필요:  sudo apt install python3-tk
```

| 스크립트 | 고유 의존성 |
|---|---|
| `a.` | tifffile, openpyxl, pandas |
| `b.` | chardet, openpyxl, pandas, urllib(stdlib) |
| `c.` | SimpleITK, python-gdcm, pynrrd, scipy, psutil |

**외부 바이너리:** `b.` 는 [dicom3tools](https://www.dclunie.com/dicom3tools.html) 의 `dciodvfy` 를 호출한다.

## 4. 코드 수정 시 반드시 지킬 것

### 4.1 Windows 종속성이 설계에 포함되어 있다

- `b.` 의 `short_path()` 는 `ctypes.windll.kernel32.GetShortPathNameW` 를 직접 호출한다.
  (한글·공백 포함 장경로를 dciodvfy가 처리하지 못하는 문제의 우회책이며, **의도된 동작**이다.)
- `find_dciodvfy()` 는 `dciodvfy.exe` 만 탐색한다.
- 기본 경로 상수: `DEFAULT_DCIODVFY = r"C:/tools/dicom3tools/bin/dciodvfy.exe"`

→ 크로스플랫폼화는 **논문 심사 이후로 미룬다.** 지금 손대면 논문에 기재된 검증 로그와 실행 경로가 어긋난다.

### 4.2 검증 로그는 논문 본문과 결속되어 있다

`report_*_dciodvfy_*.html` 의 "Errors: 0 / Warnings: 0" 결과는 **논문 Table 9의 근거**다.
스크립트 출력 형식이나 태그 생성 로직을 바꾸면 로그를 재생성하고 논문 수치를 동시에 갱신해야 한다.
→ **로그 재생성 없이 로직만 수정하지 말 것.**

### 4.3 사적 태그(Private Dictionary)

`DICOCH Private Dictionary_{CT,CR}_DICOCH_dict` 의 그룹·엘리먼트 번호는 생성물과 1:1로 대응한다.
번호 변경은 기존 산출물 전체를 무효화한다.

### 4.4 예외 처리 관행

GUI 응답성 유지를 위해 `except: pass` 가 다수 존재한다(`c.` 54개소, `a.` 14개소, `b.` 6개소).
**새 코드에서는 따라 하지 말 것.** 최소한 `logging.debug()` 로 흔적을 남긴다.
기존 것을 일괄 정리하려면 별도 커밋으로 분리하고, 정리 전후 dciodvfy 결과 동일성을 확인한다.

## 5. 알려진 미해결 사항

| 항목 | 위치 | 상태 |
|---|---|---|
| 저자 로컬 경로가 주석으로 잔존 | `b.` 202행 (`C:\Users\USER\Desktop\논문_ 코드20250702\...`) | SI 공개 전 삭제 필요 |
| `tools.zip` (21 MB) 이 Git에 직접 커밋됨 | `Source_Code/tools.zip` | 저장소 비대화. 배포 시 별도 첨부 검토 |
| Excel 임시파일 커밋 | `~$2.tag_template_filled_...xlsx` | 삭제 필요 |
| `requirements.txt` 부재 | 저장소 루트 | 3장 목록으로 생성 권고 |

## 6. 작업 규칙

- 브랜치: `claude/<작업명>` — `main` 직접 푸시 금지
- 커밋 메시지: 한국어 가능. **논문 표/그림 번호에 영향을 주는 변경은 메시지에 명시** (예: `fix(b): dciodvfy 파서 수정 — Table 9 재생성 필요`)
- 대용량 바이너리(`*.zip`, `*.dcm`, 원본 슬라이스)는 신규 커밋 금지
