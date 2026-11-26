import os
import sys
import json
import time
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# определяем папку, откуда запущен скрипт, чтобы пути не ломались
APP_DIR = os.path.dirname(os.path.abspath(__file__))
CFG_FILE = os.path.join(APP_DIR, "gui_config.json")


class AnonymizerGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("CIM XML Anonymizer — Графический интерфейс")
        self.geometry("880x670")
        self.minsize(760, 560)

        # пробуем подгрузить прошлые настройки из json
        saved_cfg = self.load_config()

        # переменные для привязки к полям ввода в tkinter
        self.input_xml = tk.StringVar(value=saved_cfg.get("src", os.path.join(APP_DIR, "practice", "input.xml")))
        self.output_xml = tk.StringVar(value=saved_cfg.get("dst", os.path.join(APP_DIR, "practice", "out_gui.xml")))
        self.db_path = tk.StringVar(value=saved_cfg.get("vault", os.path.join(APP_DIR, "practice", "vault.sqlite")))
        self.secret_key = tk.StringVar(value=saved_cfg.get("secret", "учебный-секрет"))
        self.gen_lang = tk.StringVar(value=saved_cfg.get("lang", "ru"))
        self.is_secret_visible = tk.BooleanVar(value=False)

        self.busy = False  # флаг, чтобы не запустить два скрипта одновременно
        self.btns_list = []  # сюда сложим кнопки, которые надо блокировать во время работы

        self.setup_ui()
        # перехватываем закрытие окна через крестик, чтобы сохранить пути
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def load_config(self) -> dict:
        if os.path.exists(CFG_FILE):
            try:
                with open(CFG_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                # если файл битый — просто игнорируем и берем дефолты
                pass
        return {}

    def save_config(self):
        data = {
            "src": self.input_xml.get().strip(),
            "dst": self.output_xml.get().strip(),
            "vault": self.db_path.get().strip(),
            "secret": self.secret_key.get(),
            "lang": self.gen_lang.get()
        }
        try:
            with open(CFG_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def on_close(self):
        self.save_config()
        self.destroy()

    def setup_ui(self):
        pad = {"padx": 10, "pady": 5}

        # --- Верхний блок с путями и параметрами ---
        files_box = ttk.LabelFrame(self, text="Параметры выгрузки", padding=10)
        files_box.pack(fill="x", **pad)
        files_box.columnconfigure(1, weight=1)  # чтобы поля ввода растягивались по ширине

        ttk.Label(files_box, text="Исходный XML:").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(files_box, textvariable=self.input_xml).grid(row=0, column=1, sticky="ew", padx=6, pady=4)
        ttk.Button(files_box, text="Обзор...", command=self.pick_src).grid(row=0, column=2, pady=4)

        ttk.Label(files_box, text="Результат XML:").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(files_box, textvariable=self.output_xml).grid(row=1, column=1, sticky="ew", padx=6, pady=4)
        ttk.Button(files_box, text="Сохранить как...", command=self.pick_dst).grid(row=1, column=2, pady=4)

        ttk.Label(files_box, text="Хранилище (.sqlite):").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(files_box, textvariable=self.db_path).grid(row=2, column=1, sticky="ew", padx=6, pady=4)
        ttk.Button(files_box, text="Выбрать...", command=self.pick_vault).grid(row=2, column=2, pady=4)

        # поле для пароля со звездочками и галочкой "показать"
        ttk.Label(files_box, text="Секретное слово:").grid(row=3, column=0, sticky="w", pady=4)
        self.secret_input = ttk.Entry(files_box, textvariable=self.secret_key, show="*")
        self.secret_input.grid(row=3, column=1, sticky="ew", padx=6, pady=4)
        ttk.Checkbutton(
            files_box,
            text="Показать",
            variable=self.is_secret_visible,
            command=self.toggle_secret
        ).grid(row=3, column=2, sticky="w", pady=4)

        # выбор языка фейковых данных
        ttk.Label(files_box, text="Язык генерации:").grid(row=4, column=0, sticky="w", pady=4)
        lang_frame = ttk.Frame(files_box)
        lang_frame.grid(row=4, column=1, sticky="w", padx=6, pady=4)
        ttk.Radiobutton(lang_frame, text="Русский (ru)", variable=self.gen_lang, value="ru").pack(side="left", padx=(0, 12))
        ttk.Radiobutton(lang_frame, text="Транслит (en)", variable=self.gen_lang, value="en").pack(side="left")

        # --- Основные кнопки запуска ---
        actions_frame = ttk.Frame(self)
        actions_frame.pack(fill="x", padx=10, pady=4)

        self.btn_start = ttk.Button(
            actions_frame,
            text="Запустить анонимизацию",
            command=self.run_anonymize
        )
        self.btn_start.pack(side="left", padx=(0, 8), ipady=4, ipadx=10)
        self.btns_list.append(self.btn_start)

        self.btn_verify = ttk.Button(
            actions_frame,
            text="Проверить результат (check.py)",
            command=self.run_check
        )
        self.btn_verify.pack(side="left", padx=(0, 8), ipady=4, ipadx=8)
        self.btns_list.append(self.btn_verify)

        # --- Дополнительные утилиты ---
        utils_box = ttk.LabelFrame(self, text="Утилиты и проверка", padding=8)
        utils_box.pack(fill="x", **pad)

        btn_db = ttk.Button(utils_box, text="Просмотр хранилища", command=self.run_show_vault)
        btn_db.pack(side="left", padx=(0, 6))
        self.btns_list.append(btn_db)

        btn_autotests = ttk.Button(utils_box, text="Автотесты", command=self.run_tests)
        btn_autotests.pack(side="left", padx=(0, 6))
        self.btns_list.append(btn_autotests)

        # кнопку очистки не блокируем, она ничему не мешает
        ttk.Button(utils_box, text="Очистить лог", command=self.clear_log).pack(side="right")

        # --- Окно вывода логов (сделал темным, как консоль) ---
        console_box = ttk.LabelFrame(self, text="Журнал выполнения", padding=8)
        console_box.pack(fill="both", expand=True, **pad)

        self.console_text = tk.Text(
            console_box,
            wrap="word",
            font=("Consolas", 10),
            bg="#1e1e1e",
            fg="#e0e0e0",
            insertbackground="white",
            state="disabled"  # запрещаем юзеру печатать руками в лог
        )
        scroll = ttk.Scrollbar(console_box, command=self.console_text.yview)
        self.console_text.configure(yscrollcommand=scroll.set)

        self.console_text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        # --- Нижняя строка состояния ---
        self.status_text = tk.StringVar(value="Готов к работе")
        status_lbl = ttk.Label(self, textvariable=self.status_text, relief="sunken", anchor="w", padding=(8, 3))
        status_lbl.pack(fill="x", side="bottom")

    def toggle_buttons(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        for btn in self.btns_list:
            btn.configure(state=state)

    def toggle_secret(self):
        # убираем или возвращаем маскировку звездочками
        self.secret_input.configure(show="" if self.is_secret_visible.get() else "*")

    def pick_src(self):
        selected = filedialog.askopenfilename(
            title="Выберите исходный XML-файл",
            filetypes=[("XML файлы", "*.xml"), ("Все файлы", "*.*")]
        )
        if selected:
            self.input_xml.set(selected)
            # автоматически подставляем имя выходного файла с припиской _anon
            name, ext = os.path.splitext(selected)
            self.output_xml.set(f"{name}_anon{ext or '.xml'}")
            self.save_config()

    def pick_dst(self):
        selected = filedialog.asksaveasfilename(
            title="Сохранить обезличенный XML как",
            defaultextension=".xml",
            filetypes=[("XML файлы", "*.xml"), ("Все файлы", "*.*")]
        )
        if selected:
            self.output_xml.set(selected)
            self.save_config()

    def pick_vault(self):
        selected = filedialog.asksaveasfilename(
            title="Выберите или создайте файл хранилища SQLite",
            defaultextension=".sqlite",
            confirmoverwrite=False,  # не ругаемся, если файл уже есть, мы в него дописываем
            filetypes=[("SQLite база", "*.sqlite *.db"), ("Все файлы", "*.*")]
        )
        if selected:
            self.db_path.set(selected)
            self.save_config()

    def write_log(self, msg: str):
        # чтобы записать текст в disabled виджет, его надо временно разблокировать
        self.console_text.configure(state="normal")
        self.console_text.insert("end", msg)
        self.console_text.see("end")  # автоскролл вниз
        self.console_text.configure(state="disabled")

    def clear_log(self):
        self.console_text.configure(state="normal")
        self.console_text.delete("1.0", "end")
        self.console_text.configure(state="disabled")

    def cleanup_stale_wal(self, vault_file: str):
        # если базу удалили вручную, могут остаться временные файлы WAL/SHM — чистим их
        if not os.path.exists(vault_file):
            for ext in ("-wal", "-shm"):
                temp_file = vault_file + ext
                if os.path.exists(temp_file):
                    try:
                        os.remove(temp_file)
                    except OSError:
                        pass

    def run_script(self, rel_script: str, cli_args: list, task_title: str, on_success=None):
        if self.busy:
            return
        full_script_path = os.path.join(APP_DIR, rel_script)
        if not os.path.exists(full_script_path):
            messagebox.showerror("Файл не найден", f"Не найден скрипт:\n{full_script_path}")
            return

        self.save_config()
        self.busy = True
        self.toggle_buttons(False)
        self.status_text.set(f"Выполняется: {task_title}...")
        self.write_log(f"\n{'=' * 64}\n {task_title}\n{'=' * 64}\n")

        # запускаем внешний скрипт в отдельном потоке, чтобы интерфейс не завис
        def worker():
            start_time = time.perf_counter()
            custom_env = os.environ.copy()
            custom_env["PYTHONIOENCODING"] = "utf-8"
            custom_env["PYTHONUNBUFFERED"] = "1"  # отключаем буферизацию вывода, чтобы логи шли сразу
            cmd = [sys.executable, "-u", full_script_path] + cli_args

            # на винде прячем всплывающее черное окно консоли
            s_info = None
            c_flags = 0
            if os.name == "nt":
                c_flags = subprocess.CREATE_NO_WINDOW
                s_info = subprocess.STARTUPINFO()
                s_info.dwFlags |= subprocess.STARTF_USESHOWWINDOW

            try:
                process = subprocess.Popen(
                    cmd,
                    cwd=APP_DIR,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,  # объединяем ошибки и обычный вывод в один поток
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    env=custom_env,
                    startupinfo=s_info,
                    creationflags=c_flags
                )
                lines_buffer = []
                for line in process.stdout:
                    lines_buffer.append(line)
                    # через self.after безопасно обновляем GUI из фонового потока
                    self.after(0, self.write_log, line)

                exit_code = process.wait()
                duration = time.perf_counter() - start_time

                if exit_code == 0:
                    if on_success:
                        self.after(0, on_success, duration)
                    status_msg = f"Завершено: {task_title} | Время: {duration:.3f} с | Статус: Успешно"
                else:
                    if not lines_buffer:
                        self.after(0, self.write_log, "(Скрипт завершился с ошибкой без вывода в консоль)\n")
                    status_msg = f"Завершено: {task_title} | Время: {duration:.3f} с | Статус: Ошибка (код {exit_code})"

                self.after(0, self.status_text.set, status_msg)
                self.after(0, self.write_log, f"\n[{status_msg}]\n")
            except Exception as exc:
                duration = time.perf_counter() - start_time
                self.after(0, self.write_log, f"\nОШИБКА ЗАПУСКА ({duration:.3f} с): {exc}\n")
                self.after(0, self.status_text.set, "Ошибка выполнения")
            finally:
                # в любом случае разблокируем кнопки обратно
                self.busy = False
                self.after(0, self.toggle_buttons, True)

        threading.Thread(target=worker, daemon=True).start()

    def show_report_summary(self, dst_file: str, duration: float):
        # основной скрипт создает рядом json-отчет, читаем его и выводим красивую сводку
        rep_file = dst_file + ".report.json"
        if os.path.exists(rep_file):
            try:
                with open(rep_file, "r", encoding="utf-8") as f:
                    report_data = json.load(f)
                self.write_log(
                    f"\n--- Сводка из отчёта ---\n"
                    f"Общее время работы: {duration:.3f} с (внутри скрипта: {report_data.get('seconds')} с)\n"
                    f"Обработано объектов: {report_data.get('objects')}\n"
                    f"Обезличено ФИО/Email: {report_data.get('persons')}, Base64-вложений: {report_data.get('base64')}\n"
                    f"Статистика модели сохранена: {'ДА' if report_data.get('statistics_equal') else 'НЕТ'}\n"
                )
            except Exception:
                pass

    def on_check_success(self, duration: float):
        self.write_log("Проверка пройдена: Успешно! Все требования соблюдены, ошибок не обнаружено.\n")

    def on_tests_success(self, duration: float):
        self.write_log("Автотесты пройдены: Успешно!\n")

    def run_anonymize(self):
        src = self.input_xml.get().strip()
        dst = self.output_xml.get().strip()
        secret = self.secret_key.get()
        vault = self.db_path.get().strip()
        lang = self.gen_lang.get()

        # базовые проверки перед запуском, чтобы скрипт не упал с трейсбеком
        if not src or not os.path.exists(src):
            messagebox.showwarning("Проверка параметров", "Укажите существующий исходный XML-файл.")
            return
        if not dst:
            messagebox.showwarning("Проверка параметров", "Укажите путь для сохранения результата XML.")
            return
        if not secret:
            messagebox.showwarning("Проверка параметров", "Введите секретное слово.")
            return
        if not vault:
            messagebox.showwarning("Проверка параметров", "Укажите путь к файлу хранилища (.sqlite).")
            return

        self.cleanup_stale_wal(vault)
        self.run_script(
            "anonymize.py",
            [src, dst, secret, vault, lang],
            "Анонимизация модели",
            on_success=lambda elapsed: self.show_report_summary(dst, elapsed)
        )

    def run_check(self):
        src = self.input_xml.get().strip()
        dst = self.output_xml.get().strip()
        if not os.path.exists(src) or not os.path.exists(dst):
            messagebox.showwarning("Проверка файлов", "Для проверки нужны оба файла: исходный и результат.")
            return
        self.run_script(
            "check.py",
            [src, dst],
            "Независимая проверка результата",
            on_success=self.on_check_success
        )

    def run_show_vault(self):
        vault = self.db_path.get().strip()
        if not os.path.exists(vault):
            messagebox.showwarning("Хранилище не найдено", f"Файл хранилища ещё не создан:\n{vault}")
            return
        self.run_script(os.path.join("tools", "show_vault.py"), [vault], "Просмотр хранилища")

    def run_tests(self):
        self.run_script(
            os.path.join("tools", "test_rules.py"),
            [],
            "Запуск автотестов",
            on_success=self.on_tests_success
        )


if __name__ == "__main__":
    gui = AnonymizerGUI()
    gui.mainloop()
