# CLAUDE.md — DICOCH Pipeline

이 파일은 Claude Code가 이 저장소에서 작업할 때 매 세션 자동으로 읽는 프로젝트 안내서다.
`.claude/rules/`의 일반 규칙과 이 파일이 충돌하면 **이 파일이 우선한다.**

## 1. 프로젝트 개요

- **DICOCH** (Digital Imaging and Communication in Cultural Heritage): 문화유산 CT/X-ray(CR) 영상을
  DICOM 생성 → 규격 검증 → IIIF 공개까지 처리하는 파이프라인 (Generation–Validation–Publication).
- 이 저장소는 논문 *"The DICOCH Pipeline: A Verifiable, Standards-Based Workflow …"*(NRICH)의
  **보충자료(Supplementary Information, SI)** 이자 재현성 검증용 패키지다.
- 사례 데이터: 안동 하회탈(Andong Hahoe Mask) CT slice 1177, CR 영상.

## 2. 저장소 구조

```
README.md                                  SI 패키지 설명 (영문)
CR_/CT_*AndongHahoeMask.{jpg,json}          대표 결과물 (8-bit JPEG, IIIF manifest)
Supplementary Information for DICOCH/
├── Source_Code/
│   ├── a.DICOCH DICOM Converter.py         [G] 원본 영상 + 태그 시트 → DICOM Part 10 (v3.3)
│   ├── b.DICOCH - JPEG - Manifest - Validation.py
│   │                                       [V/P] dciodvfy 검증, JPEG 파생본, IIIF Presentation 3.0 manifest (v1.5)
│   ├── c.DICOM ROI Cropper.py              [분석] HU/ROI 통계 산출 및 Private 태그 기록
│   ├── README_DICOM_ROI_Cropper_{ko,en}_.md
│   └── tools.zip                           dicom3tools Windows 실행파일 (dciodvfy 등)
├── Tag Template Spreadsheet_{CT,CR}_TAG/   태그 입력 템플릿 (xlsx/csv)
├── Sample Datasets & Manifests_{CT,CR}_…/  샘플 JPEG + manifest
└── report_*_dciodvfy_*.html                검증 리포트 (DICOCH 산출물 vs 벤더 원본)
```

## 3. 실행 환경

- Python 3.9+. 세 스크립트 모두 **tkinter GUI** 기반이며 Windows 사용을 전제로 한다(`ctypes`, `.exe` 검증기).
  - 문서에는 PySide6가 언급되어 있으나 실제 코드는 tkinter를 사용한다.
- 스크립트별 의존성:

| 스크립트 | 필수 | 선택(가드 처리) |
|---|---|---|
| a. Converter | pydicom, numpy, pandas, openpyxl, Pillow, tifffile | — |
| b. Validation | pydicom, numpy, pandas, openpyxl, Pillow, chardet | — |
| c. ROI Cropper | pydicom, numpy, Pillow | SimpleITK, gdcm, pynrrd, psutil, scipy |

- 외부 검증기: `dciodvfy` (dicom3tools, `tools.zip` 내 포함).
- GUI 없는 환경(클라우드 세션 등)에서는 스크립트를 실행할 수 없으므로 아래 정적 검사로 확인한다.

```bash
# 구문 검사 (GUI 미실행)
cd "Supplementary Information for DICOCH/Source_Code"
for f in *.py; do python3 -c "import ast,sys; ast.parse(open(sys.argv[1],encoding='utf-8').read())" "$f" && echo "OK $f"; done
# 린트 (설치된 경우)
ruff check .
```

## 4. 도메인 원칙 (DICOM / IIIF)

1. **검증 기준은 `dciodvfy` Errors 0 / Warnings 0** 이다. DICOM 태그 생성 로직을 바꾸면 반드시 재검증이 필요하다고
   명시하고, 검증 없이 "규격 준수"라고 단정하지 않는다.
2. **문자셋:** `SpecificCharacterSet = ["ISO_IR 192"]`(UTF-8)를 유지한다. 공백이 포함된 값을 분할하지 않는다.
3. **Private 태그:** DICOCH 전용 그룹 `(0013,xxxx)`를 사용하며 Private Creator는 `(0013,0010+slot)`에 기록된다.
   그룹 번호, Private Creator 문자열, 사전(dictionary) 항목은 기존 데이터와 호환되도록 임의로 바꾸지 않는다.
4. **화소값 보존:** 원본 비트 깊이와 `RescaleSlope`/`RescaleIntercept`(HU 계산)를 보존한다.
   8-bit 변환은 웹 열람용 파생본(JPEG)에서만 수행한다.
5. **UID:** `pydicom.uid.generate_uid()` 등 표준 방식으로 생성하며, 하드코딩하거나 재사용하지 않는다.
6. **논문 증빙 자료는 수정 금지:** `report_*.html`, 태그 템플릿, 샘플 manifest, 대표 JPEG/JSON은 논문
   (Table 9 등)의 근거 자료다. 사용자가 명시적으로 요청하지 않는 한 수정·삭제·재생성하지 않는다.

## 5. 대용량 영상 처리 원칙 (메모리)

CT 볼륨은 수천 장 슬라이스, 수 GB 규모가 될 수 있다.

- 메타데이터만 필요하면 `pydicom.dcmread(..., stop_before_pixels=True)`를 사용한다.
- 전체 볼륨을 한 번에 적재하지 말고 슬라이스 단위(제너레이터/배치)로 처리한다.
- dtype을 의식한다: `int16` → 계산이 필요할 때만 `float32`로 올리고, 불필요한 `float64` 승격을 피한다.
- 대형 배열은 **in-place 연산을 허용한다.** `.claude/rules/common/coding-style.md`의 "불변 객체" 원칙은
  일반 객체·설정값에 적용하고, 대용량 numpy 배열에는 메모리 절약을 우선한다.
- 장시간 작업은 GUI를 멈추지 않도록 스레드로 분리하고(기존 코드 방식 유지), 예외는 traceback과 함께 로그에 남긴다.

## 6. ECC 규칙 적용 범위 (우선순위 조정)

`.claude/` 하위 규칙·에이전트·스킬은 Everything Claude Code(ECC)에서 선별 도입했다(출처: `.claude/THIRD_PARTY_NOTICES.md`).
이 저장소의 특성에 맞춰 다음과 같이 조정해 적용한다.

| ECC 규칙 | 이 저장소에서의 적용 |
|---|---|
| 파일 800줄 상한 | **신규 모듈에만** 적용. 기존 대형 스크립트(예: ROI Cropper 4,282줄)는 사용자가 요청할 때만 분할·리팩터링한다. |
| 테스트 커버리지 80% | 신규 **순수 로직**(태그 파싱, UID, manifest 생성 등)에 목표로 적용. tkinter GUI 코드는 제외한다. |
| 타입 힌트, black/isort/ruff | 신규·수정 코드에 적용. 무관한 기존 코드를 일괄 포맷팅하지 않는다(diff 최소화). |
| `planner`, `tdd-guide`, `code-reviewer`, `architect`, `build-error-resolver` 에이전트 언급 | **설치되어 있지 않다.** 코드 리뷰는 `python-reviewer`, 보안 점검은 `security-reviewer`를 사용하고, 나머지는 직접 수행한다. |
| `verification-loop`의 npm/tsc 단계 | 해당 없음. 3절의 구문 검사·ruff·pytest로 대체한다. |

- 스크립트 기능을 변경하면 파일 상단의 버전 헤더(예: `v3.3 (2025-08-11)`)와 변경 내역을 함께 갱신한다.

## 7. 커밋 규칙

- 형식: `<type>: <설명>` (type: feat, fix, refactor, docs, test, chore, perf, ci). 설명은 한국어 가능.
- Excel 잠금 파일(`~$*.xlsx`)이나 대용량 바이너리를 새로 커밋하지 않는다.
