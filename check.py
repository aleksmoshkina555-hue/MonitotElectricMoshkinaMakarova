import os
import sys
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# запоминаем папку, где лежит сам скрипт, чтобы корректно искать остальные файлы
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))


class AnonymizerGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("CIM XML Anonymizer — Графический интерфейс")
        self.geometry("820x620")
        self.minsize(720, 520)

        # заводим переменные tkinter с дефолтными путями из папки practice
        self.src_path = tk.StringVar(value=os.path.join(ROOT_DIR, "practice", "input.xml"))
        self.dst_path = tk.StringVar(value=os.path.join(ROOT_DIR, "practice", "out_gui.xml"))
        self.vault_path = tk.StringVar(value=os.path.join(ROOT_DIR, "practice", "vault.sqlite"))
        self.secret_word = tk.StringVar(value="учебный-секрет")
        self.show_secret = tk.BooleanVar(value=False)

        self.build_interface()

    def build_interface(self):
        pad = {"padx": 10, "pady": 5}

        # --- Верхняя рамка с путями к файлам и секретом ---
        files_box = ttk.LabelFrame(self, text="Параметры выгрузки", padding=10)
        files_box.pack(fill="x", **pad)
        files_box.columnconfigure(1, weight=1)  # разрешаем центральному столбцу тянуться по ширине

        ttk.Label(files_box, text="Исходный XML:").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(files_box, textvariable=self.src_path).grid(row=0, column=1, sticky="ew", padx=6, pady=4)
        ttk.Button(files_box, text="Обзор...", command=self.pick_src).grid(row=0, column=2, pady=4)

        ttk.Label(files_box, text="Результат XML:").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(files_box, textvariable=self.dst_path).grid(row=1, column=1, sticky="ew", padx=6, pady=4)
        ttk.Button(files_box, text="Сохранить как...", command=self.pick_dst).grid(row=1, column=2, pady=4)

        ttk.Label(files_box, text="Хранилище (.sqlite):").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(files_box, textvariable=self.vault_path).grid(row=2, column=1, sticky="ew", padx=6, pady=4)
        ttk.Button(files_box, text="Выбрать...", command=self.pick_vault).grid(row=2, column=2, pady=4)

        # поле ввода секретного слова, по умолчанию скрываем символы звездочками
        ttk.Label(files_box, text="Секретное слово:").grid(row=3, column=0, sticky="w", pady=4)
        self.secret_input = ttk.Entry(files_box, textvariable=self.secret_word, show="*")
        self.secret_input.grid(row=3, column=1, sticky="ew", padx=6, pady=4)
        ttk.Checkbutton(
            files_box,
            text="Показать",
            variable=self.show_secret,
            command=self.toggle_secret
        ).grid(row=3, column=2, sticky="w", pady=4)

        # --- Кнопки основных действий (запуск и проверка) ---
        actions_box = ttk.Frame(self)
        actions_box.pack(fill="x", padx=10, pady=4)

        self.run_btn = ttk.Button(
            actions_box,
            text="Запустить анонимизацию",
            command=self.run_anonymize
        )
        self.run_btn.pack(side="left", padx=(0, 8), ipady=4, ipadx=10)

        self.check_btn = ttk.Button(
            actions_box,
            text="Проверить результат (check.py)",
            command=self.run_check
        )
        self.check_btn.pack(side="left", padx=(0, 8), ipady=4, ipadx=8)

        # --- Панель с вспомогательными утилитами ---
        tools_box = ttk.LabelFrame(self, text="Утилиты и диагностика", padding=8)
        tools_box.pack(fill="x", **pad)

        ttk.Button(
            tools_box,
            text="Просмотр хранилища (show_vault)",
            command=self.run_show_vault
        ).pack(side="left", padx=(0, 6))

        ttk.Button(
            tools_box,
            text="Разведка классов (explore)",
            command=self.run_explore
        ).pack(side="left", padx=(0, 6))

        ttk.Button(
            tools_box,
            text="Словарь имён (show_words)",
            command=self.run_show_words
        ).pack(side="left", padx=(0, 6))

        ttk.Button(
            tools_box,
            text="Автотесты (test_rules)",
            command=self.run_tests
        ).pack(side="left", padx=(0, 6))

        ttk.Button(
            tools_box,
            text="Очистить лог",
            command=self.clear_log
        ).pack(side="right")

        # --- Окно логов (оформил в темных тонах как консоль) ---
        log_box = ttk.LabelFrame(self, text="Журнал выполнения", padding=8)
        log_box.pack(fill="both", expand=True, **pad)

        self.console_out = tk.Text(
            log_box,
            wrap="word",
            font=("Consolas", 10),
            bg="#1e1e1e",
            fg="#e0e0e0",
            insertbackground="white",
            state="disabled"  # запрещаем редактирование руками
        )
        scroll = ttk.Scrollbar(log_box, command=self.console_out.yview)
        self.console_out.configure(yscrollcommand=scroll.set)

        self.console_out.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        # --- Нижняя строка статуса ---
        self.status_line = tk.StringVar(value="Готов к работе")
        status_lbl = ttk.Label(self, textvariable=self.status_line, relief="sunken", anchor="w", padding=(8, 2))
        status_lbl.pack(fill="x", side="bottom")

    def toggle_secret(self):
        # переключаем отображение пароля: текст или звездочки
        self.secret_input.configure(show="" if self.show_secret.get() else "*")

    def pick_src(self):
        chosen = filedialog.askopenfilename(
            title="Выберите исходный XML-файл",
            filetypes=[("XML файлы", "*.xml"), ("Все файлы", "*.*")]
        )
        if chosen:
            self.src_path.set(chosen)
            # сразу предлагаем имя выходного файла с суффиксом _anon
            name, ext = os.path.splitext(chosen)
            self.dst_path.set(f"{name}_anon{ext or '.xml'}")

    def pick_dst(self):
        chosen = filedialog.asksaveasfilename(
            title="Сохранить обезличенный XML как",
            defaultextension=".xml",
            filetypes=[("XML файлы", "*.xml"), ("Все файлы", "*.*")]
        )
        if chosen:
            self.dst_path.set(chosen)

    def pick_vault(self):
        chosen = filedialog.asksaveasfilename(
            title="Выберите или создайте файл хранилища SQLite",
            defaultextension=".sqlite",
            confirmoverwrite=False,  # разрешаем выбрать существующую базу без предупреждения о перезаписи
            filetypes=[("SQLite база", "*.sqlite *.db"), ("Все файлы", "*.*")]
        )
        if chosen:
            self.vault_path.set(chosen)

    def write_log(self, msg: str):
        # временно включаем виджет для записи, пишем текст, скроллим вниз и снова блокируем
        self.console_out.configure(state="normal")
        self.console_out.insert("end", msg)
        self.console_out.see("end")
        self.console_out.configure(state="disabled")

    def clear_log(self):
        self.console_out.configure(state="normal")
        self.console_out.delete("1.0", "end")
        self.console_out.configure(state="disabled")

    def run_script(self, rel_path: str, cli_args: list, task_name: str):
        full_path = os.path.join(ROOT_DIR, rel_path)
        if not os.path.exists(full_path):
            messagebox.showerror("Файл не найден", f"Не найден скрипт:\n{full_path}")
            return

        self.status_line.set(f"Выполняется: {task_name}...")
        self.write_log(f"\n{'=' * 60}\n{task_name}\n{'=' * 60}\n")

        # запускаем скрипт в отдельном потоке, чтобы окно не зависало на больших файлах
        def worker():
            proc_env = os.environ.copy()
            proc_env["PYTHONIOENCODING"] = "utf-8"  # фиксируем кодировку вывода, чтобы не было кракозябр
            cmd = [sys.executable, full_path] + cli_args
            try:
                proc = subprocess.Popen(
                    cmd,
                    cwd=ROOT_DIR,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,  # сливаем stderr в stdout, чтобы видеть все ошибки в логе
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=proc_env,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0  # на Windows прячем черное окно консоли
                )
                for line in proc.stdout:
                    # передаем строки в главный поток tkinter через after
                    self.after(0, self.write_log, line)
                exit_code = proc.wait()
                done_msg = f"Завершено ({task_name}) — код выхода: {exit_code}"
                self.after(0, self.status_line.set, done_msg)
                self.after(0, self.write_log, f"\n[Готово, код: {exit_code}]\n")
            except Exception as exc:
                self.after(0, self.write_log, f"\nОШИБКА ЗАПУСКА: {exc}\n")
                self.after(0, self.status_line.set, "Ошибка выполнения")

        threading.Thread(target=worker, daemon=True).start()

    def run_anonymize(self):
        src = self.src_path.get().strip()
        dst = self.dst_path.get().strip()
        secret = self.secret_word.get()
        vault = self.vault_path.get().strip()

        # проверяем, что все поля заполнены и входной файл реально существует
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

        self.run_script("anonymize.py", [src, dst, secret, vault], "Анонимизация модели")

    def run_check(self):
        src = self.src_path.get().strip()
        dst = self.dst_path.get().strip()
        if not os.path.exists(src) or not os.path.exists(dst):
            messagebox.showwarning("Проверка файлов", "Для проверки нужны оба файла: исходный и результат.")
            return
        self.run_script("check.py", [src, dst], "Независимая проверка результата")

    def run_show_vault(self):
        vault = self.vault_path.get().strip()
        if not os.path.exists(vault):
            messagebox.showwarning("Хранилище не найдено", f"Файл хранилища ещё не создан:\n{vault}")
            return
        self.run_script(os.path.join("tools", "show_vault.py"), [vault], "Просмотр хранилища")

    def run_explore(self):
        src = self.src_path.get().strip()
        if not os.path.exists(src):
            messagebox.showwarning("Файл не найден", "Выберите существующий исходный XML-файл.")
            return
        self.run_script(os.path.join("tools", "explore.py"), [src], "Разведка классов и полей")

    def run_show_words(self):
        src = self.src_path.get().strip()
        if not os.path.exists(src):
            messagebox.showwarning("Файл не найден", "Выберите существующий исходный XML-файл.")
            return
        self.run_script(os.path.join("tools", "show_words.py"), [src], "Просмотр словаря имён")

    def run_tests(self):
        self.run_script(os.path.join("tools", "test_rules.py"), [], "Запуск автотестов правил")


if __name__ == "__main__":
    gui_app = AnonymizerGUI()
    gui_app.mainloop()
