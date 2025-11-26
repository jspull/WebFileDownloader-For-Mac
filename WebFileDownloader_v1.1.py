# --- 1. 필요한 도구들을 컴퓨터 메모리로 불러옵니다 ---
import tkinter as tk
from tkinter import scrolledtext, ttk
from tkinter import filedialog, messagebox
from playwright.sync_api import sync_playwright
import threading
import time
import os
import subprocess
from queue import Queue
import re
# import pathlib (삭제됨)

# --- 2. 전역 변수 및 큐 설정 ---
log_queue = Queue()
detected_files_queue = Queue()
detected_files = {} # { "URL": "최종 파일명" } 형태로 저장

# --- 3. UI 및 파일 관리 기능 정의 ---

def log_message(message):
    """로그 큐에 메시지를 추가합니다."""
    log_queue.put(message)

def get_sound_extensions():
    """설정 창에서 사운드 확장자 목록을 가져옵니다."""
    ext_text = ext_entry.get().strip()
    if not ext_text:
        return []
    extensions = re.split(r'[,\s\n]+', ext_text)
    return [f".{ext.strip().lstrip('.')}" for ext in extensions if ext.strip()]

def toggle_checkbox(event):
    """감지된 파일 목록의 체크박스를 토글합니다."""
    try:
        line_start = detected_files_list.index(f"@{event.x},{event.y} linestart")
        line_end = detected_files_list.index(f"{line_start} lineend")
        line_text = detected_files_list.get(line_start, line_end)
        
        if not line_text.strip(): return
        
        detected_files_list.config(state="normal")
        if line_text.startswith('[ ]'):
            detected_files_list.delete(line_start, f"{line_start} + 3 chars")
            detected_files_list.insert(line_start, '[X]')
        elif line_text.startswith('[X]'):
            detected_files_list.delete(line_start, f"{line_start} + 3 chars")
            detected_files_list.insert(line_start, '[ ]')
        detected_files_list.config(state="disabled")
    except Exception as e:
        log_message(f"체크박스 토글 오류: {e}")

# --- 4. 자동화 로직 (맥 전용으로 수정됨) ---

def launch_chrome_in_debug_mode(start_url):
    """디버깅 모드로 크롬을 실행합니다. (macOS 전용)"""
    log_message("시스템 브라우저(Chrome) 실행 시도... (macOS)")
    
    # (수정!) 맥용 크롬 경로 설정
    chrome_paths = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        os.path.expanduser("~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    ]
    
    chrome_path = next((path for path in chrome_paths if os.path.exists(path)), None)
    
    if not chrome_path:
        log_message("오류: Mac에서 Google Chrome을 찾을 수 없습니다.")
        return False
        
    # (수정!) 맥에서는 사용자 홈 디렉토리에 프로필을 만드는 것이 안전함
    profile_path = os.path.join(os.path.expanduser("~"), "WebFileDownloader_Profile")
    os.makedirs(profile_path, exist_ok=True)
    
    # (수정!) 윈도우 전용 플래그(creationflags) 제거
    try:
        subprocess.Popen(
            [chrome_path, f"--user-data-dir={profile_path}", "--remote-debugging-port=9222", start_url]
        )
        log_message(f"자동화 전용 크롬 브라우저 실행: {start_url}")
        return True
    except Exception as e:
        log_message(f"크롬 실행 실패: {e}")
        return False

def start_monitoring():
    """모니터링을 시작합니다."""
    target_url = url_entry.get()
    if not target_url.startswith("http"):
        messagebox.showerror("오류", "올바른 웹 주소(http... 형식)를 입력해주세요."); return
    
    # 우회 로직 없이 바로 대상 URL 사용
    start_url_for_chrome = target_url
    
    log_message("모니터링 시작")

    global detected_files
    detected_files = {}
    detected_files_list.config(state="normal")
    detected_files_list.delete('1.0', tk.END)
    detected_files_list.config(state="disabled")
    
    start_button.config(state="disabled", text="모니터링 중")
    download_button.config(state="normal") 
    
    threading.Thread(target=run_monitoring, args=(start_url_for_chrome,), daemon=True).start()

def run_monitoring(start_url):
    """Playwright를 실행하고 네트워크 응답을 감지합니다."""
    if not launch_chrome_in_debug_mode(start_url):
        start_button.config(state="normal", text="모니터링 시작")
        return

    browser = None
    max_retries = 30
    retry_delay = 0.5
    
    try:
        with sync_playwright() as p:
            for attempt in range(max_retries):
                try:
                    log_message(f"브라우저 연결 시도... ({attempt + 1}/{max_retries})")
                    browser = p.chromium.connect_over_cdp("http://localhost:9222", timeout=1000)
                    log_message("브라우저 연결 성공!"); break
                except Exception:
                    if attempt == max_retries - 1: raise
                    time.sleep(retry_delay)
            
            context = browser.contexts[0]
            
            def clear_detected_list(page_object):
                log_message("페이지 로드 감지. 감지 목록 초기화.")
                log_queue.put("CLEAR_DETECTED_LIST")
            
            def setup_page_events(page):
                page.on("load", clear_detected_list)
            
            context.on("page", setup_page_events)
            for page in context.pages:
                setup_page_events(page)

            # --- 응답 헤더 검사 로직 ---
            def handle_response(response):
                global detected_files
                request_url = response.url
                
                if request_url in detected_files:
                    return

                try:
                    headers = response.headers
                    content_type = headers.get('content-type', '').lower()
                    
                    clean_url = request_url.split('?')[0]
                    filename = os.path.basename(clean_url)
                    if not filename:
                        filename = clean_url.rstrip('/').split('/')[-1]
                except Exception:
                    filename = ""
                
                final_filename = filename

                if 'webm' in content_type and filename and not filename.lower().endswith('.webm'):
                    final_filename = f"{filename}.webm"
                    log_message(f"Content-Type 감지: {filename} -> {final_filename}")
                
                if final_filename and "favicon" not in final_filename:
                    log_message(f"감지: {final_filename}")
                    detected_files[request_url] = final_filename 
                    detected_files_queue.put((request_url, final_filename))

            context.on("response", handle_response)
            log_message("네트워크 응답 감지 규칙 설정 완료.")
            
            context.wait_for_event('close', timeout=0)
            
    except Exception as e:
        if "closed" not in str(e):
            log_message(f"브라우저 연결 또는 작업 중 오류 발생: {e}")
    finally:
        log_message("자동화 세션 종료.");
        start_button.config(state="normal", text="모니터링 시작")
        download_button.config(state="disabled")

# --- 5. 다운로드 로직 ---

def start_download_thread():
    """다운로드 버튼 클릭 시 호출됩니다."""
    
    lines = detected_files_list.get('1.0', tk.END).strip().split('\n')
    checked_filenames = {line[4:].strip() for line in lines if line.startswith('[X]')}
    
    if not checked_filenames:
        messagebox.showinfo("알림", "다운로드할 파일이 체크되지 않았습니다.")
        return

    urls_and_names_to_download = []
    for url, filename in detected_files.items():
        if filename in checked_filenames:
            urls_and_names_to_download.append((url, filename))
            checked_filenames.remove(filename) 
            if not checked_filenames:
                break
    
    if not urls_and_names_to_download:
        messagebox.showerror("오류", "체크된 파일의 URL을 찾을 수 없습니다.")
        return

    download_dir = filedialog.askdirectory(title="저장할 폴더를 선택하세요")
    if not download_dir:
        log_message("다운로드를 취소했습니다."); return

    save_path = os.path.join(download_dir, f"download_{time.strftime('%Y%m%d-%H%M%S')}")
    os.makedirs(save_path, exist_ok=True)
    log_message(f"다운로드 폴더 생성: {save_path}")

    download_button.config(state="disabled")
    progress_label.pack(side="top", fill='x', padx=10)
    progress_bar.pack(side="top", pady=(0,10), padx=10, fill='x')
    progress_bar.start(10)
    
    threading.Thread(target=run_download, args=(urls_and_names_to_download, save_path), daemon=True).start()

def get_unique_filepath(directory, filename):
    """중복되지 않는 파일 경로를 반환합니다."""
    base, ext = os.path.splitext(filename)
    counter = 1
    filepath = os.path.join(directory, filename)
    while os.path.exists(filepath):
        filepath = os.path.join(directory, f"{base}_{counter}{ext}")
        counter += 1
    return filepath

def run_download(urls_and_names_to_download, save_path):
    """실제 파일 다운로드를 수행하는 스레드 함수입니다."""
    log_message(f"총 {len(urls_and_names_to_download)}개의 파일 다운로드 시작...")
    
    try:
        with sync_playwright() as p:
            api_request_context = p.request.new_context(ignore_https_errors=True)
            
            for i, (url, filename) in enumerate(urls_and_names_to_download):
                    
                log_queue.put(f"P_STATUS:[ {i+1} / {len(urls_and_names_to_download)} ] 다운로드 중: {filename}")
                try:
                    response = api_request_context.get(url, timeout=30000)
                    
                    file_path = get_unique_filepath(save_path, filename)
                    
                    with open(file_path, 'wb') as f:
                        f.write(response.body())
                    log_queue.put(f"SUCCESS:{filename}")
                        
                except Exception as e:
                    log_message(f"-> 다운로드 실패: {filename} (오류: {e})")
                    
    except Exception as e:
        log_message(f"다운로드 작업 중 오류 발생: {e}")
    finally:
        log_message("--- 다운로드 완료 ---")
        log_queue.put("DOWNLOAD_COMPLETE")


# --- 6. 프로그램 화면 구성 (UI) ---

def process_queues():
    """메인 스레드에서 큐를 처리하여 UI를 업데이트합니다."""
    global detected_files
    
    while not log_queue.empty():
        message = log_queue.get_nowait()
        
        if message == "CLEAR_DETECTED_LIST":
            detected_files_list.config(state="normal")
            detected_files_list.delete('1.0', tk.END)
            detected_files_list.config(state="disabled")
            detected_files = {} 
        elif message.startswith("SUCCESS:"):
            filename = message.split(":", 1)[1]
            log_text.config(state="normal")
            log_text.insert(tk.END, f"[다운로드 성공] {filename}\n")
            log_text.see(tk.END); log_text.config(state="disabled")
        elif message.startswith("P_STATUS:"):
            status_text = message.split(":", 1)[1]
            progress_label.config(text=status_text)
        elif message == "DOWNLOAD_COMPLETE":
            progress_bar.stop()
            progress_bar.pack_forget()
            progress_label.pack_forget()
            download_button.config(state="normal")
            messagebox.showinfo("완료", "다운로드가 완료되었습니다.")
        else:
            log_text.config(state="normal")
            log_text.insert(tk.END, message + "\n")
            log_text.see(tk.END); log_text.config(state="disabled")

    sound_extensions = get_sound_extensions()
    
    while not detected_files_queue.empty():
        url, filename = detected_files_queue.get_nowait()
        
        is_sound = any(filename.lower().endswith(ext) for ext in sound_extensions)
        checkbox = '[X]' if is_sound else '[ ]'
        
        detected_files_list.config(state="normal")
        detected_files_list.insert(tk.END, f"{checkbox} {filename}\n")
        detected_files_list.see(tk.END)
        detected_files_list.config(state="disabled")

    root.after(100, process_queues)

# --- UI 레이아웃 ---
root = tk.Tk()
root.title("Web File Downloader_v1.1 - Created by 이재성")
root.geometry("800x640")

# --- 상단 프레임 (URL, 버튼) ---
top_frame = tk.Frame(root)
top_frame.pack(pady=10, padx=10, fill="x")

url_label = tk.Label(top_frame, text="시작할 웹 주소:"); url_label.pack(anchor='w')
url_entry = tk.Entry(top_frame); url_entry.pack(fill='x', pady=2)
url_entry.insert(0, "https://www.google.com/")

control_frame = tk.Frame(top_frame)
control_frame.pack(fill='x', pady=5)
start_button = tk.Button(control_frame, text="모니터링 시작", command=start_monitoring, font=("", 10, "bold"))
start_button.pack(side="left", padx=5, fill='x', expand=True)
download_button = tk.Button(control_frame, text="체크된 파일 다운로드", command=start_download_thread, font=("", 10, "bold"), state="disabled")
download_button.pack(side="left", padx=5, fill='x', expand=True)

# (우회 기능 체크박스 제거됨)

# --- 확장자 설정 프레임 ---
settings_frame = tk.LabelFrame(root, text="자동 체크 확장자 설정", padx=5, pady=5)
settings_frame.pack(pady=5, padx=10, fill="x")

ext_label = tk.Label(settings_frame, text="확장자 (쉼표, 공백으로 구분):")
ext_label.pack(side="left", padx=(0, 5))

ext_entry = tk.Entry(settings_frame) 
ext_entry.pack(side="left", fill="x", expand=True)
ext_entry.insert(0, ".png, .jpeg") 

# --- 진행률 표시 (다운로드 시 보임) ---
progress_label = tk.Label(root, text="작업 진행 중...")
progress_bar = ttk.Progressbar(root, orient="horizontal", mode="indeterminate")

# --- 메인 컨텐츠 프레임 (좌/우 분할) ---
main_content_frame = tk.Frame(root)
main_content_frame.pack(pady=(5, 10), padx=10, fill="both", expand=True)

paned_window = ttk.PanedWindow(main_content_frame, orient=tk.HORIZONTAL)
paned_window.pack(fill="both", expand=True)

# 좌측: 감지된 파일 목록
detection_frame = tk.LabelFrame(paned_window, text="감지된 파일 목록 (클릭하여 체크)", padx=5, pady=5)
paned_window.add(detection_frame, weight=1) 

detected_files_list = scrolledtext.ScrolledText(detection_frame, wrap=tk.WORD, height=10)
detected_files_list.pack(fill="both", expand=True)
detected_files_list.bind("<Button-1>", toggle_checkbox)
detected_files_list.config(state="disabled")

# 우측: 작업 현황판 (로그)
log_frame = tk.LabelFrame(paned_window, text="작업 현황판", padx=5, pady=5)
paned_window.add(log_frame, weight=1) 

log_text = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD, state="disabled")
log_text.pack(fill="both", expand=True)

# --- 메인 루프 시작 ---
process_queues()
root.mainloop()