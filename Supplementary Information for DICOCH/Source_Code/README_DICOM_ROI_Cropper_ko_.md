아래는 첨부된 README(국/영문)를 기준으로, 최근 코드 변경사항까지 반영해 **보강·통합한 최종 README 초안**입니다. 바로 프로젝트 루트에 `README_DICOM_ROI_Cropper_FINAL_ko.md` 로 저장해 쓰시면 됩니다.

---

# DICOM ROI Cropper — 최종 사용자 가이드 (FINAL, 2025-08-22)

> **연구·교육용 도구**입니다. 의료 진단/치료 목적 사용 금지.
> 실행 파일과 같은 폴더에 두면 앱의 **설명(한글)** 탭에서 읽어옵니다. *(F1/F2로 열기, Ctrl+F 검색)*

## 소개(About)

* **작성자**: 송정일 / SONG JUNG IL
* **소속**: 국립문화유산연구원 문화유산보존과학센터
* **담당**: 문화유산 비파괴 진단 (X-ray / X-ray CT, Reconstruction)
* **이메일**: [ssong85@korea.kr](mailto:ssong85@korea.kr)
* **권장 인용**: “DICOM ROI Cropper (FINAL 2025-08-22), 연구/교육 목적.”

## 무엇을 할 수 있나요?

**DICOM 시리즈**를 불러와서 다음을 수행합니다.

* 📌 **ROI 3D 크롭**, **등방성(ISO) 3D 리샘플**, **ROI XY 리샘플**
* 📌 **DICOM(옵션 JPEG-LS 무손실) / NRRD**로 저장
* 📌 **HU 포인트/ROI 리포트** (원본 픽셀 기준 HU/Gray + 디스플레이 Gray)
* 🆕 **표시 최적화 & 탐색 도구**: Auto WL/WW(슬라이스/시리즈/ROI), 자동 피사체 찾기, HU 최대치 점프, 3면 MIP 뷰

> 참고: ISO/XY가 동시에 켜지면 **ISO만 적용**되어 이중 보간을 방지합니다. ROI를 지정하지 않으면 **전체 프레임**이 대상이 됩니다.

---

## 설치 & 실행

**권장**: Windows 10/11 · Python 3.10–3.12
**필수**: `pydicom`, `numpy`, `Pillow`, `tkinter`
**권장**: `SimpleITK`(NRRD/리샘플), `gdcm`(JPEG-LS 무손실)

```powershell
# (선택) PowerShell 초기화
conda init powershell

# 패키지
pip install pydicom numpy pillow SimpleITK gdcm

# 실행
python "DICOM_ROI_Cropper_FINAL.py"
```

* DLL 문제 시 최신 **VC++ 재배포 패키지** 설치 또는 `conda-forge`의 `gdcm` 사용 권장.
* UTF-8 경고(ISO\_IR\_192) 회피: pydicom 최신, 또는 시작부에 인코딩 매핑 추가(문서 하단 “문제 해결” 참조).

---

## 빠른 시작 (Quick Start)

1. **Load DICOM Folder** → **단일 프레임** 시리즈 폴더 선택.
2. 로드 직후, 앱이 **자동으로 WL/WW를 점검**하고 필요 시 조정합니다.
3. 512×512 프리뷰에서 **마우스로 ROI 드래그** → **Z Start/End** 설정.
4. 필요한 경우 **Spacing X/Y/Th**와 **HU Slope/Intercept** 확인.
5. 리샘플 선택:

   * **Isotropic 3D**: Voxel(mm)·방법 지정
   * **Resample ROI XY**: 목표 W(px)·방법 지정 *(둘 다 켜면 ISO만 적용)*
6. **Save ROI (Single)** / **Save All (Batch)** 저장.
7. 출력 폴더에 `resample_audit.csv`, 프리뷰/리포트 PNG, HU CSV가 생성됩니다.

---

## 화면 구성

* **Top Bar 1**: Load DICOM, Select ROI, Point HU, ROI HU, Save HU(Points/ROI), Show values on image
* **Top Bar 2**: Output(DICOM/NRRD), JPEG-LS, NRRD 모드(nrrd / nhdr+raw\.gz), ASCII-only path, ROI/Point 색상
* **Top Bar 3**: Spacing X/Y/Th, HU Slope/Intercept(k/b), Auto-save Preview/HU
* **Top Bar 4 (톤 컨트롤 & 자동화)**

  * Brightness · Contrast · Gamma
  * 🆕 **Auto WL/WW (slice/series)** 버튼
  * 🆕 **Auto WL from ROI** 버튼
  * 🆕 **Find Body**, **Jump Max HU**, **MIPs** 버튼
* **Z 컨트롤**: Z Start/End, Invert Z on save
* **메인**: 좌—512 프리뷰 & ROI, 우—Batch/ROI 리스트/설명 탭

**단축키(예시)**:
`F`: Find Body · `M`: Jump Max HU · `W`: Auto WL from ROI · `V`: MIPs

---

## 새로운 기능 / 변경점 (FINAL 2025-08-22)

### 표시·측정의 “원본 일치” 보장

* **HU 계산의 단일 원칙**: *항상* 슬라이스별 `RescaleSlope/Intercept` 로, \*\*원본 픽셀(`ds.pixel_array`)\*\*에서만 계산합니다.
* **Gray(원본) vs Disp(표시) 구분**:

  * **Gray** = 파일에 저장된 **원본 16-bit** 픽셀값
  * **Disp** = WL/WW(+Brightness/Contrast/Gamma) 후 **8-bit 화면값**
* **CSV 스키마 정리**: `gray_*`, `hu_*`, `disp_*`, `slope`, `intercept` 를 **슬라이스 실값**으로 저장.
* **PixelSpacing 축 매핑 수정**: `RowSpacing(첫번째)`는 높이(H), `ColSpacing(두번째)`는 너비(W)에 곱합니다.

### 시리즈 선택·안전장치

* **CT/16-bit 우선 선택**: 같은 폴더에 파생(Secondary/SC) 시리즈가 섞여 있어도 **CT·16-bit** 를 우선 채택.
* **“효과상 8-bit” 탐지**: `BitsStored>8` 이더라도 값 범위가 `0–255` 에 갇히면 **경고 로그**로 안내합니다.

  * 이 경우 **원본 16-bit CT 시리즈**를 로드해야 진짜 Gray 범위를 얻을 수 있습니다.

### 프리뷰·오버레이 일관화

* 프리뷰/풀해상도 오버레이 모두 **HU → WL/WW → 8-bit** 동일 파이프라인.
* 로드 직후 **Auto WL/WW**: 너무 어둡/밝거나 평탄하면 시리즈 기반으로 자동 조정.

### 자동 탐색 도구

* **Find Body**: 시리즈에서 `hu>-800` 몸체 면적이 가장 큰 슬라이스를 찾아 **자동 이동 + ROI 설정**.
* **Jump Max HU**: 시리즈 전체에서 **HU 최대점**으로 점프, 주변을 ROI로 자동 설정.
* **Auto WL from ROI**: 현재 ROI 분포의 퍼센타일로 **WL/WW 자동**.
* **MIPs**: Axial/Coronal/Sagittal **최대강도투영** 팝업으로 빠른 길찾기.

### 저장·산출물

* **DICOM(CT, 16-bit) 파생 저장**: 원본 픽셀 그대로(또는 ROI 크롭) 16-bit **CT Image Storage** 로 저장.

  * ROI 크롭 시 **IPP(ImagePositionPatient)** 를 오프셋만큼 보정해 기하 정합 유지.
* **Secondary Capture(옵션)**: **현재 표시 상태(8-bit)** 를 그대로 담은 SC DICOM 저장(보고용).
* **PNG 크롭 안정화**: 경계 조건에서 **빈 크롭 방지**로 `tile cannot extend outside image` 예외 예방.

---

## 리샘플(보간) 옵션

* **Isotropic 3D**: 물리 단위 voxel(mm)로 X=Y=Z 등방 리샘플, IOP가 유효하면 IPP를 재구성하여 기하 보존.
* **Resample ROI XY**: ROI의 XY만 타깃 폭(W px)에 맞춰 리샘플.
* **동시 사용 시** ISO 우선(이중 보간 방지).

---

## 출력물 & 감사 로그

* **DICOM**: JPEG-LS 무손실(가능 시) → 실패 시 무압축
* **NRRD**: `.nrrd`(SITK) → 실패 시 `.nhdr + .raw.gz`
* **HU 리포트**:

  * **Points**: `idx, slice, x, y, gray, hu, disp, slope, intercept, timestamp, …meta`
  * **ROI**: `slice, xs, ys, ws, hs, ws_mm, hs_mm, hu_mean, hu_std, … gray_*, disp_*, slope, intercept, …meta`
* **resample\_audit.csv**: 입력/출력 shape·spacing·방법·UID·비고 기록.

---

## 사용 팁 (표시와 원본의 관계)

* **HU는 하나뿐**입니다: `HU = Gray * Slope + Intercept`.
* “디스플레이 HU”라는 별개 값은 없습니다. 화면은 WL/WW로 **HU를 8-bit로 표현**할 뿐입니다.
* 레포트에는 **원본 Gray**와 **표시 Disp(8-bit)** 를 **구분 표기**하므로 혼동이 없습니다.

---

## 문제 해결(Troubleshooting)

* **프리뷰가 까맣거나 너무 밝다** → 상단의 **Auto WL/WW**(series) 사용.
* **대상을 찾기 어렵다** → **Find Body**, **Jump Max HU**, **MIPs** 활용. ROI를 잡은 다음 **Auto WL from ROI**로 미세 조정.
* **`tile cannot extend outside image` (PNG 저장 중)** → 빈 크롭 방지 로직 적용됨. 그래도 발생하면 ROI/포인트가 이미지 경계를 벗어나지 않았는지 확인.
* **경고: `Unknown encoding 'ISO_IR_192'`** → pydicom 업데이트 또는 시작부에 다음 추가:

  ```python
  from pydicom.charset import python_encoding
  python_encoding.update({'ISO_IR 192': 'utf-8', 'ISO_IR_192': 'utf-8'})
  ```
* **Gray 값이 0–255에만 있다** → 현재 시리즈가 **파생(8-bit/톤맵)일 수 있음**. **원본 16-bit CT 시리즈**만 있는 폴더로 다시 로드.
* **JPEG-LS가 안 된다** → 자동으로 무압축 폴백. 뷰어 호환성을 먼저 확인.

---

## FAQ

**Q. ROI를 지정하지 않으면?**
A. **전체 프레임**으로 처리합니다.

**Q. ISO와 XY를 동시에 켜면?**
A. **ISO만 적용**하여 이중 보간을 막습니다.

**Q. 16-bit로 저장해야 하는 이유는?**
A. 원본 픽셀을 그대로 보존하여 **HU/Gray 재현성**을 확보하기 위함입니다. 8-bit SC는 보고·공유용으로만 권장합니다.

---

## 변경 이력 (Changelog)

* **2025-08-22 (FINAL)**

  * 슬라이스별 HU 변환 고정, CSV 스키마 정리(slope/intercept 실값), PixelSpacing 축 매핑 수정
  * Auto WL/WW(slc/series/ROI), Find Body, Jump Max HU, MIPs 추가
  * CT/16-bit 우선 시리즈 선택 + “효과상 8-bit” 탐지·경고
  * 16-bit Derived CT 저장(ROI 크롭 시 IPP 보정), SC(8-bit) 옵션
  * PNG 빈 크롭 방지 및 폴리곤 통계 덮어쓰기 버그 수정
* **2025-08-16 (patched\_2)**: ISO·XY 보호, 전체 프레임 fallback, 감사 로그 정리, JPEG-LS 경로 안정화/폴백, NRRD 수동 루틴 정리
* **2025-08-14 (patched\_1)**: 초기 패치

---

## 라이선스 / 면책

본 소프트웨어는 **있는 그대로(as-is)** 제공됩니다. **연구/교육 목적** 내에서만 사용하세요. DICOM **개인정보(PHI)** 처리에 유의하십시오.

---

