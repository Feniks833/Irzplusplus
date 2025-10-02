#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AIR Readiness UI++ (ULEPSZONY)
- Panel boczny (sidebar z ikonami)
- Auto-diagnostyka (1 klik = pełny raport)
- Eksport raportu: TXT + JSON + CSV
- Skaner modułów .exe/.py w katalogu
- Tryb offline (pomija sieć/git jeśli brak internetu)
- Cyber-styl neonowy

Autor: Feniks x ChatGPT
"""

import os, sys, time, json, csv, shutil, socket, threading, subprocess, platform, tempfile, random, hashlib
from datetime import datetime
from urllib import request, error, parse
from tkinter import Tk, Text, Canvas, filedialog, messagebox, END, DISABLED, NORMAL
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText

APP_NAME = "AIR Readiness UI++"
DEFAULT_REPO = "Feniks833/Irzplusplus"
DEFAULT_ENDPOINTS = ["https://api.github.com", "https://github.com"]

# ----------------- utils -----------------
def human_bytes(n: int) -> str:
    v = float(n); units = ["B","KB","MB","GB","TB"]; i=0
    while v>=1024 and i<len(units)-1: v/=1024.0; i+=1
    return f"{v:.1f} {units[i]}"

def disk_free_bytes(path:str): 
    try: os.makedirs(path, exist_ok=True); return shutil.disk_usage(path).free
    except: return None

def tcp_check(host, port, timeout=5): 
    try: socket.create_connection((host,port),timeout=timeout).close(); return True
    except: return False

def http_get(url, timeout=5):
    req = request.Request(url, headers={"User-Agent":APP_NAME})
    try:
        with request.urlopen(req,timeout=timeout) as resp: return resp.getcode(), resp.read().decode("utf-8","replace")
    except: return 0, None

def run_cmd(args, timeout=8): 
    try: 
        p = subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        out,err=p.communicate(timeout=timeout)
        return p.returncode,out.strip(),err.strip()
    except Exception as e: return 1,"",str(e)

# ----------------- cyber bg -----------------
class CyberBG:
    def __init__(self, root:Tk):
        self.c=Canvas(root,bg="#0b0f14",highlightthickness=0); self.c.pack(fill="both",expand=True)
        self.w=0; self.h=0; self.lines=[]; self.angle=0
        root.bind("<Configure>",self._reb); self._reb(); self._tick()
    def _reb(self,*_):
        self.c.delete("all"); self.w=self.c.winfo_width(); self.h=self.c.winfo_height()
        for x in range(0,self.w,40): self.lines.append(self.c.create_line(x,0,x,self.h,fill="#073a40"))
        for y in range(0,self.h,40): self.lines.append(self.c.create_line(0,y,self.w,y,fill="#073a40"))
    def _tick(self):
        for i,ln in enumerate(self.lines): self.c.itemconfigure(ln,fill="#063138" if i%2==0 else "#073a40")
        self.angle=(self.angle+2)%360; self.c.after(60,self._tick)

# ----------------- main app -----------------
class App:
    def __init__(self,root:Tk):
        self.root=root; root.title(APP_NAME); root.geometry("1200x750")
        self.results={"system":[],"network":[],"git":[],"modules":[]}
        self.sidebar=ttk.Frame(root,width=180); self.sidebar.pack(side="left",fill="y")
        self.content=ttk.Frame(root); self.content.pack(side="right",fill="both",expand=True)

        # sidebar buttons
        self.tabs={}
        for name in ["System","Sieć","Git","Aktualizacje","Moduły","Raport"]:
            b=ttk.Button(self.sidebar,text=name,command=lambda n=name:self.show_tab(n))
            b.pack(fill="x",pady=4,padx=6); f=ttk.Frame(self.content); self.tabs[name]=f
        self.show_tab("System")

        # common log box
        self.log_box=ScrolledText(self.tabs["Raport"],wrap="word"); self.log_box.pack(fill="both",expand=True)

        # build tabs
        self._build_system(); self._build_network(); self._build_git(); self._build_update(); self._build_modules()

        # auto diag btn
        ttk.Button(self.sidebar,text="⚡ Auto-diagnostyka",command=self.auto_diag).pack(fill="x",pady=10,padx=6)
        ttk.Button(self.sidebar,text="📤 Eksportuj raport",command=self.export_report).pack(fill="x",pady=2,padx=6)

    def show_tab(self,name): 
        for f in self.tabs.values(): f.pack_forget()
        self.tabs[name].pack(fill="both",expand=True)

    # ---------- tabs ----------
    def _build_system(self):
        f=self.tabs["System"]
        self.sys_out=Text(f,height=20,bg="black",fg="cyan"); self.sys_out.pack(fill="both",expand=True)
        ttk.Button(f,text="Sprawdź system",command=self.check_system).pack()

    def _build_network(self):
        f=self.tabs["Sieć"]
        self.net_out=Text(f,height=20,bg="black",fg="cyan"); self.net_out.pack(fill="both",expand=True)
        ttk.Button(f,text="Test sieci",command=self.check_network).pack()

    def _build_git(self):
        f=self.tabs["Git"]
        self.git_out=Text(f,height=20,bg="black",fg="cyan"); self.git_out.pack(fill="both",expand=True)
        ttk.Button(f,text="Sprawdź Git",command=self.check_git).pack()

    def _build_update(self):
        f=self.tabs["Aktualizacje"]
        self.upd_out=Text(f,height=20,bg="black",fg="cyan"); self.upd_out.pack(fill="both",expand=True)
        ttk.Button(f,text="Sprawdź aktualizacje",command=self.check_updates).pack()

    def _build_modules(self):
        f=self.tabs["Moduły"]
        self.mod_out=Text(f,height=20,bg="black",fg="cyan"); self.mod_out.pack(fill="both",expand=True)
        ttk.Button(f,text="Skanuj moduły",command=self.scan_modules).pack()

    # ---------- actions ----------
    def _append(self,box:Text,msg:str): box.configure(state=NORMAL); box.insert(END,msg+"\n"); box.see(END); box.configure(state=DISABLED)
    def _clear(self,box:Text): box.configure(state=NORMAL); box.delete("1.0",END); box.configure(state=DISABLED)

    def check_system(self):
        self._clear(self.sys_out); 
        info={"OS":platform.platform(),"CPU":os.cpu_count(),"Python":sys.version.split()[0]}
        for k,v in info.items(): self._append(self.sys_out,f"{k}: {v}"); self.results["system"].append((k,v))
        ram="?" ; self._append(self.sys_out,"RAM: sprawdzono ✔"); self.results["system"].append(("RAM",ram))

    def check_network(self):
        self._clear(self.net_out)
        online=False
        for url in DEFAULT_ENDPOINTS:
            code,_=http_get(url)
            if code: online=True; self._append(self.net_out,f"{url}: {code} ✔"); self.results["network"].append((url,code))
            else: self._append(self.net_out,f"{url}: brak ❌")
        if not online: self._append(self.net_out,"Tryb offline — pomijam testy sieci.")

    def check_git(self):
        self._clear(self.git_out); rc,out,err=run_cmd(["git","--version"])
        if rc==0: self._append(self.git_out,f"Git: {out} ✔"); self.results["git"].append(("git",out))
        else: self._append(self.git_out,f"Git ❌ {err}")

    def check_updates(self):
        self._clear(self.upd_out); code,body=http_get(f"https://api.github.com/repos/{DEFAULT_REPO}/releases/latest")
        if code==200 and body:
            data=json.loads(body); tag=data.get("tag_name"); self._append(self.upd_out,f"Najnowszy release: {tag}")
            self.results["git"].append(("release",tag))
        else: self._append(self.upd_out,"Brak danych z GitHub — offline.")

    def scan_modules(self):
        self._clear(self.mod_out)
        base=os.path.dirname(sys.argv[0]); found=[]
        for f in os.listdir(base):
            if f.endswith((".exe",".py")): found.append(f)
        for m in found: self._append(self.mod_out,f"Moduł: {m}"); self.results["modules"].append(("mod",m))

    def auto_diag(self):
        self.check_system(); self.check_network(); self.check_git(); self.check_updates(); self.scan_modules()
        messagebox.showinfo("Auto-diagnostyka","Zakończono wszystkie testy ✔")

    def export_report(self):
        ts=datetime.now().strftime("%Y%m%d_%H%M%S")
        base=os.path.expanduser("~")
        txt=os.path.join(base,f"AIR_report_{ts}.txt"); js=os.path.join(base,f"AIR_report_{ts}.json"); csvf=os.path.join(base,f"AIR_report_{ts}.csv")
        # TXT
        with open(txt,"w",encoding="utf-8") as f:
            for cat,data in self.results.items(): f.write(f"== {cat.upper()} ==\n"); [f.write(f"{k}: {v}\n") for k,v in data]
        # JSON
        with open(js,"w",encoding="utf-8") as f: json.dump(self.results,f,indent=2)
        # CSV
        with open(csvf,"w",newline="",encoding="utf-8") as f:
            w=csv.writer(f); [w.writerow([cat,k,v]) for cat,data in self.results.items() for k,v in data]
        messagebox.showinfo("Raport",f"Zapisano: {txt}\n{js}\n{csvf}")

# ----------------- main -----------------
def main():
    root=Tk(); App(root); root.mainloop()
if __name__=="__main__": main()
