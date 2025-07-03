#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GUI  v1.3  |  DICOM ▶ JPEG + tags.xlsx + IIIF manifest.json
 - Tag 사전(txt/csv/xlsx/json) → keyword 매핑
 - Canvas.metadata 모든 태그(바이너리 제외) 삽입
 - Manifest.metadata 통계 삽입
 - Excel 잠금 시 타임스탬프 새 파일 저장
 - Base URL / Image Base URL 로 Mirador 호환 ID 작성
"""

import json, pathlib, re, threading, urllib.parse
from datetime import datetime
from tkinter import (
    Tk, ttk, filedialog, messagebox,
    scrolledtext, StringVar, END, NORMAL, DISABLED
)

import numpy as np
import pandas as pd
import pydicom
from PIL import Image
from tqdm import tqdm

# ─────────────── Helper ────────────────
def window_level(arr, level=0.0, width=400.0):
    low, high = level - width / 2, level + width / 2
    arr = np.clip(arr, low, high)
    return ((arr - low) / width * 255).astype(np.uint8)

def lang_map(txt, lang="none"):
    return {lang: [str(txt)]}

TXT_RE = re.compile(
    r"\((?P<grp>[0-9A-Fa-f]{4}),(?P<elm>[0-9A-Fa-f]{4})\)\s+"
    r"(?P<vr>[A-Z]{2})\s+\d+\s+(?P<keyword>.+)"
)

def load_dict(path:str):
    """사전 → {(grp,elm):{"keyword":..,"vr":..,"desc":..}}"""
    if not path:
        return {}
    p = pathlib.Path(path)
    records={}
    try:
        if p.suffix.lower() in (".csv",".tsv"):
            df=pd.read_csv(p,sep="," if p.suffix==".csv" else "\t")
            for r in df.itertuples():
                g,e=int(r.group,16),int(r.element,16)
                records[(g,e)]={"keyword":r.keyword,"vr":r.vr,"desc":getattr(r,"description","")}
        elif p.suffix.lower() in (".xlsx",".xls"):
            df=pd.read_excel(p)
            for r in df.itertuples():
                g,e=int(r.group,16),int(r.element,16)
                records[(g,e)]={"keyword":r.keyword,"vr":r.vr,"desc":getattr(r,"description","")}
        elif p.suffix.lower()==".json":
            data=json.loads(p.read_text(encoding="utf-8"))
            for k,v in data.items():
                g,e=int(k[1:5],16),int(k[6:10],16)
                records[(g,e)]={"keyword":v["keyword"],"vr":v["vr"],"desc":v.get("description","")}
        else:  # txt
            for line in p.read_text(encoding="utf-8").splitlines():
                m=TXT_RE.match(line.strip())
                if m:
                    g,e=int(m["grp"],16),int(m["elm"],16)
                    records[(g,e)]={"keyword":m["keyword"].strip(),"vr":m["vr"].strip(),"desc":""}
    except Exception as ex:
        messagebox.showerror("사전 로드 오류",str(ex))
        return {}
    # pydicom datadict 확장
    for (g,e),meta in records.items():
        tag=(g<<16)|e
        try:
            pydicom.datadict.add_dict_entry(tag,meta["vr"],meta["keyword"],meta["desc"])
        except Exception:
            pass
    return records

def build_canvas_metadata(ds,dict_map):
    md=[]
    IGNORE_VR={"OB","OW","OF","OD","UN"}
    for elem in ds.iterall():
        if elem.tag==(0x7FE0,0x0010):  # Pixel Data
            continue
        if elem.VR in IGNORE_VR:
            continue
        g,e=elem.tag.group,elem.tag.element
        kw=dict_map.get((g,e),{}).get("keyword") or elem.keyword or f"({g:04X},{e:04X})"
        md.append({"label":lang_map(kw),
                   "value":lang_map(str(elem.value)[:1024])})
    return md

NUM_RE=re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")
def summary_stats(tag_rows):
    total=len(tag_rows)
    private=sum(1 for r in tag_rows if r["keyword"].startswith("(0013") or "(0013" in r.get("tag",""))
    hu=[]
    for r in tag_rows:
        if r["keyword"].lower() in ("mean hu","hu mean","mean hu value"):
            m=NUM_RE.search(str(r["value"]))
            if m: hu.append(float(m.group()))
    stats=[{"label":lang_map("Total Tags"),"value":lang_map(total)},
           {"label":lang_map("Private Tags"),"value":lang_map(private)}]
    if hu:
        stats+=[{"label":lang_map("HU Min"),"value":lang_map(min(hu))},
                {"label":lang_map("HU Max"),"value":lang_map(max(hu))}]
    return stats

# ─────────────── GUI ────────────────
root=Tk(); root.title("DICOM ▶ JPEG · Excel · IIIF Manifest (v1.3)")
dicom_dir,out_dir=StringVar(),StringVar()
base_url=StringVar(value="https://song-jung-il.github.io/Public_image")
image_base=StringVar(value="https://raw.githubusercontent.com/SONG-JUNG-IL/Public_image/main")
dict_file=StringVar()
manifest_path=StringVar(value="manifest.json")
excel_path=StringVar(value="tags.xlsx")
pad={"padx":4,"pady":4}

def browse(var,mode,title,ext):
    if mode=="dir":
        path=filedialog.askdirectory(title=title)
    else:
        path=filedialog.askopenfilename(title=title,filetypes=[(ext.upper(),f"*{ext}")])
    if path:
        var.set(path)
        if var is out_dir:
            ts=datetime.now().strftime("%Y%m%d_%H%M%S")
            manifest_path.set(str(pathlib.Path(path)/f"manifest_{ts}.json"))
            excel_path.set(str(pathlib.Path(path)/f"tags_{ts}.xlsx"))

def save_as(var,title,ext):
    f=filedialog.asksaveasfilename(defaultextension=ext,filetypes=[(ext.upper(),f"*{ext}")])
    if f: var.set(f)

labels=["DICOM 폴더","출력 폴더","Base URL","Image Base URL",
        "Tag Dictionary","manifest.json","tags.xlsx"]
vars  =[dicom_dir,out_dir,base_url,image_base,
        dict_file,manifest_path,excel_path]
btns  =[("찾기",lambda:browse(dicom_dir,"dir","DICOM 폴더",'')),
        ("찾기",lambda:browse(out_dir,"dir","출력 폴더",'')),
        (None,None),(None,None),
        ("찾기",lambda:browse(dict_file,"file","사전 파일",".txt")),
        ("저장",lambda:save_as(manifest_path,"manifest 저장",".json")),
        ("저장",lambda:save_as(excel_path,"Excel 저장",".xlsx"))]
for i,(lb,v,(bt,cmd)) in enumerate(zip(labels,vars,btns)):
    ttk.Label(root,text=lb).grid(row=i,column=0,sticky="w",**pad)
    ttk.Entry(root,textvariable=v,width=60).grid(row=i,column=1,**pad)
    if bt: ttk.Button(root,text=bt,command=cmd).grid(row=i,column=2,**pad)

progress=ttk.Progressbar(root,mode="indeterminate")
progress.grid(row=len(labels),column=0,columnspan=3,sticky="we",**pad)
logbox=scrolledtext.ScrolledText(root,height=12,width=90,state=DISABLED)
logbox.grid(row=len(labels)+1,column=0,columnspan=3,**pad)

def _append(msg,tag):
    logbox.config(state=NORMAL)
    if tag not in logbox.tag_names():
        logbox.tag_config(tag,foreground={"warn":"#f0ad4e","error":"#d9534f"}.get(tag,"#5bc0de"))
    logbox.insert(END,msg+"\n",tag); logbox.see(END); logbox.config(state=DISABLED)
def log(msg,t="info"): root.after(0,_append,msg,t)
pb_start=lambda:root.after(0,lambda:(progress.start(10),root.config(cursor="watch")))
pb_stop =lambda:root.after(0,lambda:(progress.stop(),root.config(cursor="")))

# ─────────────── Core convert ───────────────
def convert():
    try:
        dcm_root,path_out=pathlib.Path(dicom_dir.get()),pathlib.Path(out_dir.get())
        ts=datetime.now().strftime("%Y%m%d_%H%M%S")
        if manifest_path.get().endswith("manifest.json") or manifest_path.get()=="":
            manifest_path.set(str(path_out/f"manifest_{ts}.json"))
        if excel_path.get().endswith("tags.xlsx") or excel_path.get()=="":
            excel_path.set(str(path_out/f"tags_{ts}.xlsx"))

        if not dcm_root.is_dir():
            raise ValueError("DICOM 폴더를 지정하세요.")
        if urllib.parse.urlparse(base_url.get()).scheme not in ("http","https"):
            raise ValueError("Base URL 은 http/https 로 시작해야 합니다.")
        path_out.mkdir(parents=True,exist_ok=True)
        img_dir=path_out/"images"; img_dir.mkdir(exist_ok=True)

        dict_map=load_dict(dict_file.get())
        log(f"[info] 사전 태그 {len(dict_map)} 개 로드" if dict_map else "[info] 사전 미사용")

        tag_rows,canvases=[],[]
        files=sorted(dcm_root.rglob("*.dcm"))
        if not files: raise ValueError(".dcm 파일이 없습니다.")
        pb_start()
        for idx,dcm in enumerate(tqdm(files,desc="DICOM",unit="file"),1):
            try:
                ds=pydicom.dcmread(dcm,force=True)
                arr = ds.pixel_array if "PixelData" in ds else None
                if arr is not None:
                    if ds.get("Modality")=="CT":
                        hu=arr*float(ds.get("RescaleSlope",1))+float(ds.get("RescaleIntercept",0))
                        arr8=window_level(hu)
                    else:
                        denom=np.ptp(arr) or 1
                        arr8=((arr-arr.min())/denom*255).astype(np.uint8)
                    jpg_name=f"{dcm.stem}.jpg"
                    Image.fromarray(arr8).convert("L").save(img_dir/jpg_name,quality=90)
                    h,w=arr8.shape
                else:
                    jpg_name=""; h=w=0

                md=build_canvas_metadata(ds,dict_map)
                for m in md:
                    tag_rows.append({"file":dcm.name,
                                     "keyword":m["label"]["none"][0],
                                     "value":m["value"]["none"][0],"tag":""})

                body_id=f"{image_base.get().rstrip('/')}/{jpg_name}" if image_base.get() else f"{base_url.get().rstrip('/')}/images/{jpg_name}"
                canvases.append({
                    "id":f"{base_url.get().rstrip('/')}/canvas/{idx}",
                    "type":"Canvas","height":h,"width":w,
                    "items":[{
                        "id":f"{base_url.get().rstrip('/')}/canvas/{idx}",
                        "type":"AnnotationPage",
                        "items":[{
                            "id":f"{base_url.get().rstrip('/')}/canvas/{idx}",
                            "type":"Annotation","motivation":"painting",
                            "body":{"id":body_id,"type":"Image","format":"image/jpeg",
                                    "height":h,"width":w},
                            "target":f"{base_url.get().rstrip('/')}/canvas/{idx}"
                        }]
                    }],"metadata":md})

            except Exception as w: log(f"[warn] {dcm.name}: {w}","warn")

        manifest_url=f"{base_url.get().rstrip('/')}/{pathlib.Path(manifest_path.get()).name}"
        manifest={
            "@context":"https://iiif.io/api/presentation/3/context.json",
            "id":manifest_url,
            "type":"Manifest",
            "label":lang_map("DICOCH DICOM Study","en"),
            "summary":lang_map(datetime.now().isoformat(),"en"),
            "items":canvases,
            "metadata":summary_stats(tag_rows)
        }
        pathlib.Path(manifest_path.get()).write_text(
            json.dumps(manifest,indent=2,ensure_ascii=False),encoding="utf-8")
        log(f"[ok] manifest → {manifest_path.get()}")

        df=pd.DataFrame(tag_rows)
        try:
            df.to_excel(excel_path.get(),index=False)
            log(f"[ok] tags.xlsx → {excel_path.get()}")
        except PermissionError:
            alt=excel_path.with_stem(excel_path.stem+f"_{ts}")
            df.to_excel(alt,index=False)
            log(f"[warn] 파일 잠김, 새 파일 → {alt}","warn")

        log(f"[ok] JPEG 폴더 → {img_dir}")
    except Exception as e:
        log(f"[error] {e}","error"); messagebox.showerror("Error",str(e))
    finally:
        pb_stop()

ttk.Button(root,text="실행",command=lambda:threading.Thread(target=convert,daemon=True).start()
           ).grid(row=len(labels)+2,column=0,columnspan=3,**pad)
root.mainloop()
